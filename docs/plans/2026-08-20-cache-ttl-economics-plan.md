# Cache TTL Economics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a script and a skill that decide, from measured Claude Code
session history, whether the prompt-cache TTL should be one hour or five minutes.

**Architecture:** One stdlib-only Python script in `plugins/retro/bin/`, which
imports its sibling `retro.py` to reuse its timestamp parser. It walks the transcript corpus,
deduplicates API requests globally by request id, reconstructs per-session request
chains, prices each request under the policy that actually ran and under the
five-minute counterfactual, and prints a verdict. A skill explains when to run it
and how to read it.

**Tech Stack:** Python 3 standard library only. No dependencies, no build step.
Tests use `unittest` from the standard library.

**Spec:** `docs/plans/2026-08-19-cache-ttl-economics-spec.md`

## Global Constraints

- **Stdlib only.** No third-party imports, no build step, no compiled artifacts.
- **Exit codes**, shared with every script in `plugins/*/bin`: `0` ran clean and
  flagged nothing, `1` ran clean and flagged something, `2` could not run.
- **No private data may reach the repository or the script's output.** No message
  text, no project-directory names, no absolute paths, no account name. Project
  identifiers are shown only as a stable hash. No sample run may appear in any
  committed file.
- **Guard every field access.** Transcript shape varies by CLI version; a
  `KeyError` partway through a 1.1 GB corpus loses the whole run.
- **Cache TTL constants:** five minutes is `300.0` seconds, one hour is `3600.0`.
- **Prices** are USD per token, keyed by the exact `message.model` string, and
  carry `PRICES_VERIFIED_ON = "2026-08-19"` with source
  `https://platform.claude.com/docs/en/about-claude/pricing`.
- **Commit messages** never go through a double-quoted shell string. Use
  `git commit -F -` fed by a single-quoted heredoc.

## Two decisions this plan settles that the spec left open

1. **Filename is `cache_ttl.py`, not `cache-ttl.py`.** The spec's hyphen was
   inherited from the shell scripts in `plugins/core/bin/`, which have no
   extension. The only Python precedent in this repo is `retro.py`. An underscore
   also lets the test file import the module directly instead of going through
   `importlib.util.spec_from_file_location`.
2. **Project labels are always hashed, never shown even redacted.** The spec said
   a `--project` match could display its label redacted. `retro.redact()` strips
   the home path and account name but passes other path segments through
   verbatim, and those segments name other projects. Matching happens against the
   raw name internally; the hash is what appears in both output modes. This is
   strictly tighter than the spec and cannot leak.

   Review found the first draft defined `project_label`, tested it twice, and
   never called it — while the help text and the skill both promised hashed
   labels in the output. The guard test passed only because the payload had no
   project field at all, so it could not fail for the reason it named. The
   label is now emitted in both modes and the test asserts its presence.

## Parallelization

Two lanes, file-disjoint, each in its own worktree branched from this branch:

- **Lane A — the script.** Tasks 1 → 2 → 3 → 4 → 5 → 7, strictly serial. They all
  write `plugins/retro/bin/cache_ttl.py` and its test file, and each task consumes
  the function the previous one defined. This is a genuine data dependency, not an
  unexamined default.
- **Lane B — the skill and manifests.** Task 6 only. Touches
  `plugins/retro/skills/deciding-the-prompt-cache-ttl/SKILL.md`,
  `plugins/retro/.claude-plugin/plugin.json`, and `.claude-plugin/marketplace.json`
  — none of which Lane A touches. Runs concurrently with the whole of Lane A.

Lane B merges back whenever it is reviewed; Lane A merges after Task 7.

The source files are disjoint, but the **plan file itself is not**: both lanes
would tick `- [ ]` boxes in it. Lane A owns `docs/plans/2026-08-20-cache-ttl-economics-plan.md`
and is the only lane that edits it; Lane B reports Task 6 completion back rather
than ticking its own box. Task 6 also documents a command line that Task 7 could
still change, so Lane A re-reads the skill's invocation block before merging.

## File Structure

| File | Responsibility |
| --- | --- |
| `plugins/retro/bin/cache_ttl.py` | Create. The whole tool: corpus walk, dedup, gap chains, cost model, report, CLI. |
| `plugins/retro/tests/fixtures.py` | Create. Builds a synthetic transcript corpus in a temp directory. |
| `plugins/retro/tests/test_cache_ttl.py` | Create. Unit tests for every rule the spec calls non-optional. |
| `plugins/retro/skills/deciding-the-prompt-cache-ttl/SKILL.md` | Create. When to run it, how to read it, the mechanics that make it correct. |
| `plugins/retro/.claude-plugin/plugin.json` | Modify. Version bump, widened description and keywords. |
| `.claude-plugin/marketplace.json` | Modify. Same three fields, kept in step. |

Running the tests, from the repository root:

```
python3 -m unittest discover -s plugins/retro/tests -t plugins/retro/tests -v
```

---

## Task 1: Synthetic corpus fixtures

**Files:**
- Create: `plugins/retro/tests/fixtures.py`
- Test: `plugins/retro/tests/test_cache_ttl.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `build_corpus(root, sessions)` writing a transcript tree, and
  `usage_row(rid, model, read, w1, w5, out, when)` returning one JSONL record as a
  dict. `sessions` is a list of dicts with keys `project` (str), `session` (str),
  `subagent` (bool, default False), `workflow` (str or absent — nests the
  transcript under `subagents/workflows/<id>/`, the layout most real subagent
  transcripts use), and `rows` (list of dicts from `usage_row`).

- [ ] **Step 1: Write the failing test**

Create `plugins/retro/tests/test_cache_ttl.py`:

```python
"""Tests for cache_ttl. Stdlib unittest; no third-party runner."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plugins" / "retro" / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures  # noqa: E402

T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestFixtures(unittest.TestCase):
    def test_build_corpus_writes_main_and_subagent_transcripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "proj-a", "session": "s1", "rows": [
                    fixtures.usage_row("r1", "claude-opus-5", 100, 10, 0, 5, T0),
                ]},
                {"project": "proj-a", "session": "s1", "subagent": True, "rows": [
                    fixtures.usage_row("r2", "claude-sonnet-5", 200, 0, 20, 5, T0),
                ]},
            ])
            main = root / "proj-a" / "s1.jsonl"
            sub = root / "proj-a" / "s1" / "subagents" / "agent-0.jsonl"
            self.assertTrue(main.is_file())
            self.assertTrue(sub.is_file())
            rec = json.loads(main.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(rec["requestId"], "r1")
            self.assertEqual(rec["message"]["usage"]["cache_read_input_tokens"], 100)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s plugins/retro/tests -t plugins/retro/tests -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fixtures'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/retro/tests/fixtures.py`:

```python
"""Build a synthetic Claude Code transcript corpus for tests.

Mirrors the real layout: main-thread transcripts at
<root>/<project>/<session>.jsonl, subagent transcripts one level deeper at
<root>/<project>/<session>/subagents/agent-<n>.jsonl.
"""

import json
from collections import Counter


def usage_row(rid, model, read, w1, w5, out, when):
    """One assistant JSONL record carrying a usage block."""
    return {
        "type": "assistant",
        "requestId": rid,
        "timestamp": when.isoformat().replace("+00:00", "Z"),
        "message": {
            "model": model,
            "usage": {
                "input_tokens": 0,
                "cache_read_input_tokens": read,
                "output_tokens": out,
                "cache_creation": {
                    "ephemeral_1h_input_tokens": w1,
                    "ephemeral_5m_input_tokens": w5,
                },
            },
        },
    }


def build_corpus(root, sessions):
    """Write `sessions` to disk under `root`. Returns the root Path."""
    counters = Counter()
    for spec in sessions:
        project = root / spec["project"]
        name = spec["session"]
        if spec.get("subagent"):
            directory = project / name / "subagents"
            if spec.get("workflow"):
                # 909 of ~1,540 real subagent transcripts sit two levels
                # deeper than a plain subagent, under a workflow directory.
                # A fixture that cannot build that layout cannot pin the
                # classifier against the shape most of the corpus has.
                directory = directory / "workflows" / spec["workflow"]
            index = counters[(spec["project"], name)]
            counters[(spec["project"], name)] += 1
            path = directory / ("agent-%d.jsonl" % index)
        else:
            directory = project
            path = directory / (name + ".jsonl")
        directory.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            for row in spec["rows"]:
                handle.write(json.dumps(row) + "\n")
    return root
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s plugins/retro/tests -t plugins/retro/tests -v`
Expected: PASS, 1 test

- [ ] **Step 5: Commit**

```bash
git add plugins/retro/tests/fixtures.py plugins/retro/tests/test_cache_ttl.py
git commit -F - <<'MSG'
test: synthetic transcript corpus for cache TTL tests

Mirrors the real layout, main-thread transcripts at one level and subagent
transcripts one level deeper, so the main/subagent split can be tested
without touching real session history.
MSG
```

---

## Task 2: Corpus walk and global deduplication

**Files:**
- Create: `plugins/retro/bin/cache_ttl.py`
- Modify: `plugins/retro/tests/test_cache_ttl.py`

**Interfaces:**
- Consumes: `fixtures.build_corpus`, `fixtures.usage_row` from Task 1.
- Produces:
  - `is_main_thread(path, projects_dir) -> bool`
  - `collect(projects_dir) -> (requests, skipped)` where `requests` is
    `{rid: record}` and `skipped` is a `Counter`. A record is a dict with keys
    `rid`, `model`, `read`, `w1`, `w5`, `out`, `tokens`, `start` (an aware
    `datetime`), `source` (Path of the owning transcript), `main` (bool).

- [ ] **Step 1: Write the failing test**

Append to `plugins/retro/tests/test_cache_ttl.py`, above the `__main__` block:

```python
import cache_ttl  # noqa: E402


class TestCollect(unittest.TestCase):
    def test_classifies_main_thread_and_subagent_by_depth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("main1", "claude-opus-5", 10, 1, 0, 1, T0)]},
                {"project": "p", "session": "s", "subagent": True, "rows": [
                    fixtures.usage_row("sub1", "claude-sonnet-5", 20, 0, 2, 1, T0)]},
            ])
            requests, _ = cache_ttl.collect(root)
            self.assertTrue(requests["main1"]["main"])
            self.assertFalse(requests["sub1"]["main"])

    def test_deduplicates_the_same_request_id_across_two_files(self):
        """A resumed session copies rows into a new transcript. Counting each
        file separately would double-count the request."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = fixtures.usage_row("dup", "claude-opus-5", 1000, 50, 0, 5, T0)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "first", "rows": [row]},
                {"project": "p", "session": "second", "rows": [row]},
            ])
            requests, _ = cache_ttl.collect(root)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests["dup"]["read"], 1000)

    def test_settled_row_wins_when_rows_of_one_request_disagree(self):
        """Streaming writes a zeroed placeholder beside the full row. Taking
        the first row would zero out a real request."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("r", "claude-opus-5", 0, 26, 0, 0, T0),
                    fixtures.usage_row("r", "claude-opus-5", 992810, 26, 0, 40,
                                       T0 + timedelta(seconds=30)),
                ]},
            ])
            requests, _ = cache_ttl.collect(root)
            self.assertEqual(requests["r"]["read"], 992810)

    def test_request_start_is_the_earliest_of_its_rows(self):
        """Rows of one request span up to several minutes; the gap measures
        from when the request started."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            late = T0 + timedelta(seconds=353)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("r", "claude-opus-5", 0, 1, 0, 0, T0),
                    fixtures.usage_row("r", "claude-opus-5", 500, 1, 0, 9, late),
                ]},
            ])
            requests, _ = cache_ttl.collect(root)
            self.assertEqual(requests["r"]["start"], T0)

    def test_rows_without_a_timestamp_are_skipped_and_tallied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = fixtures.usage_row("r", "claude-opus-5", 1, 1, 0, 1, T0)
            del bad["timestamp"]
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [bad]}])
            requests, skipped = cache_ttl.collect(root)
            self.assertEqual(len(requests), 0)
            self.assertEqual(skipped["no_timestamp"], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s plugins/retro/tests -t plugins/retro/tests -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cache_ttl'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/retro/bin/cache_ttl.py`:

```python
#!/usr/bin/env python3
"""cache_ttl -- decide the prompt-cache TTL from measured session history.

Answers one question: should Claude Code's prompt cache use the one-hour TTL
or the five-minute one? It replays the request timeline of every session,
prices it under the policy that actually ran and under the counterfactual,
and prints the difference.

Counts and prices only. No message text leaves this script, and project
identifiers are reduced to a stable hash before anything is printed.

Stdlib only. Every field access is guarded: transcript shape varies by CLI
version, and a KeyError partway through a 1GB corpus loses the whole run.

Exit codes match the sibling scripts in plugins/core/bin:
    0  ran clean, the TTL in force is the right one
    1  ran clean, the TTL should change
    2  could not run (no projects directory, no readable transcripts)
"""

EXIT_CLEAN, EXIT_FLAGGED, EXIT_CANNOT_RUN = 0, 1, 2

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

# retro.py is a sibling in this directory. It is import-safe: every statement
# at module level is an assignment, and its writes live inside functions.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import retro  # noqa: E402

HOME = Path.home()
PROJECTS_DIR = HOME / ".claude" / "projects"

FIVE_MINUTES = 300.0
ONE_HOUR = 3600.0


def is_main_thread(path, projects_dir):
    """Main-thread transcripts sit at <projects>/<project>/<session>.jsonl.

    Subagent transcripts sit deeper, under <session>/subagents/. The split is
    positional; isSidechain confirms it but never contradicts it, because the
    flag is absent from every top-level file.
    """
    try:
        return path.parent.parent == projects_dir
    except Exception:
        return False


def _rows(path, main, skipped):
    """Yield one record per assistant row carrying a usage block."""
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        skipped["unreadable_file"] += 1
        return
    with handle:
        for line in handle:
            if '"usage"' not in line:
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                skipped["bad_json"] += 1
                continue
            if not isinstance(raw, dict) or raw.get("type") != "assistant":
                continue
            message = raw.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            rid = raw.get("requestId")
            if not rid:
                # Some rows carry only message.id. Tallied, because if a future
                # CLI writes some rows of one request with a requestId and some
                # without, the id-less rows become a phantom second request.
                rid = message.get("id")
                if rid:
                    skipped["request_id_fallback"] += 1
            if not rid:
                skipped["no_request_id"] += 1
                continue
            start = retro.parse_ts(raw.get("timestamp"))
            if start is None:
                skipped["no_timestamp"] += 1
                continue
            if start.tzinfo is None:
                # A timestamp without Z or an offset compares as naive and
                # raises against every aware one, aborting the whole run.
                skipped["naive_timestamp"] += 1
                continue
            creation = usage.get("cache_creation")
            if not isinstance(creation, dict):
                creation = {}
            read = usage.get("cache_read_input_tokens") or 0
            w1 = creation.get("ephemeral_1h_input_tokens") or 0
            w5 = creation.get("ephemeral_5m_input_tokens") or 0
            out = usage.get("output_tokens") or 0
            model = message.get("model")
            if not isinstance(model, str):
                # A non-string model makes sorted() on the unpriced bucket
                # raise at the very end of an otherwise complete run.
                model = "<unknown>"
            yield {
                "rid": rid,
                "model": model,
                "read": read,
                "w1": w1,
                "w5": w5,
                "out": out,
                "tokens": read + w1 + w5 + out,
                "start": start,
                "source": path,
                "main": main,
            }


def collect(projects_dir):
    """Walk every transcript and return globally deduplicated requests.

    Deduplication is global rather than per file. Resuming or forking a
    session copies recent rows, request id and usage intact, into the new
    transcript; per-file counting double-counts those requests.

    Where rows of one request disagree, the settled row wins -- the one with
    the largest total token count. A request's start is the earliest
    timestamp across its rows, and its owning transcript is the first one it
    appeared in.
    """
    requests = {}
    skipped = Counter()
    for path in sorted(projects_dir.rglob("*.jsonl")):
        main = is_main_thread(path, projects_dir)
        for record in _rows(path, main, skipped):
            rid = record["rid"]
            previous = requests.get(rid)
            if previous is None:
                requests[rid] = record
                continue
            earliest = min(previous["start"], record["start"])
            if record["tokens"] > previous["tokens"]:
                record["start"] = earliest
                record["source"] = previous["source"]
                record["main"] = previous["main"]
                requests[rid] = record
            else:
                previous["start"] = earliest
    return requests, skipped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s plugins/retro/tests -t plugins/retro/tests -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add plugins/retro/bin/cache_ttl.py plugins/retro/tests/test_cache_ttl.py
git commit -F - <<'MSG'
feat: corpus walk and global request deduplication for cache TTL

Deduplicates by request id across the whole corpus rather than per file,
because resuming or forking a session copies rows into a new transcript and
per-file counting double-counts roughly seven percent of read tokens. Where
rows of one request disagree the settled row wins, and a request is stamped
with the earliest of its rows.
MSG
```

---

## Task 3: Session chains and gap bands

**Files:**
- Modify: `plugins/retro/bin/cache_ttl.py`
- Modify: `plugins/retro/tests/test_cache_ttl.py`

**Interfaces:**
- Consumes: `collect` from Task 2.
- Produces:
  - `chains(requests, main_only=True) -> {source: [record, ...]}` sorted by start.
  - `gap_seconds(chain) -> [(record, gap_or_None), ...]`, `None` for the opener.
  - `band_table(paired) -> {band_name: {"n", "zero_read", "read", "write"}}`
    taking the flat `[(record, gap), ...]` list that `gap_seconds` returns,
    not a chains dict
    over bands `0-1m`, `1-5m`, `5-10m`, `10-15m`, `15-60m`, `>60m`.

- [ ] **Step 1: Write the failing test**

Append to `plugins/retro/tests/test_cache_ttl.py`, above the `__main__` block:

```python
class TestChains(unittest.TestCase):
    def _corpus(self, root, offsets):
        rows = [fixtures.usage_row("r%d" % i, "claude-opus-5", 100, 5, 0, 1,
                                   T0 + timedelta(seconds=off))
                for i, off in enumerate(offsets)]
        fixtures.build_corpus(root, [
            {"project": "p", "session": "s", "rows": rows}])

    def test_chain_is_ordered_by_start_not_file_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("late", "claude-opus-5", 1, 1, 0, 1,
                                       T0 + timedelta(seconds=60)),
                    fixtures.usage_row("early", "claude-opus-5", 1, 1, 0, 1, T0),
                ]}])
            requests, _ = cache_ttl.collect(root)
            chain = list(cache_ttl.chains(requests).values())[0]
            self.assertEqual([r["rid"] for r in chain], ["early", "late"])

    def test_opener_has_no_gap_and_others_measure_from_previous_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._corpus(root, [0, 30, 630])
            requests, _ = cache_ttl.collect(root)
            chain = list(cache_ttl.chains(requests).values())[0]
            gaps = [gap for _, gap in cache_ttl.gap_seconds(chain)]
            self.assertIsNone(gaps[0])
            self.assertEqual(gaps[1], 30.0)
            self.assertEqual(gaps[2], 600.0)

    def test_subagent_requests_are_excluded_from_main_chains(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("m", "claude-opus-5", 1, 1, 0, 1, T0)]},
                {"project": "p", "session": "s", "subagent": True, "rows": [
                    fixtures.usage_row("s1", "claude-sonnet-5", 1, 0, 1, 1, T0)]},
            ])
            requests, _ = cache_ttl.collect(root)
            main = cache_ttl.chains(requests, main_only=True)
            subs = cache_ttl.chains(requests, main_only=False)
            self.assertEqual(sum(len(c) for c in main.values()), 1)
            self.assertEqual(sum(len(c) for c in subs.values()), 1)

    def test_band_table_puts_each_gap_in_the_right_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # gaps after the opener: 30s, 120s, 400s, 700s, 1800s, 7200s
            self._corpus(root, [0, 30, 150, 550, 1250, 3050, 10250])
            requests, _ = cache_ttl.collect(root)
            chain = list(cache_ttl.chains(requests).values())[0]
            table = cache_ttl.band_table(cache_ttl.gap_seconds(chain))
            self.assertEqual(table["0-1m"]["n"], 1)
            self.assertEqual(table["1-5m"]["n"], 1)
            self.assertEqual(table["5-10m"]["n"], 1)
            self.assertEqual(table["10-15m"]["n"], 1)
            self.assertEqual(table["15-60m"]["n"], 1)
            self.assertEqual(table[">60m"]["n"], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s plugins/retro/tests -t plugins/retro/tests -v`
Expected: FAIL with `AttributeError: module 'cache_ttl' has no attribute 'chains'`

- [ ] **Step 3: Write minimal implementation**

Append to `plugins/retro/bin/cache_ttl.py`:

```python
BANDS = (
    (0.0, 60.0, "0-1m"),
    (60.0, 300.0, "1-5m"),
    (300.0, 600.0, "5-10m"),
    (600.0, 900.0, "10-15m"),
    (900.0, 3600.0, "15-60m"),
    (3600.0, float("inf"), ">60m"),
)


def chains(requests, main_only=True):
    """Group requests into per-transcript chains ordered by start time.

    Gaps are measured within one transcript. Grouping by project directory
    instead moves the decisive band's zero-read share by an order of
    magnitude, so the choice is explicit: the conversation history dominates
    the cached prefix and is specific to one session.
    """
    grouped = {}
    for record in requests.values():
        if bool(record["main"]) != bool(main_only):
            continue
        grouped.setdefault(record["source"], []).append(record)
    for rows in grouped.values():
        rows.sort(key=lambda item: item["start"])
    return grouped


def gap_seconds(chain):
    """Pair each record with the seconds since the previous request started.

    The first request of a chain has no previous request and yields None.
    Rows are ordered by timestamp rather than file position, because a few
    rows appear out of order within their own transcript and trusting file
    order yields negative gaps.
    """
    paired = []
    for index, record in enumerate(chain):
        if index == 0:
            paired.append((record, None))
            continue
        delta = (record["start"] - chain[index - 1]["start"]).total_seconds()
        paired.append((record, max(0.0, delta)))
    return paired


def band_of(seconds):
    for low, high, name in BANDS:
        if low <= seconds < high:
            return name
    return ">60m"


def band_table(paired):
    """Summarise gap bands. Openers carry no gap and are not banded."""
    table = {name: {"n": 0, "zero_read": 0, "read": 0, "write": 0}
             for _, _, name in BANDS}
    for record, gap in paired:
        if gap is None:
            continue
        bucket = table[band_of(gap)]
        bucket["n"] += 1
        bucket["read"] += record["read"]
        bucket["write"] += record["w1"] + record["w5"]
        if record["read"] == 0:
            bucket["zero_read"] += 1
    return table
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s plugins/retro/tests -t plugins/retro/tests -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add plugins/retro/bin/cache_ttl.py plugins/retro/tests/test_cache_ttl.py
git commit -F - <<'MSG'
feat: session chains and gap bands for cache TTL

Gaps are measured between consecutive requests within one transcript,
ordered by timestamp rather than file position, because a cache read
refreshes the time to live and a few rows appear out of order within their
own file.
MSG
```

---

## Task 4: Prices and the cost model

**Files:**
- Modify: `plugins/retro/bin/cache_ttl.py`
- Modify: `plugins/retro/tests/test_cache_ttl.py`

**Interfaces:**
- Consumes: `chains`, `gap_seconds` from Task 3.
- Produces:
  - `PRICES`, `PRICES_VERIFIED_ON`, `PRICES_SOURCE` module constants.
  - `evaluate(chained) -> dict` with keys `observed`, `counterfactual`, `ratio`,
    `delta`, `openers`, `decisive_read`, `neutral_read`, `bands` (a `Counter` over
    `0-5m` / `5-60m` / `>60m`), and `unpriced` (a `Counter` keyed by model id).

- [ ] **Step 1: Write the failing test**

Append to `plugins/retro/tests/test_cache_ttl.py`, above the `__main__` block:

```python
class TestCostModel(unittest.TestCase):
    def _evaluate(self, offsets, read=1000, w1=100, w5=0,
                  model="claude-opus-5"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [fixtures.usage_row("r%d" % i, model, read, w1, w5, 1,
                                       T0 + timedelta(seconds=off))
                    for i, off in enumerate(offsets)]
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": rows}])
            requests, _ = cache_ttl.collect(root)
            return cache_ttl.evaluate(cache_ttl.chains(requests))

    def test_gap_under_five_minutes_costs_the_same_read_under_both_policies(self):
        result = self._evaluate([0, 60])
        # Second request: 1h policy pays 100 tokens at the 1h write rate plus
        # 1000 read; 5m policy pays the same tokens at the 5m write rate.
        expected_observed = (100 * 10.00e-6 + 1000 * 0.50e-6) * 2
        expected_counter = (100 * 6.25e-6 + 1000 * 0.50e-6) * 2
        self.assertAlmostEqual(result["observed"], expected_observed, places=9)
        self.assertAlmostEqual(result["counterfactual"], expected_counter,
                               places=9)
        self.assertLess(result["ratio"], 1.0)

    def test_gap_past_five_minutes_rewrites_the_prefix_under_the_counterfactual(self):
        result = self._evaluate([0, 600])
        opener_obs = 100 * 10.00e-6 + 1000 * 0.50e-6
        opener_cf = 100 * 6.25e-6 + 1000 * 0.50e-6
        # The missing request rewrites read + writes at the 5m rate, no read.
        miss_cf = (1000 + 100) * 6.25e-6
        self.assertAlmostEqual(result["counterfactual"], opener_cf + miss_cf,
                               places=9)
        self.assertAlmostEqual(result["observed"], opener_obs * 2, places=9)
        self.assertGreater(result["ratio"], 1.0)
        self.assertEqual(result["bands"]["5-60m"], 1)

    def test_session_opener_takes_the_unchanged_branch(self):
        result = self._evaluate([0])
        self.assertEqual(result["openers"], 1)
        self.assertEqual(sum(result["bands"].values()), 0)

    def test_decisive_and_neutral_read_tokens_are_separated(self):
        result = self._evaluate([0, 60, 600])
        self.assertEqual(result["neutral_read"], 1000)
        self.assertEqual(result["decisive_read"], 1000)

    def test_unknown_model_is_reported_not_priced_at_a_default(self):
        result = self._evaluate([0, 60], model="claude-unknown-9")
        self.assertEqual(result["observed"], 0.0)
        self.assertEqual(result["unpriced"]["claude-unknown-9"], 2)

    def test_price_rows_hold_the_exact_published_multipliers(self):
        """Every row is base x1.25 (5m), x2.0 (1h), x0.1 (read). Expressed
        against the 5m write so no base column is needed: 1h is 1.6x the 5m
        write and a read is 0.08x it. An ordering-only assertion would let a
        tenfold typo through."""
        for model, (w5, w1, read) in cache_ttl.PRICES.items():
            self.assertAlmostEqual(w1, w5 * 1.6, places=12, msg=model)
            self.assertAlmostEqual(read, w5 * 0.08, places=12, msg=model)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s plugins/retro/tests -t plugins/retro/tests -v`
Expected: FAIL with `AttributeError: module 'cache_ttl' has no attribute 'evaluate'`

- [ ] **Step 3: Write minimal implementation**

Append to `plugins/retro/bin/cache_ttl.py`:

```python
# USD per token, keyed by the exact message.model string in transcripts.
# Read from the model pricing table on the page below. Re-check the date
# before trusting a dollar figure: prices change and this table does not.
PRICES_VERIFIED_ON = "2026-08-19"
PRICES_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICES = {
    # model id:                    (write_5m,  write_1h,  read)
    "claude-fable-5":              (12.50e-6, 20.00e-6, 1.00e-6),
    "claude-opus-5":               (6.25e-6, 10.00e-6, 0.50e-6),
    "claude-opus-4-8":             (6.25e-6, 10.00e-6, 0.50e-6),
    "claude-opus-4-7":             (6.25e-6, 10.00e-6, 0.50e-6),
    "claude-sonnet-5":             (2.50e-6, 4.00e-6, 0.20e-6),
    "claude-sonnet-4-6":           (3.75e-6, 6.00e-6, 0.30e-6),
    "claude-sonnet-4-5-20250929":  (3.75e-6, 6.00e-6, 0.30e-6),
    "claude-haiku-4-5-20251001":   (1.25e-6, 2.00e-6, 0.10e-6),
}


def evaluate(chained):
    """Price every request under the policy that ran and the counterfactual.

    Observed policy, per request:
        w1 * price_1h + w5 * price_5m + read * price_read

    Counterfactual, forcing the five-minute TTL: every write becomes a
    five-minute write, and whether the request still hits depends on the gap.
    Within five minutes it hits and the read stands. Past five minutes the
    prefix is gone, so the tokens it would have read are rewritten instead --
    the rewrite subsumes both the read and the increment, which is why the
    miss branch has no read term.

    Session openers take the unchanged branch. A request whose model has no
    price is counted into `unpriced` and contributes no cost, but stays in
    the chain so it cannot invent a longer gap for its successor.
    """
    result = {
        "observed": 0.0,
        "counterfactual": 0.0,
        "openers": 0,
        "decisive_read": 0,
        "neutral_read": 0,
        "bands": Counter(),
        "unpriced": Counter(),
    }
    for chain in chained.values():
        for record, gap in gap_seconds(chain):
            price = PRICES.get(record["model"])
            if price is None:
                result["unpriced"][record["model"]] += 1
                continue
            write_5m, write_1h, read_price = price
            read, w1, w5 = record["read"], record["w1"], record["w5"]
            result["observed"] += w1 * write_1h + w5 * write_5m + read * read_price
            hit_cost = (w1 + w5) * write_5m + read * read_price
            if gap is None:
                result["openers"] += 1
                result["counterfactual"] += hit_cost
            elif gap <= FIVE_MINUTES:
                result["counterfactual"] += hit_cost
                result["neutral_read"] += read
                result["bands"]["0-5m"] += 1
            else:
                result["counterfactual"] += (read + w1 + w5) * write_5m
                if gap <= ONE_HOUR:
                    result["bands"]["5-60m"] += 1
                    result["decisive_read"] += read
                else:
                    result["bands"][">60m"] += 1
    result["delta"] = result["counterfactual"] - result["observed"]
    result["ratio"] = (result["counterfactual"] / result["observed"]
                       if result["observed"] else 0.0)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s plugins/retro/tests -t plugins/retro/tests -v`
Expected: PASS, 16 tests

- [ ] **Step 5: Commit**

```bash
git add plugins/retro/bin/cache_ttl.py plugins/retro/tests/test_cache_ttl.py
git commit -F - <<'MSG'
feat: price table and cost model for cache TTL

Prices every request under the one-hour policy that ran and under the
five-minute counterfactual. Past five minutes the prefix is gone, so the
tokens that would have been read are rewritten instead, and the rewrite
subsumes both the read and the increment.

Unknown model ids are reported rather than priced at a default, and stay in
the chain so they cannot invent a longer gap for the request after them.
MSG
```

---

## Task 5: Report, privacy, and the command line

**Files:**
- Modify: `plugins/retro/bin/cache_ttl.py`
- Modify: `plugins/retro/tests/test_cache_ttl.py`

**Interfaces:**
- Consumes: everything from Tasks 2 to 4.
- Produces:
  - `project_label(path, projects_dir) -> str`, always a hash, never a name.
  - `within_window(requests, days, now) -> dict` filtering by start time.
  - `report(projects_dir, days, project, as_json, stream) -> int` returning an
    exit code.
  - `main(argv=None) -> int`.

- [ ] **Step 1: Write the failing test**

Append to `plugins/retro/tests/test_cache_ttl.py`, above the `__main__` block:

```python
import io  # noqa: E402


class TestBoundariesAndBranches(unittest.TestCase):
    """The comparisons the verdict turns on. Mutation testing showed the suite
    stayed green when either boundary was moved, so each is pinned from both
    sides at its exact value."""

    def _at_gap(self, seconds):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("a", "claude-opus-5", 1000, 100, 0, 1, T0),
                    fixtures.usage_row("b", "claude-opus-5", 1000, 100, 0, 1,
                                       T0 + timedelta(seconds=seconds))]}])
            requests, _ = cache_ttl.collect(root)
            return cache_ttl.evaluate(cache_ttl.chains(requests))

    def test_gap_of_exactly_five_minutes_still_counts_as_a_hit(self):
        result = self._at_gap(300)
        self.assertEqual(result["bands"]["0-5m"], 1)
        self.assertEqual(result["neutral_read"], 1000)
        self.assertEqual(result["decisive_read"], 0)

    def test_one_second_past_five_minutes_counts_as_a_miss(self):
        result = self._at_gap(301)
        self.assertEqual(result["bands"]["5-60m"], 1)
        self.assertEqual(result["decisive_read"], 1000)
        self.assertEqual(result["neutral_read"], 0)

    def test_gap_of_exactly_one_hour_is_still_the_decisive_band(self):
        result = self._at_gap(3600)
        self.assertEqual(result["bands"]["5-60m"], 1)
        self.assertEqual(result["bands"][">60m"], 0)

    def test_one_second_past_an_hour_leaves_the_decisive_band(self):
        result = self._at_gap(3601)
        self.assertEqual(result["bands"][">60m"], 1)
        self.assertEqual(result["bands"]["5-60m"], 0)
        self.assertEqual(result["decisive_read"], 0)

    def test_delta_is_counterfactual_minus_observed(self):
        result = self._at_gap(600)
        self.assertAlmostEqual(result["delta"],
                               result["counterfactual"] - result["observed"],
                               places=12)
        self.assertGreater(result["delta"], 0.0)

    def test_malformed_json_rows_are_tallied_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("a", "claude-opus-5", 1, 1, 0, 1, T0)]}])
            with open(root / "p" / "s.jsonl", "a", encoding="utf-8") as handle:
                handle.write('{"type":"assistant","usage": BROKEN\n')
            _, skipped = cache_ttl.collect(root)
            self.assertEqual(skipped["bad_json"], 1)

    def test_a_timestamp_without_a_zone_is_skipped_rather_than_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            naive = fixtures.usage_row("n", "claude-opus-5", 1, 1, 0, 1, T0)
            naive["timestamp"] = "2026-08-01T12:00:00"
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    naive,
                    fixtures.usage_row("a", "claude-opus-5", 1, 1, 0, 1, T0)]}])
            requests, skipped = cache_ttl.collect(root)
            self.assertEqual(skipped["naive_timestamp"], 1)
            self.assertEqual(list(requests), ["a"])

    def test_unpriced_main_thread_yields_no_verdict_rather_than_a_false_one(self):
        """observed reaches zero when every main model is unpriced, and a zero
        denominator rendered as a confident 'switch the TTL'."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("x", "claude-zzz-unknown", 5, 1, 0, 1, T0),
                    fixtures.usage_row("y", "claude-zzz-unknown", 5, 1, 0, 1,
                                       T0 + timedelta(seconds=30))]}])
            stream = io.StringIO()
            code = cache_ttl.report(root, None, None, False, stream)
            self.assertEqual(code, cache_ttl.EXIT_CLEAN)
            self.assertIn("nothing to decide", stream.getvalue())
            self.assertNotIn("FORCE_PROMPT_CACHING_5M", stream.getvalue())

    def test_a_subagent_only_unknown_model_still_reaches_the_unpriced_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("m1", "claude-opus-5", 100, 5, 0, 1, T0),
                    fixtures.usage_row("m2", "claude-opus-5", 100, 5, 0, 1,
                                       T0 + timedelta(seconds=30))]},
                {"project": "p", "session": "s", "subagent": True, "rows": [
                    fixtures.usage_row("s1", "claude-mystery-7", 900, 0, 9, 1,
                                       T0)]},
            ])
            stream = io.StringIO()
            cache_ttl.report(root, None, None, True, stream)
            body = json.loads(stream.getvalue())
            self.assertIn("claude-mystery-7", body["unpriced_requests"])
            self.assertGreater(body["unpriced_tokens"]["claude-mystery-7"], 0)

    def test_ttl_in_force_is_read_from_the_write_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("a", "claude-opus-5", 10, 0, 900, 1, T0),
                    fixtures.usage_row("b", "claude-opus-5", 10, 0, 900, 1,
                                       T0 + timedelta(seconds=30))]}])
            stream = io.StringIO()
            cache_ttl.report(root, None, None, True, stream)
            self.assertEqual(json.loads(stream.getvalue())["ttl_in_force"],
                             "five minutes")

    def test_validation_table_is_fed_from_subagent_chains(self):
        """The skill calls this table the validation. Fed from main chains it
        would validate nothing, and no exit code would move."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("m1", "claude-opus-5", 100, 5, 0, 1, T0),
                    fixtures.usage_row("m2", "claude-opus-5", 100, 5, 0, 1,
                                       T0 + timedelta(seconds=30))]},
                {"project": "p", "session": "s", "subagent": True, "rows": [
                    fixtures.usage_row("s1", "claude-sonnet-5", 50, 0, 5, 1, T0),
                    fixtures.usage_row("s2", "claude-sonnet-5", 50, 0, 5, 1,
                                       T0 + timedelta(seconds=7200))]},
            ])
            stream = io.StringIO()
            cache_ttl.report(root, None, None, False, stream)
            text = stream.getvalue()
            validation = text.split("validation:")[1].split("main-thread gap bands")[0]
            self.assertIn(">60m", validation)
            self.assertNotIn("0-1m", validation)

    def test_workflow_nested_subagents_are_still_classified_as_subagents(self):
        """Most real subagent transcripts sit under subagents/workflows/<id>/,
        two levels deeper than the shallow case the other tests build."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("m", "claude-opus-5", 1, 1, 0, 1, T0)]},
                {"project": "p", "session": "s", "subagent": True,
                 "workflow": "wf_abc123", "rows": [
                     fixtures.usage_row("w", "claude-sonnet-5", 9, 0, 1, 1, T0)]},
            ])
            requests, _ = cache_ttl.collect(root)
            self.assertTrue(requests["m"]["main"])
            self.assertFalse(requests["w"]["main"])

    def test_a_projects_directory_with_no_transcripts_cannot_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "empty-project").mkdir()
            code = cache_ttl.report(root, None, None, False, io.StringIO())
            self.assertEqual(code, cache_ttl.EXIT_CANNOT_RUN)


class TestPrivacyAndCli(unittest.TestCase):
    def test_project_label_never_reveals_the_directory_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            name = "C--Users-someone-git-secretproject"
            path = root / name / "s.jsonl"
            label = cache_ttl.project_label(path, root)
            self.assertNotIn("secretproject", label)
            self.assertNotIn("someone", label)
            self.assertNotIn("C--", label)
            self.assertTrue(label.startswith("project-"))

    def test_project_label_is_stable_for_the_same_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = cache_ttl.project_label(root / "same" / "one.jsonl", root)
            b = cache_ttl.project_label(root / "same" / "two.jsonl", root)
            self.assertEqual(a, b)

    def test_window_filter_keeps_only_recent_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("old", "claude-opus-5", 1, 1, 0, 1,
                                       T0 - timedelta(days=90)),
                    fixtures.usage_row("new", "claude-opus-5", 1, 1, 0, 1, T0),
                ]}])
            requests, _ = cache_ttl.collect(root)
            kept = cache_ttl.within_window(requests, 30, T0)
            self.assertEqual(list(kept), ["new"])

    def test_missing_projects_directory_exits_cannot_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope"
            code = cache_ttl.report(missing, None, None, False, io.StringIO())
            self.assertEqual(code, cache_ttl.EXIT_CANNOT_RUN)

    def test_empty_window_is_an_ordinary_result_not_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("old", "claude-opus-5", 1, 1, 0, 1,
                                       T0 - timedelta(days=900))]}])
            code = cache_ttl.report(root, 1, None, False, io.StringIO())
            self.assertEqual(code, cache_ttl.EXIT_CLEAN)

    def test_json_output_carries_no_project_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "C--Users-someone-git-secretproject",
                 "session": "s", "rows": [
                     fixtures.usage_row("a", "claude-opus-5", 100, 5, 0, 1, T0),
                     fixtures.usage_row("b", "claude-opus-5", 100, 5, 0, 1,
                                        T0 + timedelta(seconds=600))]}])
            stream = io.StringIO()
            cache_ttl.report(root, None, None, True, stream)
            payload = stream.getvalue()
            self.assertNotIn("secretproject", payload)
            self.assertNotIn("someone", payload)
            self.assertNotIn("C--", payload)
            # Asserting only absence passes even when no label is emitted at
            # all, so it could never fail for the reason it names. Require the
            # hashed label to be present, and to be this directory's hash.
            body = json.loads(payload)
            expected = cache_ttl.project_label(
                root / "C--Users-someone-git-secretproject" / "s.jsonl", root)
            self.assertIn(expected, body["requests_by_project"])
            self.assertEqual(body["requests_by_project"][expected], 2)

    def test_verdict_flags_when_the_counterfactual_is_cheaper(self):
        """All gaps under five minutes: nothing is ever rewritten, so the
        cheaper five-minute writes win and the tool should say so."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [fixtures.usage_row("r%d" % i, "claude-opus-5", 10, 5000, 0,
                                       1, T0 + timedelta(seconds=i * 30))
                    for i in range(5)]
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": rows}])
            code = cache_ttl.report(root, None, None, False, io.StringIO())
            self.assertEqual(code, cache_ttl.EXIT_FLAGGED)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s plugins/retro/tests -t plugins/retro/tests -v`
Expected: FAIL with `AttributeError: module 'cache_ttl' has no attribute 'project_label'`

- [ ] **Step 3: Write minimal implementation**

Append to `plugins/retro/bin/cache_ttl.py`:

```python
import argparse  # noqa: E402
from datetime import timedelta, timezone  # noqa: E402
from datetime import datetime  # noqa: E402


def project_label(path, projects_dir):
    """A stable, non-reversible label for a project directory.

    Never the directory name. On a real machine those names are mangled
    absolute paths that embed the account name, other projects' names, and
    sometimes a session id -- all of which are forbidden in this repository
    and in anything this script prints. retro.redact() is not enough on its
    own, because it rewrites the home path and username but passes other
    path segments through verbatim.
    """
    try:
        raw = path.relative_to(projects_dir).parts[0]
    except (ValueError, IndexError):
        raw = "unknown"
    return "project-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def within_window(requests, days, now=None):
    """Keep requests whose start falls inside the last `days` days (UTC)."""
    if days is None:
        return requests
    if days <= 0:
        return {}
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    return {rid: record for rid, record in requests.items()
            if record["start"] >= cutoff}


def _money(value):
    return "$%s" % format(round(value, 2), ",.2f")


def report(projects_dir, days, project, as_json, stream):
    """Measure the corpus and print the verdict. Returns an exit code."""
    if not projects_dir.is_dir():
        stream.write("cannot run: no session directory at %s\n"
                     % projects_dir.name)
        return EXIT_CANNOT_RUN

    requests, skipped = collect(projects_dir)
    if not requests and not skipped:
        stream.write("cannot run: no readable transcripts\n")
        return EXIT_CANNOT_RUN

    requests = within_window(requests, days)
    if project:
        requests = {rid: record for rid, record in requests.items()
                    if project in str(record["source"])}

    main_chains = chains(requests, main_only=True)
    sub_chains = chains(requests, main_only=False)
    result = evaluate(main_chains)

    main_records = [r for c in main_chains.values() for r in c]
    sub_records = [r for c in sub_chains.values() for r in c]
    if not main_records:
        # A window or project filter that matches nothing is an ordinary
        # result, not a failure. Exit code 2 is for absent input only.
        stream.write("no main-thread requests in this window; nothing to decide\n")
        return EXIT_CLEAN
    main_read = sum(r["read"] for r in main_records)
    sub_read = sum(r["read"] for r in sub_records)
    w1_total = sum(r["w1"] for r in main_records)
    w5_total = sum(r["w5"] for r in main_records)

    # A model counts as pinned to five minutes only if it never received a
    # one-hour write anywhere in the window. Testing one request at a time
    # would flag any model that merely happened to write nothing that turn.
    wrote_1h, wrote_5m = set(), set()
    for record in main_records:
        if record["w1"]:
            wrote_1h.add(record["model"])
        if record["w5"]:
            wrote_5m.add(record["model"])
    pinned = wrote_5m - wrote_1h
    pinned_read = sum(r["read"] for r in main_records if r["model"] in pinned)
    governed = main_read - pinned_read
    all_read = main_read + sub_read
    governed_share = (100.0 * governed / all_read) if all_read else 0.0

    ttl_in_force = "one hour" if w1_total >= w5_total else "five minutes"
    if result["observed"] <= 0.0:
        # Guarding an empty record list is not enough: observed also reaches
        # zero when every main-thread model is missing from the price table,
        # and the ratio then reads 0.0, which renders as a confident "switch
        # the TTL" produced from no priced data at all.
        stream.write("no priced main-thread requests in this window; "
                     "nothing to decide\n")
        if result["unpriced"]:
            stream.write("every main-thread request used a model with no "
                         "price row: %s\n"
                         % ", ".join(sorted(result["unpriced"])))
        return EXIT_CLEAN
    keep_current = result["ratio"] >= 1.0
    verdict_code = EXIT_CLEAN if keep_current else EXIT_FLAGGED

    # Unpriced models are counted across BOTH splits, with token volume. The
    # cost model runs on main chains only, so a subagent-only unknown model
    # would otherwise never surface -- and those models feed the subagent
    # table the skill calls the validation.
    unpriced_all = Counter()
    unpriced_tokens = Counter()
    for record in main_records + sub_records:
        if record["model"] not in PRICES:
            unpriced_all[record["model"]] += 1
            unpriced_tokens[record["model"]] += record["tokens"]

    by_project = Counter()
    for record in main_records:
        by_project[project_label(record["source"], projects_dir)] += 1

    # Sensitivity 1: group gaps by project directory instead of by transcript.
    dir_chains = {}
    for record in main_records:
        try:
            key = record["source"].relative_to(projects_dir).parts[0]
        except (ValueError, IndexError):
            key = "unknown"
        dir_chains.setdefault(key, []).append(record)
    for rows in dir_chains.values():
        rows.sort(key=lambda item: item["start"])
    dir_result = evaluate(dir_chains)

    # Sensitivity 2: force every session opener to miss.
    openers_forced = 0.0
    for chain in main_chains.values():
        if not chain:
            continue
        first = chain[0]
        price = PRICES.get(first["model"])
        if price is None:
            continue
        write_5m, _, read_price = price
        hit = (first["w1"] + first["w5"]) * write_5m + first["read"] * read_price
        miss = (first["read"] + first["w1"] + first["w5"]) * write_5m
        openers_forced += miss - hit

    if as_json:
        json.dump({
            "window_days": days,
            "prices_verified_on": PRICES_VERIFIED_ON,
            "prices_source": PRICES_SOURCE,
            "ttl_in_force": ttl_in_force,
            "main_requests": len(main_records),
            "subagent_requests": len(sub_records),
            "main_read_tokens": main_read,
            "subagent_read_tokens": sub_read,
            "write_tokens_1h": w1_total,
            "write_tokens_5m": w5_total,
            "governed_share_of_read_tokens": round(governed_share, 1),
            "observed_cost": round(result["observed"], 2),
            "counterfactual_cost": round(result["counterfactual"], 2),
            "delta": round(result["delta"], 2),
            "ratio": round(result["ratio"], 3),
            "decisive_band_requests": result["bands"]["5-60m"],
            "decisive_read_tokens": result["decisive_read"],
            "neutral_read_tokens": result["neutral_read"],
            "session_openers": result["openers"],
            "unpriced_requests": dict(unpriced_all),
            "unpriced_tokens": dict(unpriced_tokens),
            "pinned_to_5m_models": sorted(pinned),
            "requests_by_project": dict(by_project),
            "snapshot_earliest": min(r["start"] for r in main_records).isoformat(),
            "snapshot_latest": max(r["start"] for r in main_records).isoformat(),
            "sensitivity_ratio_grouped_by_directory": round(dir_result["ratio"], 3),
            "sensitivity_openers_forced_to_miss": round(openers_forced, 2),
            "skipped": dict(skipped),
            "keep_current_ttl": keep_current,
        }, stream, indent=2, sort_keys=True)
        stream.write("\n")
        return verdict_code

    stream.write("prompt-cache TTL economics\n")
    stream.write("window: %s   prices verified %s\n\n"
                 % ("all history" if days is None else "last %d days" % days,
                    PRICES_VERIFIED_ON))

    stream.write("corpus\n")
    stream.write("  main-thread requests   %12d\n" % len(main_records))
    stream.write("  subagent requests      %12d   (pinned to 5m, unaffected)\n"
                 % len(sub_records))
    stream.write("  main read tokens       %12d\n" % main_read)
    stream.write("  1h write tokens        %12d\n" % w1_total)
    stream.write("  5m write tokens        %12d\n" % w5_total)
    stream.write("  TTL in force           %12s\n" % ttl_in_force)
    stream.write("  snapshot               %s .. %s\n"
                 % (min(r["start"] for r in main_records).date(),
                    max(r["start"] for r in main_records).date()))
    if pinned:
        # Names, not a count: the spec's risk mitigation is that the pinned
        # set is computed rather than hardcoded, and a bare count cannot be
        # read as evidence of that.
        stream.write("  pinned to 5m           %12s   (unaffected by the setting)\n"
                     % ", ".join(sorted(pinned)))
    stream.write("  setting governs        %11.1f%%  of all cache-read tokens\n\n"
                 % governed_share)

    stream.write("validation: subagents run on the 5m TTL, so their gap bands\n")
    stream.write("show the counterfactual directly rather than modelled.\n")
    stream.write("  %-8s %9s %9s %12s %12s\n"
                 % ("band", "requests", "zero read", "mean read", "mean write"))
    sub_bands = band_table([pair for chain in sub_chains.values()
                            for pair in gap_seconds(chain)])
    for _, _, name in BANDS:
        bucket = sub_bands[name]
        if not bucket["n"]:
            continue
        stream.write("  %-8s %9d %8.1f%% %12d %12d\n"
                     % (name, bucket["n"],
                        100.0 * bucket["zero_read"] / bucket["n"],
                        bucket["read"] // bucket["n"],
                        bucket["write"] // bucket["n"]))
    stream.write("\n")

    stream.write("main-thread gap bands\n")
    stream.write("  %-8s %9s %9s %12s %12s\n"
                 % ("band", "requests", "zero read", "mean read", "mean write"))
    main_bands = band_table([pair for chain in main_chains.values()
                             for pair in gap_seconds(chain)])
    for _, _, name in BANDS:
        bucket = main_bands[name]
        if not bucket["n"]:
            continue
        stream.write("  %-8s %9d %8.1f%% %12d %12d\n"
                     % (name, bucket["n"],
                        100.0 * bucket["zero_read"] / bucket["n"],
                        bucket["read"] // bucket["n"],
                        bucket["write"] // bucket["n"]))
    stream.write("\n")

    stream.write("cost, cache-related only (not total spend)\n")
    stream.write("  observed, %-14s %14s\n" % (ttl_in_force, _money(result["observed"])))
    stream.write("  counterfactual, 5m       %14s\n" % _money(result["counterfactual"]))
    stream.write("  difference               %14s   ratio %.2fx\n\n"
                 % (_money(result["delta"]), result["ratio"]))

    stream.write("the decision lives in the 5-60 minute band\n")
    stream.write("  requests there         %12d\n" % result["bands"]["5-60m"])
    stream.write("  their read tokens      %12d\n" % result["decisive_read"])
    stream.write("  reads costing the same %12d   (gaps under 5m)\n"
                 % result["neutral_read"])
    stream.write("  session openers        %12d   (unchanged either way)\n\n"
                 % result["openers"])

    stream.write("requests by project (labels are hashes, never names)\n")
    for label, count in sorted(by_project.items()):
        stream.write("  %-22s %12d\n" % (label, count))
    stream.write("\n")

    stream.write("sensitivities, so the modelling choices are not buried\n")
    stream.write("  gaps grouped by project dir  ratio %.2fx (vs %.2fx by transcript)\n"
                 % (dir_result["ratio"], result["ratio"]))
    stream.write("  openers all forced to miss   %s on top of the delta\n\n"
                 % _money(openers_forced))

    if unpriced_all:
        stream.write("unpriced models (no price row; not defaulted)\n")
        for model, count in sorted(unpriced_all.items()):
            stream.write("  %-30s %6d requests %12d tokens\n"
                         % (model, count, unpriced_tokens[model]))
        stream.write("\n")
    if skipped:
        stream.write("skipped rows: %s\n\n"
                     % ", ".join("%s=%d" % kv for kv in sorted(skipped.items())))

    if keep_current:
        stream.write("VERDICT: keep the %s TTL. Forcing five minutes would cost\n"
                     % ttl_in_force)
        stream.write("         %s more, %.2fx, over this window.\n"
                     % (_money(result["delta"]), result["ratio"]))
    else:
        stream.write("VERDICT: the five-minute TTL would be cheaper here, by %s.\n"
                     % _money(-result["delta"]))
        stream.write("         Set FORCE_PROMPT_CACHING_5M=1 to switch.\n")
    return verdict_code


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="cache_ttl",
        description="Decide the prompt-cache TTL from measured session history.")
    sub = parser.add_subparsers(required=True, dest="command")
    p_report = sub.add_parser("report", help="measure the corpus and decide")
    p_report.add_argument("--days", type=int, default=None,
                          help="restrict to the last N days (UTC); "
                               "default is the whole corpus")
    p_report.add_argument("--project", default=None,
                          help="restrict to transcripts whose path contains "
                               "this substring; only the hashed label is printed")
    p_report.add_argument("--json", action="store_true",
                          help="emit the same figures machine-readably")
    args = parser.parse_args(argv)
    try:
        return report(PROJECTS_DIR, args.days, args.project, args.json,
                      sys.stdout)
    except Exception as error:
        # Exit 1 is reserved for "ran clean and flagged something". A crash
        # that exits 1 is indistinguishable from a verdict to an automated
        # caller, so every unexpected failure lands on 2.
        sys.stderr.write("cannot run: %s: %s\n"
                         % (type(error).__name__, error))
        return EXIT_CANNOT_RUN


if __name__ == "__main__":
    sys.exit(main())
```

Move the `import argparse` and `datetime` lines up to the module's import block
rather than leaving them mid-file; they are written inline above only to keep this
step's diff self-contained.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s plugins/retro/tests -t plugins/retro/tests -v`
Expected: PASS, 36 tests

- [ ] **Step 5: Commit**

```bash
git add plugins/retro/bin/cache_ttl.py plugins/retro/tests/test_cache_ttl.py
git commit -F - <<'MSG'
feat: report, privacy rules, and command line for cache TTL

Project directories are labelled by a stable hash and never by name, because
on a real machine those names are mangled absolute paths that embed the
account name and other projects. The json payload carries no project
identifier at all.

An empty window or project filter is an ordinary result and exits clean;
exit code two is reserved for genuinely absent input.
MSG
```

---

## Task 6: Skill and manifests — Lane B, concurrent with Tasks 1 to 5

**Files:**
- Create: `plugins/retro/skills/deciding-the-prompt-cache-ttl/SKILL.md`
- Modify: `plugins/retro/.claude-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json`

**Interfaces:**
- Consumes: nothing at build time. It documents `cache_ttl.py report`, whose
  interface is fixed by this plan.
- Produces: nothing other tasks consume.

- [ ] **Step 1: Write the skill**

Create `plugins/retro/skills/deciding-the-prompt-cache-ttl/SKILL.md`:

```markdown
---
name: deciding-the-prompt-cache-ttl
description: Use when deciding whether Claude Code's prompt cache should use the one-hour or the five-minute TTL, when a cache-related environment variable is about to be set, or when re-checking that the TTL in force still earns its keep after working habits changed. Measures session history rather than reasoning about it.
---

# Deciding the prompt-cache TTL

## Overview

Whether to run the prompt cache at a one-hour or a five-minute time to live,
decided from what the machine actually did rather than from intuition.

Run it:

    python "${CLAUDE_PLUGIN_ROOT}/bin/cache_ttl.py" report
    python "${CLAUDE_PLUGIN_ROOT}/bin/cache_ttl.py" report --days 30

`${CLAUDE_PLUGIN_ROOT}` is how every other skill in this plugin invokes its
script, and it is the only form that resolves when the plugin is installed
rather than run from a checkout.

Exit `0` means the TTL in force is the right one, `1` means it should change,
`2` means it could not run.

## The mechanic that decides it

A cache read refreshes the time to live. So session length is irrelevant — what
matters is the **gap between consecutive requests**. A nine-hour session whose
every pause stays under five minutes gets nothing from the one-hour TTL.

The decision therefore lives in one narrow band: gaps between five minutes and
one hour. Shorter and both policies hit; longer and both mostly miss.

## Reading the output

- **Setting governs N% of read tokens.** Subagents run on the five-minute TTL
  whatever you set, and some models are pinned to five minutes too. The verdict
  applies to the governed share, never to total spend.
- **Cost is cache-related only.** Uncached input and output tokens are excluded.
  They cancel in the comparison but the figure is not a bill.
- **The subagent band table is the validation.** Subagents already run the
  five-minute policy, so their bands show the counterfactual directly. Reads
  should collapse and writes should spike as gaps cross five minutes. If that
  shape ever disappears, the cost model's premise has changed and the verdict
  needs rechecking before it is trusted.

## The procedure

1. Run the report over all history. Read the verdict line last; read the
   "setting governs" line first, because it bounds what the verdict applies to.
2. Check the subagent validation table still shows reads collapsing and writes
   spiking across five minutes. If that shape is gone, stop — the cost model's
   premise has changed and the verdict is not trustworthy until you know why.
3. Check `PRICES_VERIFIED_ON` against the pricing page before quoting a dollar
   figure.
4. Check the unpriced bucket is empty. A model with no price row is missing
   from the cost model entirely.
5. Only then act on the verdict, by setting or clearing the environment
   variable below.

## The knobs

| Variable | Effect |
| --- | --- |
| `FORCE_PROMPT_CACHING_5M=1` | Force five minutes regardless of authentication |
| `ENABLE_PROMPT_CACHING_1H=1` | Opt into one hour on an API key or third-party provider; on a subscription, hold one hour while drawing on usage credits |
| `DISABLE_PROMPT_CACHING=1` | Turn caching off entirely; for debugging only |

On a Claude subscription the one-hour TTL is requested automatically, and drops
to five minutes only while drawing on usage credits.

## Counting rules the tool depends on

Get any of these wrong and the numbers move without looking wrong:

1. **Deduplicate by request id globally, not per file.** Resuming or forking a
   session copies rows, request id and usage intact, into the new transcript.
   Per-file counting double-counts them.
2. **When rows of one request disagree, the settled row wins** — the one with
   the largest total token count. Streaming leaves zeroed placeholder rows.
3. **A request's timestamp is the earliest of its rows.** Rows span minutes,
   enough to move a request across the five-minute boundary.
4. **Walk with `rglob` and split by path depth.** A `*/*.jsonl` glob silently
   drops every subagent transcript.
5. **Order by timestamp, not file position.** Some rows are out of order, and
   trusting file order produces negative gaps.

## Before trusting a dollar figure

Check `PRICES_VERIFIED_ON` in the script against the pricing page. Prices change;
the table does not update itself. Unknown model ids are reported in an "unpriced"
bucket rather than assigned a default — a non-empty bucket means the table needs
a new row.

## Privacy

Output carries counts, dollar figures, and hashed project labels only. Never
paste a run into a tracked file: project directory names on a real machine are
mangled absolute paths that embed the account name and other projects.

## Common mistakes

- **Reading the verdict as applying to your whole bill.** It covers the governed
  share only — subagents and any five-minute-pinned model are outside it, and
  the figure is cache-related cost, not total spend.
- **Treating a narrow ratio as precise.** Grouping gaps by project directory
  instead of by transcript moves the ratio by several points. The report prints
  that sensitivity; read it before quoting a margin.
- **Quoting a dollar figure without checking the price date.** The table does
  not update itself.
- **Comparing two runs as if they were the same measurement.** The corpus grows
  continuously and may be pruned by a retention setting, so counts move in both
  directions between runs. Compare the ratio, not the totals.

## Red flags

- The unpriced bucket is non-empty → a model is missing from the cost model.
- The subagent validation table has lost its five-minute cliff → the premise
  the counterfactual rests on no longer holds.
- The verdict says "nothing to decide" → the window or filter matched nothing
  priceable; widen it rather than reading that as a result.
- The report names a project directory rather than a hash → stop and fix it
  before the output goes anywhere.
```

- [ ] **Step 2: Verify the skill's frontmatter parses and the file is complete**

Run:
```bash
python3 -c "
import sys
text = open('plugins/retro/skills/deciding-the-prompt-cache-ttl/SKILL.md', encoding='utf-8').read()
assert text.startswith('---'), 'no frontmatter'
head = text.split('---')[1]
assert 'name:' in head and 'description:' in head, 'missing frontmatter keys'
assert 'TODO' not in text and 'TBD' not in text, 'placeholder left in skill'
print('skill frontmatter OK,', len(text), 'chars')
"
```
Expected: `skill frontmatter OK, <n> chars`

- [ ] **Step 3: Update both manifests in step**

The project CLAUDE.md requires `.claude-plugin/marketplace.json` and each
plugin's own `plugin.json` to stay in step. Adding a fourth lens to `retro`
means bumping the version and widening the description and keywords in both.

In `plugins/retro/.claude-plugin/plugin.json`, set:

```json
{
  "name": "retro",
  "description": "Retrospectives from measured session history: find recurring friction, audit which standing rules and skills actually do anything, scout tooling against named seams, and decide harness settings such as the prompt-cache TTL from what the machine did.",
  "version": "0.2.0",
  "author": {
    "name": "polston"
  },
  "keywords": ["retrospective", "workflow", "session-history", "metrics", "cost"]
}
```

In `.claude-plugin/marketplace.json`, update the `retro` entry's `description`,
`version`, and `keywords` to exactly the same values, leaving `name`, `source`,
and `category` untouched.

- [ ] **Step 4: Verify the manifests agree**

Run:
```bash
python3 -c "
import json
plugin = json.load(open('plugins/retro/.claude-plugin/plugin.json', encoding='utf-8'))
market = json.load(open('.claude-plugin/marketplace.json', encoding='utf-8'))
entry = [p for p in market['plugins'] if p['name'] == 'retro'][0]
for field in ('description', 'version', 'keywords'):
    assert plugin[field] == entry[field], (field, plugin[field], entry[field])
print('manifests in step at version', plugin['version'])
"
```
Expected: `manifests in step at version 0.2.0`

- [ ] **Step 5: Record the new directory kind in both instruction files**

`plugins/<name>/tests/` is a layout this repository has not had before, and the
"Layout and conventions" list in `CLAUDE.md` enumerates only `bin/`, `skills/`,
and `docs/plans/`. Add one bullet to that list, and the same bullet to
`AGENTS.md`, which is kept identical in substance:

```
- `plugins/<name>/tests/` — stdlib `unittest`, no runner or dependency. Run
  with `python -m unittest discover -s plugins/<name>/tests -t plugins/<name>/tests`.
```

- [ ] **Step 6: Commit**

```bash
git add plugins/retro/skills/deciding-the-prompt-cache-ttl/SKILL.md \
        plugins/retro/.claude-plugin/plugin.json \
        .claude-plugin/marketplace.json CLAUDE.md AGENTS.md
git commit -F - <<'MSG'
feat: skill for deciding the prompt-cache TTL

Documents the mechanic that makes the measurement correct, that a cache read
refreshes the time to live so gaps rather than session length decide the
question, and the five counting rules that move the numbers without looking
wrong when they are missed.

Carries no sample run: project directory names on a real machine are mangled
absolute paths, and a pasted run would put them in a published repository.

Widens the retro plugin to a fourth lens and bumps both manifests in step.
MSG
```

---

## Task 7: Verify against the real corpus

**Files:**
- Modify: `plugins/retro/bin/cache_ttl.py` (only if a defect is found)

**Interfaces:**
- Consumes: the finished script from Task 5.
- Produces: nothing. This is the acceptance gate.

Review disproved the first draft of this gate. It required "every count at or
above the spec value", which the per-file-deduplication bug — the exact bug
counting rule 1 exists to prevent — passes comfortably: per-file dedup inflates
read tokens by 7.2% and one-hour writes by 12.7%, and still lands the ratio at
1.329, inside the tolerance. Every count reads as healthy precisely because the
bug over-counts. A monotonically growing corpus means that check can never fail
in the direction it was written to catch, and a transcript-retention setting
means it can fail for a reason that is not a defect at all.

The gate below tests the counting rules directly instead of inferring them
from totals.

- [ ] **Step 1: Run the full test suite**

Run: `python3 -m unittest discover -s plugins/retro/tests -t plugins/retro/tests -v`
Expected: PASS, 35 tests, 0 failures

- [ ] **Step 2: Run against the real corpus and time it**

Run:
```bash
time python3 plugins/retro/bin/cache_ttl.py report
```
Expected: completes in under a minute, exit code `0`.

- [ ] **Step 3: Prove global deduplication actually fired**

This is the check that replaces the count comparison. It measures the thing the
rule exists for, and it fails loudly if the walk ever reverts to per-file
counting.

```bash
python3 - <<'PROBE'
import sys, pathlib
sys.path.insert(0, "plugins/retro/bin")
import cache_ttl
projects = cache_ttl.PROJECTS_DIR
per_file = 0
seen = {}
for path in sorted(projects.rglob("*.jsonl")):
    local = set()
    for record in cache_ttl._rows(path, True, cache_ttl.Counter()):
        if record["rid"] in local:
            continue
        local.add(record["rid"])
        per_file += 1
        seen.setdefault(record["rid"], set()).add(path)
across = sum(1 for files in seen.values() if len(files) > 1)
print("requests counted per file :", per_file)
print("requests after global dedup:", len(seen))
print("ids appearing in >1 file  :", across)
assert across > 0, "no cross-file duplicates found; the probe itself is broken"
assert len(seen) < per_file, "global dedup removed nothing"
assert per_file - len(seen) == across, "dedup did not remove exactly the duplicates"
print("OK: global dedup removed", per_file - len(seen), "double-counted requests")
PROBE
```
Expected: a non-zero duplicate count and `OK: global dedup removed N ...`.
If `across` is 0, the corpus genuinely has no forked or resumed sessions and
this probe cannot gate — say so rather than treating the pass as meaningful.

- [ ] **Step 4: Check internal consistency of the counts**

These relations hold by construction, so any violation is a defect regardless
of how the corpus has grown or been pruned.

```bash
python3 - <<'PROBE'
import sys
sys.path.insert(0, "plugins/retro/bin")
import cache_ttl
projects = cache_ttl.PROJECTS_DIR
requests, skipped = cache_ttl.collect(projects)
main = cache_ttl.chains(requests, main_only=True)
top_level = [p for p in projects.rglob("*.jsonl")
             if cache_ttl.is_main_thread(p, projects)]
openers = len(main)
print("top-level files:", len(top_level), " chains:", len(main),
      " openers:", openers, " skipped:", dict(skipped))
assert openers == len(main), "one opener per chain, by definition"
assert len(main) <= len(top_level), "more chains than transcripts"
result = cache_ttl.evaluate(main)
banded = sum(result["bands"].values())
assert banded + result["openers"] == sum(len(c) for c in main.values()) \
       - sum(result["unpriced"].values()), "requests lost between bands and openers"
print("OK: counts are internally consistent")
PROBE
```
Expected: `OK: counts are internally consistent`.

- [ ] **Step 5: Check the ratio, and read the sensitivity before judging it**

The ratio should land near **1.33**, and the verdict direction (keep the
one-hour TTL) is what actually matters. Treat **1.25 to 1.45** as the range
that needs no investigation. That band is deliberately wider than the model's
own sensitivity: the report's own per-directory grouping line lands around
1.28, and that is a modelling variant, not a defect.

A ratio below 1.0 means the tool is recommending a switch — that is a real
result, not a failure, but confirm it against the gap bands before believing
it, because it should not happen while the decisive band holds ~1,000 requests.

If the ratio lands outside 1.25 to 1.45, do not widen this range. Compare the
gap-band table against the spec's and find which band moved.

- [ ] **Step 6: Verify the output leaks nothing, and run the repo's own audit**

```bash
SCRATCH="${TMPDIR:-/tmp}/cache-ttl-check"
mkdir -p "$SCRATCH"
python3 plugins/retro/bin/cache_ttl.py report > "$SCRATCH/plain.txt"
python3 plugins/retro/bin/cache_ttl.py report --json > "$SCRATCH/out.json"
```

Then check both files. A plain absence-grep cannot fail usefully here, because
the report emits no path segments at all — so assert the hashed labels are
present as well as the names absent:

```bash
python3 - <<'PROBE'
import json, os, pathlib, re
scratch = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "cache-ttl-check"
plain = (scratch / "plain.txt").read_text(encoding="utf-8")
body = json.loads((scratch / "out.json").read_text(encoding="utf-8"))
account = pathlib.Path.home().name
banned = [account, "C--", "/Users/", "/home/", str(pathlib.Path.home())]
for text, name in ((plain, "plain"), (json.dumps(body), "json")):
    for token in banned:
        assert token not in text, "%s output contains %r" % (name, token)
labels = body["requests_by_project"]
assert labels, "no hashed labels in json; the hashing path never ran"
assert all(re.fullmatch(r"project-[0-9a-f]{8}", k) for k in labels), labels
assert re.search(r"project-[0-9a-f]{8}", plain), "no hashed labels in plain text"
print("OK: %d hashed labels in plain text, none in json, no identifying tokens"
      % len(set(labels)))
PROBE
rm -rf "$SCRATCH"
```
Expected: `OK: N hashed labels, no identifying tokens in either output`.

Then run the repository's own auditor, which no earlier step covered:

```bash
sh plugins/core/bin/repo-privacy-audit
```
Expected: only the known-accepted hit in the identity column, which the project
CLAUDE.md names as deliberate. Any hit in a tree or patch column that names this
change is a finding — stop and report it rather than committing.

- [ ] **Step 7: Verify the exit codes**

```bash
python3 plugins/retro/bin/cache_ttl.py report --days 30 >/dev/null; echo "30d exit=$?"
python3 plugins/retro/bin/cache_ttl.py report --days 1 --project no-such-project >/dev/null; echo "empty exit=$?"
python3 plugins/retro/bin/cache_ttl.py report --days 0 >/dev/null; echo "zero-days exit=$?"
```
Expected: `30d exit=0`, `empty exit=0`, `zero-days exit=0`. An empty filter
result is an ordinary result, never exit `2`, and `--days 0` means zero days —
not, as the first draft had it, all of history.

- [ ] **Step 8: Commit any fix, then report**

If Steps 2 to 7 found no defect there is nothing to commit; report the measured
ratio, the runtime, and the duplicate count from Step 3. If a defect was found,
fix it, re-run the suite, and:

```bash
git add plugins/retro/bin/cache_ttl.py
git commit -F - <<'MSG'
fix: correct cache TTL measurement against the real corpus
MSG
```

---


## Self-review

**Spec coverage.** The first draft of this line claimed every spec rule mapped
to a task. Review disproved it: the report was missing the main-thread gap-band
table, both sensitivity lines, the snapshot boundary, the pinned-model names,
and the unpriced token volume — while the self-review asserted full coverage.
All are now in Task 5. Mapping as it now stands: the corpus walk and the five
counting rules to Tasks 2 and 3; the cost model, price table, and unpriced
bucket to Task 4; the report sections, sensitivities, window, project filter,
JSON payload, privacy rules, and exit codes to Task 5; the skill and the
manifests to Task 6; the acceptance gate to Task 7.

**Two spec statements this plan tightens, both deliberately:**

1. The spec allowed a matched `--project` label to be shown redacted. This plan
   always hashes. Tighter, and it cannot leak.
2. The spec's risk table said a skipped request breaks the gap chain. This plan
   distinguishes: a row with no timestamp is dropped entirely and tallied, while
   a request whose model has no price stays in the chain and contributes no cost.
   Dropping a priced-unknown request from the chain would invent a longer gap for
   its successor, which is the failure the spec was guarding against.

**Two defects caught by walking the tests against the code before committing:**

1. An empty window produced `observed == 0.0`, so `ratio` was `0.0`, so the
   verdict read "switch to five minutes" — from no data at all. Task 5 now
   returns `EXIT_CLEAN` with an explicit message as soon as the window holds no
   main-thread requests, before any arithmetic runs.
2. Pinned-model detection tested one request at a time, so any model that
   happened to write nothing on a given turn was flagged as pinned. It now
   compares the set of models that ever wrote at one hour against those that
   ever wrote at five minutes, across the window. The first draft called this
   "inflating the setting governs N% figure"; measured, the two rules differ by
   0.002 percentage points. The direction was right and the magnitude was
   asserted without measuring it.

**Type consistency.** Record keys `rid`, `model`, `read`, `w1`, `w5`, `out`,
`tokens`, `start`, `source`, `main` are written in Task 2 and read unchanged in
Tasks 3, 4, and 5. `PRICES` values are ordered `(write_5m, write_1h, read)`
everywhere, including the invariant test in Task 4. `evaluate` returns the keys
Task 5's `report` reads: `observed`, `counterfactual`, `ratio`, `delta`,
`openers`, `decisive_read`, `neutral_read`, `bands`, `unpriced`. Band names are
the six in `BANDS` for the display table and the three coarse names `0-5m`,
`5-60m`, `>60m` in `evaluate`'s counter — these are deliberately different sets,
used in different places, and never mixed.
