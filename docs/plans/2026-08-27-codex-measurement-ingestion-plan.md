# Codex Measurement Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach `retro.py extract` to measure Codex rollout transcripts into the same ledger as Claude transcripts, labelled per harness, with per-harness reporting.

**Architecture:** A second reducer (`measure_codex`) beside `measure()`, sharing the judgment helpers; roots resolved per harness at call time; schema 7 rows carry `harness`, `population`, `parent_session_id`, `project_key`, `ineligible`, `compacted`; every reporting command splits by harness and divides each counter by its own eligible-row count.

**Tech Stack:** Python 3 stdlib only; `unittest` via `sh plugins/p/bin/python-launcher`.

**Spec:** `docs/plans/2026-08-27-codex-measurement-ingestion-spec.md` — read it first; every design decision below argues from it. Grounding numbers cited in code comments come from its Grounding section.

## Global Constraints

- Stdlib only, no dependencies, no build step (repo CLAUDE.md; `test_ci_contract.py` enforces).
- Nothing personally identifiable enters the repo: no machine paths, no home-shaped literals in fixtures — redaction fixtures build `cwd` from `Path.home()` at runtime (spec Verification 2).
- No transcript content is ever copied into fixtures; all fixtures are synthetic (spec Verification 2).
- Commit messages via `git commit -F -` fed by a single-quoted heredoc; never backticks in a double-quoted `-m` (repo CLAUDE.md).
- Exit-code convention for `bin/` scripts: 0 clean, 1 flagged, 2 could not run — `extract` exits 2 only when every root is missing (spec D1.2).
- Test command: `sh plugins/p/bin/python-launcher -B -m unittest discover -s plugins/p/tests -t plugins/p/tests`; single module: append `-p "test_retro*.py"` variant shown per task.
- All paths below are relative to the worktree root. All `retro.py` line anchors are for the file at commit `d1d5d6a` and drift as tasks land — anchor by the quoted code, not the number.

## Task order and parallelism

Tasks 1→2→3→4→5→5b are serial (same file: `plugins/p/bin/retro.py`; Task 1 is their regression net). Tasks 6 and 7 are file-disjoint from every other task and from each other — dispatchable in parallel any time after Task 1. Task 8 is last.

---

### Task 1: Claude-side characterisation tests

The existing suite exercises none of the reducer or reporting code this plan
rewrites (only `friction_score` and `report_labels` are tested today, in
`test_eval_legacy_compat.py`). Pin current Claude behaviour before touching it.

**Files:**
- Create: `plugins/p/tests/test_retro_measure.py`
- Modify: `plugins/p/tests/fixtures.py` (append two helpers)
- Test: the new file is the test

**Interfaces:**
- Consumes: `fixtures.build_corpus(root, sessions)` (exists); `retro.py` loaded by file path exactly as `test_eval_legacy_compat.py` does.
- Produces: `fixtures.claude_user(text, when, source="typed")` and `fixtures.claude_assistant(text, when, tools=())` record builders; module-level `load_retro()` helper other test modules copy.

- [ ] **Step 1: Add Claude record builders to fixtures.py**

Append to `plugins/p/tests/fixtures.py`:

```python
def claude_user(text, when, source="typed"):
    """One typed user record, minimal but shaped like the real thing."""
    return {
        "type": "user",
        "timestamp": when.isoformat().replace("+00:00", "Z"),
        "sessionId": "sess-claude-1",
        "cwd": "/tmp/proj",
        "gitBranch": "main",
        "version": "9.9.9",
        "promptSource": source,
        "message": {"role": "user",
                    "content": [{"type": "text", "text": text}]},
    }


def claude_assistant(text, when, tools=()):
    """One assistant record; `tools` is (name, input_dict) pairs."""
    content = [{"type": "text", "text": text}]
    for index, (name, tool_input) in enumerate(tools):
        content.append({"type": "tool_use", "id": "tu-%d" % index,
                        "name": name, "input": tool_input})
    return {
        "type": "assistant",
        "timestamp": when.isoformat().replace("+00:00", "Z"),
        "sessionId": "sess-claude-1",
        "cwd": "/tmp/proj",
        "message": {"role": "assistant", "content": content,
                    "usage": {"input_tokens": 10, "output_tokens": 20,
                              "cache_read_input_tokens": 5}},
    }
```

- [ ] **Step 2: Write the characterisation tests**

Create `plugins/p/tests/test_retro_measure.py`:

```python
"""Characterisation of measure() over a synthetic Claude transcript.

Pins the counters, identity fields, and ending that the Codex ingestion
change must not move. Written BEFORE that change, deliberately."""

import importlib.util
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fixtures import build_corpus, claude_assistant, claude_user

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def load_retro():
    spec = importlib.util.spec_from_file_location(
        "retro_measure_under_test", PLUGIN_ROOT / "bin" / "retro.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_session(root):
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    rows = [
        claude_user("please do the thing", t0),
        claude_assistant("x" * 400, t0 + timedelta(minutes=1),
                         tools=[("Read", {"file_path": "/tmp/a"})]),
        # Short reply after a long turn, corrective wording: a correction.
        claude_user("no, wrong file", t0 + timedelta(minutes=2)),
        # Long enough to clear CORRECTION_MIN_PRIOR_CHARS (200) — a short
        # turn before "sure" classifies as nothing, not as an approval.
        claude_assistant("z" * 400, t0 + timedelta(minutes=3)),
        # Whole-reply affirmative after a long turn: an approval.
        claude_user("sure", t0 + timedelta(minutes=4)),
        claude_assistant("finished", t0 + timedelta(minutes=5)),
    ]
    build_corpus(root, [{"project": "projA", "session": "s1", "rows": rows}])
    return root / "projA" / "s1.jsonl"


class MeasureCharacterisation(unittest.TestCase):
    def setUp(self):
        self.retro = load_retro()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = write_session(Path(self.tmp.name))

    def measured(self):
        return self.measure_row()

    def measure_row(self):
        return self.retro.measure(self.path)

    def test_counters_pin(self):
        row = self.measure_row()
        self.assertEqual(3, row["turns"])
        self.assertEqual(3, row["user_prompts"])
        self.assertEqual(1, row["tool_calls"])
        self.assertEqual(1, row["correction_candidates"])
        self.assertEqual(1, row["approval_turns"])
        self.assertEqual(0, row["repeat_calls"])
        self.assertEqual(0, row["interrupts"])
        self.assertEqual("text", row["ending"])

    def test_identity_pin(self):
        row = self.measure_row()
        self.assertEqual("sess-claude-1", row["session_id"])
        self.assertEqual("main", row["git_branch"])
        self.assertEqual("2026-08-01", row["date"])
        self.assertEqual(30, row["tokens_in"])
        self.assertEqual(60, row["tokens_out"])
        self.assertEqual(15, row["cache_read"])

    def test_subagent_layout_is_recognised(self):
        sub_rows = [claude_user("go", datetime(2026, 8, 1, tzinfo=timezone.utc)),
                    claude_assistant("ok", datetime(2026, 8, 1, tzinfo=timezone.utc))]
        build_corpus(Path(self.tmp.name), [{
            "project": "projA", "session": "s1",
            "subagent": True, "rows": sub_rows}])
        sub_path = (Path(self.tmp.name) / "projA" / "s1" / "subagents"
                    / "agent-0.jsonl")
        row = self.retro.measure(sub_path)
        # The temp-root fallback makes `rel` the bare filename, so the
        # path-prefix rule cannot see `subagents/`. Task 3 replaces this
        # with a root-relative test through measure(path, harness, root).
        self.assertFalse(row["is_subagent"])


class CurrentShapes(unittest.TestCase):
    """Pin today's totals/split_population shapes. Task 3 REWRITES these
    tests against the new contracts; until then they are the regression
    net the spec's Verification 1 requires."""

    def setUp(self):
        self.retro = load_retro()

    def test_totals_returns_one_counter_today(self):
        agg = self.retro.totals([{"turns": 5, "tokens_out": 7},
                                 {"turns": 2, "tokens_out": 1}])
        self.assertEqual(7, agg["turns"])
        self.assertEqual(8, agg["tokens_out"])

    def test_split_population_returns_main_sub_tuple_today(self):
        main, sub = self.retro.split_population(
            [{"is_subagent": False}, {"is_subagent": True}])
        self.assertEqual(1, len(main))
        self.assertEqual(1, len(sub))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the new module; expect all green (it pins current behaviour)**

Run: `sh plugins/p/bin/python-launcher -B -m unittest discover -s plugins/p/tests -t plugins/p/tests -p "test_retro_measure.py" -v`
Expected: PASS. If an assertion fails, the pin is wrong — fix the test to
match actual behaviour (print the row), never the code.

- [ ] **Step 4: Run the full suite — three times, capturing verbose output**

Run three times: `sh plugins/p/bin/python-launcher -B -m unittest discover -s plugins/p/tests -t plugins/p/tests -v 2>&1 | tail -30`
Expected: 332 + new tests, OK (skipped=1) every time. The review baseline saw
one unreproduced failure in eight runs whose name was not captured; if any run
fails here, record the failing test's name and fix or quarantine it in this
task's commit — a flake left in place will read as a regression at every
later task's gate.

- [ ] **Step 5: Commit**

```bash
git add plugins/p/tests/fixtures.py plugins/p/tests/test_retro_measure.py
git commit -F - <<'MSG'
Pin Claude reducer behaviour before Codex ingestion

Characterisation tests over a synthetic transcript: counters, identity
fields, ending. The Codex change must not move these.
MSG
```

---

### Task 2: Roots, harness detection, and the extract walk

**Files:**
- Modify: `plugins/p/bin/retro.py` — module constants (~line 51), `measure_outcome` (~914), `cmd_extract` (~947)
- Create: `plugins/p/tests/test_retro_extract.py`

**Interfaces:**
- Produces: `claude_projects_dir()`, `codex_sessions_dir()` → `Path`; `transcript_roots()` → tuple of `(harness, Path)`, Claude first; `is_rollout(path)` → bool; `ROLLOUT_TYPES` frozenset; `measure_outcome(path, harness, root)`; `measure(path, harness="claude", root=None)` (row unchanged this task).
- Consumes: `read_records`, `TranscriptUnreadable`, `MEASURED/NOT_TRANSCRIPT/UNREADABLE`.

- [ ] **Step 1: Write failing tests**

Create `plugins/p/tests/test_retro_extract.py`:

```python
"""Root resolution, structural harness detection, and the extract walk."""

import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from fixtures import build_corpus, claude_assistant, claude_user

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def load_retro():
    spec = importlib.util.spec_from_file_location(
        "retro_extract_under_test", PLUGIN_ROOT / "bin" / "retro.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def minimal_rollout_lines():
    t = "2026-08-01T12:00:00.000Z"
    return [
        {"type": "session_meta", "timestamp": t, "payload": {
            "session_id": "sess-cx-1", "cwd": "/tmp/w", "cli_version": "1.0",
            "thread_source": "user", "git": {"branch": "main"}}},
        {"type": "response_item", "timestamp": t, "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "hello there"}]}},
        {"type": "response_item", "timestamp": t, "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "hi"}]}},
    ]


def write_rollout(root, rel, lines=None):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in (lines or minimal_rollout_lines()):
            fh.write(json.dumps(rec) + "\n")
    return path


class RootResolution(unittest.TestCase):
    def test_env_overrides_resolve_at_call_time(self):
        retro = load_retro()
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/cxhome",
                                          "CLAUDE_CONFIG_DIR": "/tmp/cchome"}):
            self.assertEqual(Path("/tmp/cxhome") / "sessions",
                             retro.codex_sessions_dir())
            self.assertEqual(Path("/tmp/cchome") / "projects",
                             retro.claude_projects_dir())
        roots = retro.transcript_roots()
        self.assertEqual(("claude", "codex"), tuple(h for h, _ in roots))


class HarnessDetection(unittest.TestCase):
    def setUp(self):
        self.retro = load_retro()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_rollout_detected_by_first_record(self):
        path = write_rollout(self.root, "2026/08/01/rollout-x.jsonl")
        self.assertTrue(self.retro.is_rollout(path))

    def test_claude_transcript_is_not_rollout(self):
        t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
        build_corpus(self.root, [{"project": "p", "session": "s", "rows": [
            claude_user("hi", t0), claude_assistant("yo", t0)]}])
        self.assertFalse(self.retro.is_rollout(self.root / "p" / "s.jsonl"))

    def test_junk_file_is_neither(self):
        path = self.root / "junk.jsonl"
        path.write_text('{"type": "something-else"}\n', encoding="utf-8")
        self.assertFalse(self.retro.is_rollout(path))
        outcome, row = self.retro.measure_outcome(path, "claude", self.root)
        self.assertEqual(self.retro.NOT_TRANSCRIPT, outcome)

    def test_rollout_without_session_meta_is_still_a_rollout(self):
        recs = minimal_rollout_lines()[1:]   # drop the session_meta
        path = write_rollout(self.root, "2026/08/01/no-meta.jsonl", recs)
        self.assertTrue(self.retro.is_rollout(path))

    def test_mixed_file_is_a_rollout(self):
        # Any rollout-set type inside the first 20 records decides; a
        # Claude-set record before it does not veto (spec D1.4).
        recs = [{"type": "user", "message": {"content": []}},
                {"type": "turn_context", "payload": {}}]
        path = write_rollout(self.root, "2026/08/01/mixed.jsonl", recs)
        self.assertTrue(self.retro.is_rollout(path))


class ExtractWalk(unittest.TestCase):
    def run_extract(self, claude_home, codex_home, work):
        env = {"CLAUDE_CONFIG_DIR": str(claude_home),
               "CODEX_HOME": str(codex_home), "RETRO_HOME": str(work)}
        with mock.patch.dict(os.environ, env):
            retro = load_retro()   # WORK_DIR reads RETRO_HOME at import
            args = mock.Mock(rebuild=False)
            try:
                code = retro.cmd_extract(args)
            except SystemExit as stop:
                return retro, stop.code
            return retro, code

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.claude_home = base / "cc"
        self.codex_home = base / "cx"
        self.work = base / "work"

    def test_both_roots_missing_exits_2(self):
        retro, code = self.run_extract(self.claude_home, self.codex_home,
                                       self.work)
        self.assertEqual(retro.EXIT_CANNOT_RUN, code)

    # Passes once measure_codex lands (Task 4 removes this decorator);
    # until then the stub measures no rollout and the ledger stays empty.
    @unittest.expectedFailure
    def test_single_root_is_enough(self):
        write_rollout(self.codex_home / "sessions", "2026/08/01/r.jsonl")
        retro, code = self.run_extract(self.claude_home, self.codex_home,
                                       self.work)
        self.assertIn(code, (retro.EXIT_CLEAN, retro.EXIT_FLAGGED))
        ledger = (self.work / "metrics.jsonl").read_text(encoding="utf-8")
        self.assertEqual(1, len(ledger.strip().splitlines()))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `sh plugins/p/bin/python-launcher -B -m unittest discover -s plugins/p/tests -t plugins/p/tests -p "test_retro_extract.py" -v`
Expected: FAIL — `AttributeError` on `codex_sessions_dir` / `is_rollout`, and `measure_outcome` arity.

- [ ] **Step 3: Implement roots and detection in retro.py**

Directly under the `PROJECTS_DIR = CLAUDE_DIR / "projects"` line, add:

```python
def claude_projects_dir():
    """The Claude transcript root, resolved at call time so tests can
    inject it and so CLAUDE_CONFIG_DIR is honoured — retiring the
    documented asymmetry with stopped-promises (spec D1.1)."""
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(config) if config else HOME / ".claude") / "projects"


def codex_sessions_dir():
    home = os.environ.get("CODEX_HOME")
    return (Path(home) if home else HOME / ".codex") / "sessions"


def transcript_roots():
    """(harness, root) pairs, Claude first: a file reachable from two
    roots is owned by the first (spec D1.3)."""
    return (("claude", claude_projects_dir()), ("codex", codex_sessions_dir()))
```

Near `MEASURED, NOT_TRANSCRIPT, UNREADABLE` (~line 703), add:

```python
# Structural harness detection (spec D1.4). Every observed rollout opens
# with session_meta, so record 1 decides in practice; the 20-record cap
# guards a future leading sidecar. Claude transcripts never carry these
# types in their first 20 records (0 of 2,362 measured), and anything
# that is not a rollout falls through to measure(), whose whole-file
# conversation test keeps deciding NOT_TRANSCRIPT exactly as before.
ROLLOUT_TYPES = frozenset((
    "session_meta", "response_item", "event_msg", "turn_context",
    "world_state", "compacted", "inter_agent_communication_metadata"))


def is_rollout(path):
    for count, rec in enumerate(read_records(path)):
        if isinstance(rec, dict) and rec.get("type") in ROLLOUT_TYPES:
            return True
        if count >= 19:
            return False
    return False
```

- [ ] **Step 4: Thread harness and root through measure_outcome and measure**

Change `measure_outcome` to:

```python
def measure_outcome(path, harness, root):
    """One file's outcome: (MEASURED, row), (NOT_TRANSCRIPT, None) or
    (UNREADABLE, None). Never raises for an unreadable file - the thread pool
    in cmd_extract abandons its whole result stream on the first exception."""
    try:
        if is_rollout(path):
            row = measure_codex(path, root)
        else:
            row = measure(path, harness, root)
    except TranscriptUnreadable:
        return UNREADABLE, None
    return (MEASURED, row) if row is not None else (NOT_TRANSCRIPT, None)
```

Until Task 4 lands, add a stub directly above it so this task stays runnable:

```python
def measure_codex(path, root):
    """Implemented in the Codex-reducer task; a rollout is measured there.
    Until then it is not a transcript this pipeline can reduce."""
    return None
```

Change `measure(path)`'s signature to `measure(path, harness="claude", root=None)`
and its relative-path block (currently `path.relative_to(PROJECTS_DIR)`) to:

```python
    base = root if root is not None else claude_projects_dir()
    try:
        rel = path.relative_to(base).as_posix()
    except ValueError:
        rel = path.name
```

Row contents are unchanged in this task.

- [ ] **Step 5: Rework cmd_extract's walk — two separate anchored edits**

**Edit (a):** replace ONLY the three-line guard

```python
    if not PROJECTS_DIR.is_dir():
        print(f"no session directory at {PROJECTS_DIR}", file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)
```

with:

```python
    roots = list(transcript_roots())
    live = [(harness, root) for harness, root in roots if root.is_dir()]
    if not live:
        print("no session directory at any root: "
              + ", ".join(str(root) for _, root in roots), file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)
    absent = [harness for harness, root in roots if not root.is_dir()]
```

The `rebuild` / `rows` / stale-schema-detection / `state` block between the
guard and the walk (the ~15 lines from `rebuild = args.rebuild` through
`state = {} if rebuild else load_state()`) is **untouched** — it is the
mechanism that migrates the ledger across the schema bump.

**Edit (b):** replace ONLY the single line
`transcripts = sorted(PROJECTS_DIR.rglob("*.jsonl"))` with the candidate list
deduplicated by resolved path, first root wins:

```python
    candidates = {}
    duplicates = 0
    for harness, root in live:
        for path in sorted(root.rglob("*.jsonl")):
            key = str(path.resolve())
            if key in candidates:
                duplicates += 1
                continue
            candidates[key] = (path, harness, root)
    transcripts = list(candidates.values())
```

The stale/fingerprint loop iterates `for path, harness, root in transcripts:`
and appends `(path, harness, root, fingerprint)`. Track measured counts per
harness: initialise `measured_by_harness = Counter()` beside
`measured = not_transcripts = 0`, and in the `outcome == MEASURED` branch add
`measured_by_harness[row.get("harness") or harness] += 1`. The pool call
becomes:

```python
        for (path, harness, root, fingerprint), (outcome, row) in zip(
                stale, pool.map(
                    lambda item: measure_outcome(item[0], item[1], item[2]),
                    stale)):
```

Keying stays by `row["transcript"]` this task (harness lands on the row in
Task 3; the composite key change happens there). Extend the summary print:

```python
    print(f"transcripts: {len(transcripts)}  measured: {measured}  "
          f"unchanged: {unchanged}  not-transcripts: {not_transcripts}  "
          f"unreadable: {unreadable}")
    for harness in ("claude", "codex"):
        if harness in absent:
            print(f"  {harness}: root absent")
            continue
        in_ledger = sum(1 for r in rows.values()
                        if (r.get("harness") or "claude") == harness)
        print(f"  {harness}: {measured_by_harness[harness]} measured, "
              f"{in_ledger} in ledger")
    if duplicates:
        print(f"  duplicate paths skipped: {duplicates}")
```

- [ ] **Step 6: Run the new module, then the full suite**

Run the Task 2 module: expected PASS — `test_single_root_is_enough` shows as
an expected failure (its decorator, written in Step 1, comes off in Task 4).
Run the full suite: all green including Task 1's pins.

- [ ] **Step 7: Commit**

```bash
git add plugins/p/bin/retro.py plugins/p/tests/test_retro_extract.py
git commit -F - <<'MSG'
Resolve transcript roots per harness and detect rollouts structurally

Extract walks the Claude and Codex roots, deduplicates by resolved path
with Claude first, exits 2 only when every root is missing, and names
absent roots in its summary. Rollout files are recognised from their
first records and routed to a reducer stub.
MSG
```

---

### Task 3: Schema 7 — labelled rows, population split, per-counter denominators

**Files:**
- Modify: `plugins/p/bin/retro.py` — `SCHEMA_VERSION` (~135), `measure` row block (~871), `totals` (~1057), `split_population` (~1072), `cmd_pack` call sites (~1156-1187), `cmd_subagents` filter (~1388), `cmd_effect` split/usage (~1926-1968), `project_key` (~1332), `reporting_session_ids` (~1361)
- Modify: `plugins/p/tests/test_retro_measure.py` (update pins to the new fields)
- Create: `plugins/p/tests/test_retro_rows.py`

**Interfaces:**
- Produces: row fields `harness` (str), `population` (str: main|subagent|automation|unknown), `parent_session_id` (str), `project_key` (str), `ineligible` (list of COUNTERS names), `compacted` (bool); `totals(rows)` → `(Counter, Counter)` (sums, per-counter eligible-row counts); `split_population(rows)` → dict keyed `main|subagent|automation|unknown`; `project_key(row)` reads the stored column with the old rule as fallback.
- Consumes: Task 2's `measure(path, harness, root)`.

- [ ] **Step 1: Write failing tests**

Create `plugins/p/tests/test_retro_rows.py`:

```python
"""Schema 7 row fields and the population/eligibility machinery."""

import unittest

from test_retro_extract import load_retro


class Totals(unittest.TestCase):
    def test_totals_returns_sums_and_eligible_row_counts(self):
        retro = load_retro()
        rows = [
            {"tool_errors": 2, "turns": 5, "ineligible": []},
            {"tool_errors": 0, "turns": 7,
             "ineligible": ["tool_errors", "queued_prompts",
                            "permission_mode_changes"]},
        ]
        sums, eligible = retro.totals(rows)
        self.assertEqual(2, sums["tool_errors"])
        self.assertEqual(12, sums["turns"])
        self.assertEqual(1, eligible["tool_errors"])
        self.assertEqual(2, eligible["turns"])


class SplitPopulation(unittest.TestCase):
    def test_dict_keyed_by_population(self):
        retro = load_retro()
        # A row with no population key (pre-rename writer; cannot occur
        # after a rebuild, but the reader must not crash) counts as main.
        rows = [{"population": "main"}, {"population": "subagent"},
                {"population": "automation"}, {}]
        split = retro.split_population(rows)
        self.assertEqual(2, len(split["main"]))
        self.assertEqual(1, len(split["subagent"]))
        self.assertEqual(1, len(split["automation"]))
        self.assertEqual(0, len(split["unknown"]))


class ProjectKey(unittest.TestCase):
    def test_stored_column_wins_and_old_rule_is_fallback(self):
        retro = load_retro()
        self.assertEqual("cx-ab12cd34",
                         retro.project_key({"project_key": "cx-ab12cd34",
                                            "transcript": "2026/08/x.jsonl"}))
        self.assertEqual("projA",
                         retro.project_key({"transcript": "projA/s1.jsonl"}))


class SelfExclusion(unittest.TestCase):
    def test_codex_session_env_vars_are_read(self):
        import os
        from unittest import mock
        retro = load_retro()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "tid-1",
                                          "CODEX_SESSION_ID": "sid-1",
                                          "CLAUDE_CODE_SESSION_ID": "cid-1"}):
            ids = retro.reporting_session_ids(["extra-1"])
        self.assertEqual({"extra-1", "tid-1", "sid-1", "cid-1"}, ids)


if __name__ == "__main__":
    unittest.main()
```

Run: expect FAIL (`totals` returns one value; `split_population` returns a
tuple; `project_key` ignores the column; env vars unread).

- [ ] **Step 2: Implement the schema fields in measure()**

`SCHEMA_VERSION = 6` → `SCHEMA_VERSION = 7`.

In `measure()`'s row block: delete the single line
`"is_subagent": "subagents/" in rel,` and in its place put these six lines
(the rest of the dict, and the `schema`/`COUNTERS`/`ending` assignments after
it, stay exactly as they are):

```python
        "harness": harness,
        "population": "subagent" if "subagents/" in rel else "main",
        "parent_session_id": "",
        "project_key": rel.split("/")[0] if "/" in rel else (rel or "?"),
        "ineligible": [],
        "compacted": False,
```

(`automation` is Codex-only by design — spec D2.2 states the asymmetry.
`population_source` is a Codex-row field only; Claude rows do not carry it.)

- [ ] **Step 3: Rework totals, split_population, project_key, reporting_session_ids**

```python
def totals(rows):
    """Aggregate the rows handed in, and nothing else — plus, per counter,
    how many of those rows could observe it. The caller chooses the
    population; a rate divides each counter by its own eligible-row count,
    never by the row count (spec D4.1)."""
    agg = Counter()
    eligible_rows = Counter()
    for row in rows:
        ineligible = set(row.get("ineligible") or ())
        for key in COUNTERS:
            agg[key] += int(row.get(key) or 0)
            if key not in ineligible:
                eligible_rows[key] += 1
        agg["tokens_out"] += int(row.get("tokens_out") or 0)
    return agg, eligible_rows


def split_population(rows):
    """Rows bucketed by population. Only `main` carries per-session rates;
    the rest are spend. A row with no population field counts as main —
    unreachable after a schema-7 rebuild, but a reader must not crash."""
    split = {"main": [], "subagent": [], "automation": [], "unknown": []}
    for row in rows:
        split.get(row.get("population") or "main", split["unknown"]).append(row)
    return split
```

`project_key` becomes:

```python
def project_key(row):
    """Which project a row belongs to, as an opaque key. Stored on the row
    since schema 7 (Codex rows hash their redacted cwd — spec D2.4); the
    first path component stays as the fallback for the Claude layout."""
    stored = row.get("project_key")
    if stored:
        return str(stored)
    return str(row.get("transcript") or "?").split("/")[0]
```

`reporting_session_ids`: after the `CLAUDE_CODE_SESSION_ID` block add:

```python
    # The same tuple format-ctl reads (plugins/p/bin/format-ctl); adding a
    # harness means editing both.
    for name in ("CODEX_SESSION_ID", "CODEX_THREAD_ID"):
        value = os.environ.get(name) or ""
        if value:
            ids.add(value)
```

- [ ] **Step 4: Update every consumer of the old shapes**

All four `is_subagent` uses and both `split_population`/`totals` call sites:

1. `cmd_pack` (~1156): `main, sub = split_population(window)` →

```python
    split = split_population(window)
    prior_split = split_population(prior)
    main, sub = split["main"], split["subagent"]
    prior_main, prior_sub = prior_split["main"], prior_split["subagent"]
    now_t, now_eligible = totals(main)
    prev_t, _ = totals(prior_main)
    now_s, _ = totals(sub)
```

The per-session line divides per counter (guard zero):

```python
    if main:
        lines.append("Per session: " + ", ".join(
            f"{key} {now_t[key] / now_eligible[key]:.1f}"
            for key in COUNTERS if now_eligible[key]))
```

(The full per-harness pack layout is Task 5; this step only keeps the
current output correct against the new shapes. Append automation/unknown
spend counts to the subagent spend line: `split["automation"]` and
`split["unknown"]` lengths, printed only when nonzero.)

2. `cmd_subagents` (~1388): `r.get("is_subagent")` →
`(r.get("population") or "") == "subagent"`, and the skip filter becomes:

```python
    rows = [r for r in rows
            if r.get("session_id") not in skip
            and (r.get("parent_session_id") or "") not in skip]
```

with `dropped` computed from the same predicate before filtering.

3. `cmd_effect` (~1926): same `split_population` dict unpack as cmd_pack;
`totals` unpacks pairs everywhere it is called; automation rows are named:
after the split add `spend = len(split["subagent"]) + len(split["automation"])`
and include it in the printed population line. (Harness restriction is
Task 5.)

4. `measure()` row write — done in Step 2.

- [ ] **Step 5: Update Task 1's pins**

In `test_retro_measure.py`:

1. `measure_row()` becomes
   `return self.retro.measure(self.path, "claude", Path(self.tmp.name))` —
   with a real root, `transcript` is now `projA/s1.jsonl`, not the bare
   filename fallback.
2. The subagent-layout test calls
   `retro.measure(sub_path, "claude", Path(self.tmp.name))` and asserts
   `row["population"] == "subagent"`, `row["harness"] == "claude"`,
   `row["ineligible"] == []` (delete the `assertFalse(is_subagent)` line and
   its comment).
3. The main-session tests additionally assert `row["population"] == "main"`
   and `row["project_key"] == "projA"`.
4. The `CurrentShapes` class is REWRITTEN against the new contracts: its
   totals test unpacks the pair (`sums, eligible = retro.totals(...)`), and
   its split test asserts the dict shape — Task 3 Step 1's
   `test_retro_rows.py` carries the authoritative versions, so simply delete
   `CurrentShapes` here and note that `test_retro_rows.py` supersedes it.

- [ ] **Step 6: Run both new modules, then the full suite**

Expected: PASS everywhere. Fix any missed `totals()` single-value unpack the
suite surfaces — grep `totals(`: three statements but FIVE calls (lines 1158,
1159, 1946; the first and last lines each call it twice, and every call now
returns a pair).

- [ ] **Step 7: Commit**

```bash
git add plugins/p/bin/retro.py plugins/p/tests/test_retro_measure.py plugins/p/tests/test_retro_rows.py
git commit -F - <<'MSG'
Label ledger rows by harness and population under schema 7

Rows carry harness, population, parent_session_id, project_key,
ineligible, and compacted. totals returns per-counter eligible-row
counts and every rate divides by its own denominator;
split_population buckets four populations; self-exclusion reads the
Codex session variables format-ctl already names.
MSG
```

---

### Task 4: The Codex reducer

**Files:**
- Modify: `plugins/p/bin/retro.py` — replace the `measure_codex` stub; extend `MACHINE_PROMPT_OPENERS` (~634)
- Modify: `plugins/p/tests/fixtures.py` (rollout builder)
- Create: `plugins/p/tests/test_retro_codex.py`
- Remove: the `@unittest.expectedFailure` left in `test_retro_extract.py`

**Interfaces:**
- Produces: `measure_codex(path, root)` → row dict (same schema as `measure`); `fixtures.rollout_meta/rollout_user/rollout_assistant/rollout_call/rollout_output/rollout_event` builders; constants `CODEX_INELIGIBLE`, `CODEX_POLL_TOOLS`.
- Consumes: shared classifiers (`classify_user_turn`, `is_approval` via classifier, `signature`, `redact`, `parse_ts`), Task 2's root plumbing, Task 3's row fields.

- [ ] **Step 1: Add rollout builders to fixtures.py**

```python
def rollout_meta(when, thread_source="user", parent=None, cwd="/tmp/w",
                 branch="main"):
    payload = {"session_id": "sess-cx-1", "cwd": cwd, "cli_version": "1.0"}
    if thread_source is not None:
        payload["thread_source"] = thread_source
    if parent:
        payload["parent_thread_id"] = parent
    if branch:
        payload["git"] = {"branch": branch}
    return {"type": "session_meta", "timestamp": when, "payload": payload}


def rollout_user(text, when):
    return {"type": "response_item", "timestamp": when, "payload": {
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": text}]}}


def rollout_assistant(text, when):
    return {"type": "response_item", "timestamp": when, "payload": {
        "type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": text}]}}


def rollout_call(name, arguments, when, call_id="c1",
                 item_type="custom_tool_call"):
    return {"type": "response_item", "timestamp": when, "payload": {
        "type": item_type, "name": name, "call_id": call_id,
        "arguments": arguments}}


def rollout_output(when, call_id="c1", item_type="custom_tool_call_output"):
    return {"type": "response_item", "timestamp": when, "payload": {
        "type": item_type, "call_id": call_id,
        "output": [{"type": "input_text", "text": "ok"}]}}


def rollout_event(event_type, when, info=None):
    payload = {"type": event_type}
    if info is not None:
        payload["info"] = info
    return {"type": "event_msg", "timestamp": when, "payload": payload}


def token_count_event(when, input_tokens, cached, output, reasoning):
    return rollout_event("token_count", when, info={"total_token_usage": {
        "input_tokens": input_tokens, "cached_input_tokens": cached,
        "output_tokens": output, "reasoning_output_tokens": reasoning,
        "total_tokens": input_tokens + output + reasoning}})
```

- [ ] **Step 2: Write failing tests**

Create `plugins/p/tests/test_retro_codex.py` (uses `load_retro` from
`test_retro_extract`, `write_rollout`-style writing via json lines):

```python
"""measure_codex over synthetic rollouts — every counter mapping."""

import json
import tempfile
import unittest
from pathlib import Path

import fixtures as fx
from test_retro_extract import load_retro

T = "2026-08-01T12:00:00.000Z"
T2 = "2026-08-01T12:30:00.000Z"


def write(root, recs, rel="2026/08/01/rollout-a.jsonl"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in recs:
            fh.write(json.dumps(rec) + "\n")
    return path


class CodexReducer(unittest.TestCase):
    def setUp(self):
        self.retro = load_retro()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def measure(self, recs):
        return self.retro.measure_codex(write(self.root, recs), self.root)

    def test_identity_population_and_ineligible(self):
        row = self.measure([fx.rollout_meta(T), fx.rollout_user("hi", T),
                            fx.rollout_assistant("yo", T2)])
        self.assertEqual("codex", row["harness"])
        self.assertEqual("main", row["population"])
        self.assertEqual("sess-cx-1", row["session_id"])
        self.assertEqual("main", row["git_branch"])
        self.assertEqual("2026-08-01", row["date"])
        self.assertEqual(1800, row["duration_s"])
        self.assertEqual(sorted(["tool_errors", "queued_prompts",
                                 "permission_mode_changes"]),
                         sorted(row["ineligible"]))
        self.assertEqual("thread_source", row["population_source"])
        self.assertEqual([], row["eligible"])
        self.assertTrue(row["project_key"].startswith("cx-"))
        self.assertEqual("2026/08/01/rollout-a.jsonl", row["transcript"])

    def test_subagent_and_automation_populations(self):
        row = self.measure([fx.rollout_meta(T, thread_source="subagent",
                                            parent="parent-1"),
                            fx.rollout_user("go", T),
                            fx.rollout_assistant("done", T)])
        self.assertEqual("subagent", row["population"])
        self.assertEqual("parent-1", row["parent_session_id"])
        row = self.measure([fx.rollout_meta(T, thread_source="automation"),
                            fx.rollout_user("cron", T),
                            fx.rollout_assistant("ran", T)])
        self.assertEqual("automation", row["population"])

    def test_absent_thread_source_falls_back(self):
        row = self.measure([fx.rollout_meta(T, thread_source=None,
                                            parent="parent-2"),
                            fx.rollout_user("x", T),
                            fx.rollout_assistant("y", T)])
        self.assertEqual("subagent", row["population"])
        self.assertEqual("fallback", row["population_source"])
        # Absent with no parent id: unknown, NEVER main — an unclassified
        # file must not enter the per-session-rate population (spec D2.2).
        row = self.measure([fx.rollout_meta(T, thread_source=None),
                            fx.rollout_user("x", T),
                            fx.rollout_assistant("y", T)])
        self.assertEqual("unknown", row["population"])
        row = self.measure([fx.rollout_meta(T, thread_source="new-kind"),
                            fx.rollout_user("x", T),
                            fx.rollout_assistant("y", T)])
        self.assertEqual("unknown", row["population"])

    def test_every_machine_opener_filters_on_the_codex_path(self):
        retro = self.retro
        for opener in retro.MACHINE_PROMPT_OPENERS:
            if not opener.startswith("<"):
                continue   # the prose openers are Claude-transcript shapes
            body = opener + ' x="1">' if not opener.endswith(">") else opener
            row = self.measure([fx.rollout_meta(T),
                                fx.rollout_user(body + "\npayload", T),
                                fx.rollout_user("a real prompt", T),
                                fx.rollout_assistant("ok", T)])
            self.assertEqual(1, row["user_prompts"],
                             "opener not filtered: %r" % opener)

    def test_wrapper_tagged_messages_are_not_prompts(self):
        row = self.measure([
            fx.rollout_meta(T),
            fx.rollout_user("<task-notification>done</task-notification>", T),
            fx.rollout_user('<codex_internal_context kind="x">y', T),
            fx.rollout_user("a real prompt", T),
            fx.rollout_assistant("ok", T),
        ])
        self.assertEqual(1, row["user_prompts"])

    def test_correction_and_approval_classification(self):
        row = self.measure([
            fx.rollout_meta(T),
            fx.rollout_user("do the thing", T),
            fx.rollout_assistant("x" * 400, T),
            fx.rollout_user("no, wrong file", T),
            # Long enough to clear CORRECTION_MIN_PRIOR_CHARS (200); a
            # short prior turn makes "sure" classify as nothing.
            fx.rollout_assistant("z" * 400, T),
            fx.rollout_user("sure", T),
            fx.rollout_assistant("finished", T),
        ])
        self.assertEqual(1, row["correction_candidates"])
        self.assertEqual(1, row["approval_turns"])
        self.assertEqual(3, row["user_prompts"])

    def test_turns_and_tool_calls(self):
        row = self.measure([
            fx.rollout_meta(T),
            fx.rollout_user("go", T),
            fx.rollout_assistant("working", T),
            fx.rollout_call("exec", '{"cmd": "ls"}', T, call_id="c1"),
            fx.rollout_output(T, call_id="c1"),
            fx.rollout_event("web_search_end", T),
        ])
        # assistant message + call item = 2 turns; call + web_search = 2 tools
        self.assertEqual(2, row["turns"])
        self.assertEqual(2, row["tool_calls"])
        self.assertEqual("text", row["ending"])

    def test_repeat_calls_parse_args_and_skip_polls(self):
        row = self.measure([
            fx.rollout_meta(T), fx.rollout_user("go", T),
            fx.rollout_call("exec", '{"a": 1, "b": 2}', T, call_id="c1"),
            fx.rollout_output(T, call_id="c1"),
            fx.rollout_call("exec", '{"b": 2, "a": 1}', T, call_id="c2"),
            fx.rollout_output(T, call_id="c2"),
            fx.rollout_call("wait", '{"a": 1}', T, call_id="c3"),
            fx.rollout_call("wait", '{"a": 1}', T, call_id="c4"),
            fx.rollout_call("exec", "", T, call_id="c5"),
            fx.rollout_call("exec", "", T, call_id="c6"),
            fx.rollout_assistant("done", T),
        ])
        # key-order-insensitive repeat counted once; polls and empty args never
        self.assertEqual(1, row["repeat_calls"])

    def test_interrupts_and_endings(self):
        row = self.measure([fx.rollout_meta(T), fx.rollout_user("go", T),
                            fx.rollout_event("turn_aborted", T)])
        self.assertEqual(1, row["interrupts"])
        self.assertEqual("interrupted", row["ending"])
        row = self.measure([
            fx.rollout_meta(T), fx.rollout_user("go", T),
            fx.rollout_call("exec", '{"a": 1}', T, call_id="dangling"),
        ])
        self.assertEqual("unanswered", row["ending"])

    def test_tokens_with_reset_banking(self):
        row = self.measure([
            fx.rollout_meta(T), fx.rollout_user("go", T),
            fx.token_count_event(T, 100, 40, 50, 10),
            fx.token_count_event(T, 20, 5, 8, 2),   # reset: totals dropped
            fx.token_count_event(T, 30, 10, 12, 3),
            fx.rollout_assistant("done", T),
        ])
        # banked (100-40) + final (30-10) = 80
        self.assertEqual(80, row["tokens_in"])
        # banked (50+10) + final (12+3) = 75
        self.assertEqual(75, row["tokens_out"])
        self.assertEqual(50, row["cache_read"])   # banked 40 + final 10

    def test_skills_from_wrapper_blocks(self):
        row = self.measure([
            fx.rollout_meta(T), fx.rollout_user("go", T),
            fx.rollout_user("<skill>\n<name>p:doctor</name>\n<path>x</path>"
                            "\n</skill>", T),
            fx.rollout_assistant("ok", T),
        ])
        self.assertEqual(["doctor"], row["skills_used"])
        self.assertEqual(1, row["skill_runs"])

    def test_compacted_flag_and_first_meta_wins(self):
        row = self.measure([
            fx.rollout_meta(T, thread_source="user"),
            {"type": "compacted", "timestamp": T, "payload": {}},
            fx.rollout_meta(T, thread_source="subagent"),
            fx.rollout_user("go", T), fx.rollout_assistant("ok", T),
        ])
        self.assertTrue(row["compacted"])
        self.assertEqual("main", row["population"])

    def test_cwd_redaction(self):
        home_cwd = str(Path.home() / "work" / "thing")
        row = self.measure([fx.rollout_meta(T, cwd=home_cwd),
                            fx.rollout_user("go", T),
                            fx.rollout_assistant("ok", T)])
        self.assertNotIn(str(Path.home()), row["project"])
        self.assertIn("~", row["project"])

    def test_no_message_records_is_not_a_transcript(self):
        self.assertIsNone(self.measure([fx.rollout_meta(T)]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run to verify failure** — the stub returns `None` for
everything; nearly every test fails. Expected.

- [ ] **Step 4: Extend MACHINE_PROMPT_OPENERS**

The attributed-tag question is already measured (plan review ran the probe
over all 317 rollouts): exactly three tags never appear bare —
`codex_internal_context` (130), `in-app-browser-context` (57), and `image`
(4). Replace the `MACHINE_PROMPT_OPENERS` tuple with:

```python
# One list for both harnesses (spec D3). Codex wrapper tags whose every
# occurrence carries attributes enter as bracket-less prefixes; bare tags
# keep the closed form so a person merely quoting a tag name mid-sentence
# still counts. Anchored at the start deliberately. Measured over the
# rollout corpus: codex_internal_context, in-app-browser-context and image
# only ever appear as <tag attr...>.
MACHINE_PROMPT_OPENERS = (
    "<task-notification>", "<system-reminder>", "<command-name>",
    "<local-command-stdout>", "Base directory for this skill:",
    "This session is being continued", "Caveat: The messages below were generated",
    # Codex-only wrapper tags (spec Grounding 5).
    "<environment_context>", "<recommended_plugins>", "<skill>",
    "<turn_aborted>", "<codex_delegation>",
    "<codex_internal_context", "<in-app-browser-context", "<image",
)
```

- [ ] **Step 5: Implement measure_codex**

Replace the stub with the full reducer. Constants first, near the opener list:

```python
# Codex counter mapping (spec D3). Three counters have no rollout signal:
# no queue or permission-mode record exists, and a nonzero exec exit is
# information, not a harness refusal — matching the Claude rule.
CODEX_INELIGIBLE = ("tool_errors", "queued_prompts", "permission_mode_changes")
# Polls repeat identical arguments as their normal operation; counting them
# as repeats is the defect signature()'s docstring records for Claude,
# re-measured for rollouts: the naive rule flags 14.5% of calls, these
# exclusions bring it to 9.9%, and the residual is real repetition. The
# spec names exactly these two polls — the ones present in the corpus.
CODEX_POLL_TOOLS = frozenset(("wait", "wait_agent"))
# A call item is answered only by its own pair's output type (spec D3);
# note tool_search's output name drops "_call".
CODEX_CALL_PAIRS = {"function_call": "function_call_output",
                    "custom_tool_call": "custom_tool_call_output",
                    "tool_search_call": "tool_search_output"}
CODEX_TOOL_EVENTS = frozenset(("web_search_end", "image_generation_end"))
_SKILL_NAME = re.compile(r"<name>\s*([^<]+?)\s*</name>")


def _codex_text(payload):
    """Visible text of a rollout message payload."""
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(str(block.get("text") or "") for block in content
                     if isinstance(block, dict)
                     and block.get("type") in ("input_text", "output_text",
                                               "text"))


def _codex_population(meta):
    """(population, how it was decided). Absent thread_source maps to
    subagent when a parent id proves it, else to unknown — never to main:
    an unclassified file must not enter the population every per-session
    rate divides by. (All 50 absent-thread_source corpus files come from a
    single alpha CLI build on one day, none with a parent id.)"""
    source = meta.get("thread_source")
    if source == "user":
        return "main", "thread_source"
    if source in ("subagent", "automation"):
        return source, "thread_source"
    if source is None:
        if meta.get("parent_thread_id"):
            return "subagent", "fallback"
        return "unknown", "fallback"
    return "unknown", "thread_source"
```

Then the reducer:

```python
def measure_codex(path, root):
    """Reduce one Codex rollout to a metrics row.

    Same row schema as measure(); the judgment helpers are shared, the
    record walk is not — the two formats disagree on which signals exist,
    so the schema, not the record stream, is the contract (spec D3).
    """
    m = Counter()
    meta = None
    meta_disagrees = False
    first_ts = last_ts = None
    seen_sigs = set()
    skills = set()
    prior_assistant_chars = 0
    saw_message = False
    compacted = False
    aborted = False
    open_calls = set()   # (expected_output_type, call_id) pairs
    last_assistant_text = ""
    # Cumulative token totals reset mid-file in 5 of 317 corpus rollouts;
    # bank the running total at each reset and add the final run.
    bank = Counter()
    current = {}

    for rec in read_records(path):
        if not isinstance(rec, dict):
            continue
        ts = parse_ts(rec.get("timestamp"))
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        rtype = rec.get("type")
        payload = rec.get("payload")
        if rtype == "compacted":
            compacted = True
            continue
        if not isinstance(payload, dict):
            continue
        if rtype == "session_meta":
            if meta is None:
                meta = payload   # first meta wins (spec D2.2)
            elif payload.get("thread_source") != meta.get("thread_source"):
                meta_disagrees = True
            continue
        if rtype == "event_msg":
            etype = payload.get("type")
            if etype == "token_count":
                info = payload.get("info")
                total = (info or {}).get("total_token_usage") \
                    if isinstance(info, dict) else None
                if isinstance(total, dict):
                    if current and int(total.get("total_tokens") or 0) < \
                            int(current.get("total_tokens") or 0):
                        for key, value in current.items():
                            bank[key] += int(value or 0)
                    current = total
            elif etype == "turn_aborted":
                m["interrupts"] += 1
                aborted = True
            elif etype in CODEX_TOOL_EVENTS:
                m["tool_calls"] += 1
            continue
        if rtype != "response_item":
            continue
        itype = payload.get("type")
        if itype == "message":
            role = payload.get("role")
            body = _codex_text(payload)
            if role == "assistant":
                m["turns"] += 1
                saw_message = True
                last_assistant_text = body
                prior_assistant_chars += len(body)
            elif role == "user":
                saw_message = True
                opener = body.lstrip().startswith(MACHINE_PROMPT_OPENERS)
                if opener:
                    stripped = body.lstrip()
                    if stripped.startswith("<skill"):
                        match = _SKILL_NAME.search(body)
                        if match:
                            skills.add(match.group(1).split(":")[-1])
                            m["skill_runs"] += 1
                elif body.strip():
                    kind = classify_user_turn(body, prior_assistant_chars)
                    if kind == "interrupt":
                        # Matches the Claude reducer: an interrupt is not a
                        # prompt. The marker is Claude-shaped and expected
                        # to be absent from rollouts.
                        m["interrupts"] += 1
                    else:
                        m["user_prompts"] += 1
                        if kind == "correction":
                            m["correction_candidates"] += 1
                        elif kind == "approval":
                            m["approval_turns"] += 1
                    prior_assistant_chars = 0
            # developer-role messages never count (spec D3)
        elif itype in CODEX_CALL_PAIRS:
            m["turns"] += 1
            m["tool_calls"] += 1
            name = str(payload.get("name") or payload.get("tool_name") or "")
            call_id = str(payload.get("call_id") or payload.get("id") or "")
            if call_id:
                open_calls.add((CODEX_CALL_PAIRS[itype], call_id))
            raw = payload.get("arguments")
            if raw is None:
                raw = payload.get("input")
            parsed = None
            if isinstance(raw, str) and raw.strip():
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = raw
            elif raw:
                parsed = raw
            if parsed and name not in CODEX_POLL_TOOLS:
                sig = signature(parsed)
                key = (name, sig)
                if key in seen_sigs:
                    m["repeat_calls"] += 1
                seen_sigs.add(key)
        elif itype in CODEX_CALL_PAIRS.values():
            open_calls.discard(
                (itype, str(payload.get("call_id") or payload.get("id") or "")))

    if not saw_message:
        return None

    if current:
        for key, value in current.items():
            bank[key] += int(value or 0)
    meta = meta or {}
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    project = redact(str(meta.get("cwd") or ""))
    tokens_in = max(0, bank["input_tokens"] - bank["cached_input_tokens"])

    if last_assistant_text.strip():
        ending = "text"
    elif aborted:
        ending = "interrupted"
    elif open_calls:
        ending = "unanswered"
    else:
        ending = "silent"

    population, population_source = _codex_population(meta)
    row = {
        "transcript": rel,
        # harness names the transcript FORMAT; the walk's root decides
        # ownership and dedup only (spec D1.3).
        "harness": "codex",
        "population": population,
        "population_source": population_source,
        "parent_session_id": str(meta.get("parent_thread_id") or ""),
        "project_key": "cx-" + hashlib.sha1(
            project.encode("utf-8")).hexdigest()[:8],
        "ineligible": list(CODEX_INELIGIBLE),
        "compacted": compacted,
        "session_id": str(meta.get("session_id") or meta.get("id") or "")
                      or path.stem,
        "project": project,
        "git_branch": str(((meta.get("git") or {}).get("branch")) or "")
                      if isinstance(meta.get("git"), dict) else "",
        "cc_version": str(meta.get("cli_version") or ""),
        "date": first_ts.date().isoformat() if first_ts else "",
        "duration_s": int((last_ts - first_ts).total_seconds())
                      if first_ts and last_ts else 0,
        "tokens_in": tokens_in,
        # Visible and reasoning output are both spend (spec D3).
        "tokens_out": bank["output_tokens"] + bank["reasoning_output_tokens"],
        "cache_read": bank["cached_input_tokens"],
        "skills_used": sorted(skills),
        "schema": SCHEMA_VERSION,
        "ending": ending,
        # The seven mechanical-failure signals are Claude harness-refusal
        # markers a rollout never emits.
        "eligible": [],
        "meta_disagrees": meta_disagrees,
    }
    for key in COUNTERS:
        row[key] = m[key]
    for key in SUBAGENT_COUNTERS:
        row[key] = 0
    return row
```

- [ ] **Step 6: Extend the extract summary with the population lines**

In `cmd_extract`, after the per-harness ledger lines from Task 2, add:

```python
    codex_rows = [r for r in rows.values() if r.get("harness") == "codex"]
    if codex_rows:
        populations = Counter(r.get("population") for r in codex_rows)
        print("  codex populations: " + ", ".join(
            f"{name} {populations[name]}" for name in
            ("main", "subagent", "automation", "unknown") if populations[name]))
        fallbacks = sum(1 for r in codex_rows
                        if r.get("population_source") == "fallback")
        if fallbacks:
            print(f"  codex rows classified by fallback "
                  f"(no thread_source): {fallbacks}")
        disagreements = sum(1 for r in codex_rows if r.get("meta_disagrees"))
        if disagreements:
            print(f"  codex files whose metas disagree on thread_source: "
                  f"{disagreements}")
```

Also change the ledger key to the composite (spec D2.7): in `cmd_extract`,
`rows = {r["transcript"]: r ...}` becomes
`rows = {(r.get("harness") or "claude", r["transcript"]): r ...}` and
`rows[row["transcript"]] = row` becomes
`rows[(row.get("harness") or "claude", row["transcript"])] = row`.

- [ ] **Step 7: Two structure-only probes; record their answers as comments**

Spec D2.6 and the tool-events question both require a measured answer written
into the code. Run one probe over the real corpus printing counts only:

1. **Compaction shape:** for files containing a top-level `compacted`
   record, count `response_item` records before vs after it. Record the
   answer (append-preserving or rewriting) as a one-sentence comment beside
   `compacted = True` in `measure_codex`.
2. **Tool-event pairing:** count how many `patch_apply_end` and
   `mcp_tool_call_end` events have a same-file `custom_tool_call`/
   `function_call` item (match by any shared call/turn id key, or by
   count parity per file). If a family has paired call items, it stays OUT
   of `CODEX_TOOL_EVENTS` (counting both would double-count); if not, add
   it. Either way, extend the `CODEX_TOOL_EVENTS` comment with the counts
   and the decision — `web_search_end` (1,032) and `image_generation_end`
   (18) are already measured as unpaired.

- [ ] **Step 8: Remove the expectedFailure marker in test_retro_extract.py**
(single-root test now measures the rollout), run both modules, then the full
suite.

Expected: PASS everywhere.

One caution for anyone running commands mid-plan: do NOT run `extract`
against your real `RETRO_HOME` between Tasks 2 and 4 — the stub would
fingerprint every rollout as not-a-transcript and the finished reducer would
then skip them as unchanged. Task tests all use scratch `RETRO_HOME`s, and a
deployed ledger migrates cleanly (its schema-6 rows force a full rebuild,
which clears the fingerprint state). If it happens anyway, `extract
--rebuild` recovers.

- [ ] **Step 9: Commit**

```bash
git add plugins/p/bin/retro.py plugins/p/tests/fixtures.py plugins/p/tests/test_retro_codex.py plugins/p/tests/test_retro_extract.py
git commit -F - <<'MSG'
Reduce Codex rollouts into the ledger

measure_codex maps rollout records onto the shared row schema: shared
turn classifiers over plain user messages behind one machine-opener
list, poll-excluded repeat signatures, token-reset banking, call-id
endings, skill wrapper blocks, and population from thread_source with
the parent-id fallback. Three counters are ineligible by declaration
rather than silently zero.
MSG
```

---

### Task 5: Per-harness reporting

**Files:**
- Modify: `plugins/p/bin/retro.py` — module docstring (~1-35), `cmd_pack` (~1146), `installed_skills`/`cmd_skills` (~1225), `cmd_subagents` (~1378), `cmd_effect` (~1902 and its argparse ~2029)
- Create: `plugins/p/tests/test_retro_reporting.py`

**Interfaces:**
- Produces: `installed_skills()` → `{"claude": set, "codex": set}`; `cmd_pack` per-harness trend blocks; `--harness` flag on `effect`.
- Consumes: Task 3's `totals` pair and `split_population` dict; Task 4's rows.

- [ ] **Step 1: Write failing tests**

Create `plugins/p/tests/test_retro_reporting.py` — drive the commands through
`contextlib.redirect_stdout` over a synthetic ledger (write rows straight to
`RETRO_HOME/metrics.jsonl`, schema 7, then reload the module with `RETRO_HOME`
set, as `ExtractWalk` does):

```python
"""Per-harness output of pack, skills, and subagents."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from test_retro_extract import load_retro


def base_row(**over):
    today = datetime.now(timezone.utc).date().isoformat()
    row = {"transcript": "p/s.jsonl", "harness": "claude",
           "population": "main", "parent_session_id": "",
           "project_key": "p", "ineligible": [], "compacted": False,
           "session_id": "s", "project": "~", "git_branch": "", "cc_version": "",
           "date": today, "duration_s": 60, "tokens_in": 0, "tokens_out": 10,
           "cache_read": 0, "skills_used": [], "schema": 7, "ending": "text",
           "eligible": []}
    for key in ["turns", "user_prompts", "tool_calls", "tool_errors",
                "repeat_calls", "correction_candidates", "approval_turns",
                "interrupts", "permission_mode_changes", "queued_prompts",
                "skill_runs"]:
        row.setdefault(key, 0)
    for key in ["schema_rejected", "unread_before_write",
                "missing_path_target", "search_pattern_rejected",
                "invalid_tool_input", "workspace_target_outside",
                "workspace_shape_unverifiable"]:
        row[key] = 0
    row.update(over)
    return row


class Reporting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / "work"
        self.work.mkdir(parents=True)

    def load_with_ledger(self, rows):
        with open(self.work / "metrics.jsonl", "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        patcher = mock.patch.dict(os.environ, {"RETRO_HOME": str(self.work)})
        patcher.start()
        self.addCleanup(patcher.stop)
        return load_retro()

    def run_cmd(self, retro, func, **args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            func(mock.Mock(**args))
        return out.getvalue()

    def test_pack_prints_per_harness_blocks_and_unranked_note(self):
        retro = self.load_with_ledger([
            base_row(tool_errors=2),
            base_row(transcript="2026/08/x.jsonl", harness="codex",
                     project_key="cx-1", turns=3,
                     ineligible=["tool_errors", "queued_prompts",
                                 "permission_mode_changes"]),
        ])
        self.run_cmd(retro, retro.cmd_pack, days=7, sessions=8)
        pack = next(self.work.glob("pack-*.md")).read_text(encoding="utf-8")
        self.assertIn("### claude", pack)
        self.assertIn("### codex", pack)
        self.assertIn("not friction-ranked", pack)

    def test_skills_prints_per_harness_columns(self):
        retro = self.load_with_ledger([
            base_row(skills_used=["p:doctor"], skill_runs=1),
            base_row(transcript="2026/08/x.jsonl", harness="codex",
                     skills_used=["doctor"], skill_runs=1),
        ])
        text = self.run_cmd(retro, retro.cmd_skills, days=0)
        self.assertIn("| doctor | 1 | 1 |", text)
        self.assertIn("## Never fired — claude", text)
        self.assertIn("## Never fired — codex", text)

    def test_subagents_excludes_codex_from_failure_table_and_guards_empty(self):
        retro = self.load_with_ledger([
            base_row(population="subagent", eligible=["invalid_tool_input"]),
            base_row(transcript="2026/08/y.jsonl", harness="codex",
                     population="subagent", parent_session_id="pp",
                     ineligible=["tool_errors", "queued_prompts",
                                 "permission_mode_changes"]),
        ])
        text = self.run_cmd(retro, retro.cmd_subagents, days=0,
                            exclude_session=[])
        self.assertIn("codex subagent rows are excluded", text)
        self.assertIn("## How codex runs answered", text)
        # Excluding the parent id drops the codex row entirely (D4.6):
        # its per-harness sections must vanish, not just shrink.
        text = self.run_cmd(retro, retro.cmd_subagents, days=0,
                            exclude_session=["pp"])
        self.assertNotIn("## How codex runs answered", text)
        self.assertNotIn("codex subagent rows are excluded", text)


if __name__ == "__main__":
    unittest.main()
```

(The third test's final assertion is deliberately loose — assert on whatever
exact wording Step 4 chooses; tighten it there.)

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Rework cmd_pack (spec D4.2, D4.3)**

Replace the single trend table with one block per harness present in the
window, keeping the combined sessions line first. Shape:

```python
    lines = [f"# Evidence pack — last {args.days} days",
             f"Window: {start} to {now}. Main sessions: {len(main)} "
             f"(prior window: {len(prior_main)}).", "", "## Trends", ""]
    lines += [
        "Per-harness blocks; token and turn columns are never summed or "
        "compared across harnesses (the usage-accounting profile forbids "
        "cross-source token statistics, and a Codex `turn` is a structural "
        "analogue, not the same unit).", "",
        "Legacy correction and interrupt labels are candidate-sampler "
        "output only. They do not affect ranking and cannot justify a "
        "prompt, skill, rule, hook, agent, or process change.", ""]
    for harness in ("claude", "codex"):
        h_main = [r for r in main if (r.get("harness") or "claude") == harness]
        h_prior = [r for r in prior_main
                   if (r.get("harness") or "claude") == harness]
        if not h_main and not h_prior:
            continue
        h_now, h_eligible = totals(h_main)
        h_prev, _ = totals(h_prior)
        lines += [f"### {harness}", "",
                  "| signal | this window | prior | delta |",
                  "|---|---|---|---|"]
        table = [("sessions", len(h_main), len(h_prior))]
        table += [(key, h_now[key], h_prev[key])
                  for key in ["tokens_out"] + COUNTERS]
        for key, a, b in table:
            delta = "n/a" if not b else f"{(a - b) / b * 100:+.0f}%"
            label = key + " (candidate only)" if key in LEGACY_TURN_COUNTERS else key
            if key in COUNTERS and h_eligible[key] < len(h_main):
                label += f" ({h_eligible[key]} of {len(h_main)} rows observable)"
            lines.append(f"| {label} | {a} | {b} | {delta} |")
        lines.append("")
        if h_main:
            lines.append("Per session: " + ", ".join(
                f"{key} {h_now[key] / h_eligible[key]:.1f}"
                for key in COUNTERS if h_eligible[key]))
            lines.append("")
        h_sub = [r for r in sub if (r.get("harness") or "claude") == harness]
        h_sub_t, _ = totals(h_sub)
        lines.append(f"Subagent spend — {len(h_sub)} transcripts: "
                     + ", ".join(f"{key} {h_sub_t[key]}"
                                 for key in ["tokens_out"] + COUNTERS))
        for extra_name in ("automation", "unknown"):
            extra = [r for r in split[extra_name]
                     if (r.get("harness") or "claude") == harness]
            if extra:
                lines.append(f"{extra_name.capitalize()} spend — "
                             f"{len(extra)} transcripts.")
        lines.append("")
```

Ranking: before `ranked = sorted(...)`, restrict the friction-ranked list to
Claude (spec D4.3):

```python
    rankable = [r for r in main if (r.get("harness") or "claude") == "claude"]
    lines += ["## Candidate moments", ""]
    if len(main) > len(rankable):
        lines.append(f"_{len(main) - len(rankable)} main sessions are not "
                     f"friction-ranked - their harness emits no ranking "
                     f"signal. See the candidate-sampled section below._")
        lines.append("")
    ranked = sorted(rankable, key=friction_score, reverse=True)[:args.sessions]
```

The compacted note interpolates as a local, not an append-to-f-string:
compute `compacted = sum(1 for r in window if r.get("compacted"))` and
`compacted_note = (f" {compacted} compacted rollouts in window."
if compacted else "")` before the `lines = [...]` literal, and write the
Window line as
`f"Window: {start} to {now}. Main sessions: {len(main)} "
f"(prior window: {len(prior_main)}).{compacted_note}"`.

(The Codex candidate-sampled section and the approval moments land in
Task 5b, which owns `moments` and the section that follows the ranked list.)

(delete the old `lines += ["", "## Candidate moments", ""]`; compacted rows
count goes in the window header: after computing `window`, add
`compacted = sum(1 for r in window if r.get("compacted"))` and append
`f" {compacted} compacted rollouts in window." if compacted else ""` to the
Window line.)

- [ ] **Step 4: Rework installed_skills and cmd_skills (spec D4.4)**

```python
def installed_skills():
    """Per-harness skill inventories, by directory name. glob on a missing
    directory yields nothing, so no existence guards. The Claude half keeps
    CLAUDE_DIR deliberately — the spec sanctions adding Codex roots, not
    moving the Claude one."""
    codex_home = Path(os.environ.get("CODEX_HOME") or (HOME / ".codex"))
    claude = ({p.parent.name for p in (CLAUDE_DIR / "skills").glob("*/SKILL.md")}
              | {p.parent.name for p in
                 (CLAUDE_DIR / "plugins" / "cache").rglob("skills/*/SKILL.md")})
    codex = ({p.parent.name for p in (codex_home / "skills").glob("*/SKILL.md")}
             | {p.parent.name for p in
                (codex_home / "plugins").rglob("skills/*/SKILL.md")}
             | {p.parent.name for p in
                (HOME / ".agents" / "skills").glob("*/SKILL.md")})
    return {"claude": claude, "codex": codex}
```

`cmd_skills` counts per harness and prints one fired table with per-harness
columns and per-harness dormant lists:

```python
    used = {"claude": Counter(), "codex": Counter()}
    signal_files = Counter()
    for row in rows:
        harness = row.get("harness") or "claude"
        if row.get("skills_used"):
            signal_files[harness] += 1
        for name in row.get("skills_used") or []:
            used[harness][name.split(":")[-1]] += 1

    installed = installed_skills()
    window = f"last {args.days} days" if args.days else "all history"
    print(f"# Skill firing - {window}, {len(rows)} transcripts\n")
    print("## Fired\n")
    print("| skill | claude | codex | note |")
    print("|---|---|---|---|")
    every = Counter()
    for harness_counts in used.values():
        every.update(harness_counts)
    for name, _total in every.most_common():
        in_claude = name in installed["claude"]
        in_codex = name in installed["codex"]
        note = "" if (in_claude or in_codex) else "built-in command, or renamed"
        print(f"| {name} | {used['claude'][name] or ''} "
              f"| {used['codex'][name] or ''} | {note} |")
    print(f"\nCodex denominator: {signal_files['codex']} transcripts carried "
          f"any skill signal — a heuristic count, not authoritative "
          f"attribution (the source profile declares none exists).")
    dormant_total = 0
    for harness in ("claude", "codex"):
        dormant = sorted(installed[harness] - set(used[harness]))
        dormant_total += len(dormant)
        print(f"\n## Never fired — {harness} "
              f"({len(dormant)} of {len(installed[harness])} installed)")
        for name in dormant:
            print(f"         {name}")
    return EXIT_FLAGGED if dormant_total else EXIT_CLEAN
```

- [ ] **Step 5: Rework cmd_subagents (spec D4.5, D4.6)**

After the Task 3 filter changes: restrict the mechanical-failure table to
Claude rows and split the endings/length tables per harness with empty
guards. Concretely:

```python
    claude_rows = [r for r in rows if (r.get("harness") or "claude") == "claude"]
    codex_rows = [r for r in rows if r.get("harness") == "codex"]
```

The failure table header gains, when `codex_rows`:
`print(f"\n{len(codex_rows)} codex subagent rows are excluded from this table "
f"- their harness emits none of these signals.")` — and the table loops over
`claude_rows` (both the tallies and `concentration(claude_rows, key)`).

`no_answer` is computed ONCE, over all rows, BEFORE the per-harness loop —
left after a loop it would read only the last harness's counter, silently
changing both the printed figure and the exit code:

```python
    all_endings = Counter(r.get("ending") or "?" for r in rows)
    no_answer = all_endings.get("unanswered", 0) + all_endings.get("silent", 0)
    for harness, h_rows in (("claude", claude_rows), ("codex", codex_rows)):
        if not h_rows:
            continue
        print(f"\n## How {harness} runs answered\n")
        print("| ending | transcripts | share |")
        print("|---|---|---|")
        endings = Counter(r.get("ending") or "?" for r in h_rows)
        for name in ENDINGS:
            count = endings.get(name, 0)
            print(f"| {name} - {ENDING_MEANING[name]} | {count} | "
                  f"{count / len(h_rows) * 100:.1f}% |")
        turns = sorted(int(r.get("turns") or 0) for r in h_rows)
        print(f"\n## {harness} length\n\nturns: median "
              f"{quantile(turns, 0.5)}, p90 {quantile(turns, 0.90)}, "
              f"p95 {quantile(turns, 0.95)}, max {turns[-1]}.")
        for threshold in (100, 150, 200):
            print(f"  {sum(1 for t in turns if t >= threshold)} transcripts "
                  f"at {threshold} turns or more")
```

The "Failed to answer" paragraph prints once, after the loop, from
`no_answer`; the `structured`-explanation paragraph moves above the loop and
prints once. Delete the old single-table versions of both sections.

- [ ] **Step 6: Restrict cmd_effect and add --harness (spec D4.7)**

The command cannot see where a `--since` date came from — its no-argument
mode (`if not args.since: return print_candidates()`, the function's first
lines) only prints candidate dates for the operator to retype — so the
default is unconditional, not provenance-triggered:

1. Argparse block (~line 2029): `p_effect.add_argument("--harness",
   choices=("claude", "codex", "all"), default="claude", help="population "
   "to compare; defaults to claude because the rule-change dates this "
   "command anchors on come from Claude config history")`.
2. In `cmd_effect`, after the window rows are selected and before the
   split: `if args.harness != "all": rows = [r for r in rows if
   (r.get("harness") or "claude") == args.harness]`, then
   `print(f"population: harness={args.harness}, main sessions only; "
   f"subagent, automation and unknown rows are spend and excluded")`.
3. Have `print_candidates()`'s closing usage line mention
   `[--harness codex|all]`.
4. Per-counter denominators (spec D4.1 names `effect` as the second
   consumer): the totals line becomes `b, b_elig = totals(before)` /
   `a, a_elig = totals(after)` (each side its own statement now), and the
   per-session computation `sb, sa = b[key] / len(before), a[key] /
   len(after)` becomes division by `b_elig[key]` / `a_elig[key]`, skipping
   the counter's row entirely when either eligible count is 0.

- [ ] **Step 7: Retire PROJECTS_DIR and mark cmd_label's pools (spec D1.1, D5.4)**

Every remaining `PROJECTS_DIR` reader resolves through the call-time
function, and the constant is deleted so a divergent fifth use cannot
reappear:

1. `_moments` (~line 1118): `path = PROJECTS_DIR / row.get("transcript", "")`
   → `path = claude_projects_dir() / row.get("transcript", "")`. Without
   this, on a machine setting `CLAUDE_CONFIG_DIR`, extract writes rows
   relative to one root while the moment reader resolves under another and
   every Claude moment silently vanishes from packs.
2. `label_candidates` (~lines 1514, 1516) and `cmd_label`'s existence guard
   (~line 1716): same substitution — a local
   `projects = claude_projects_dir()` at the top of each function. The
   guard's behaviour (exit 2, Claude-only pools) is unchanged.
3. Delete the `PROJECTS_DIR = CLAUDE_DIR / "projects"` line. (`CLAUDE_DIR`
   stays — `installed_skills` and `cmd_rules` use it.)
4. `cmd_label`'s pools line (~line 1734): append
   ` (Claude transcripts only - classifier calibrated on Claude turns)` to
   the printed f-string.

- [ ] **Step 8: Update the module and read_records docstrings (spec D6.1)**

Module docstring — line 2: "derive workflow-friction metrics from Claude
Code and Codex session history." In the exit-codes block: `2  could not run
(no session directory at any root, no ledger)`. In the "Stdlib only"
paragraph: "a KeyError partway through a 5GB corpus loses the whole run."
Add one sentence to the ledger paragraph: "Rows are labelled by harness and
population; three counters are declared ineligible on Codex rows rather
than reported as zeros."

`read_records` docstring — the paragraph claiming `stopped-promises.py`
"resolves its transcript root differently — it honours the harness
config-directory variable and this does not" is now false; rewrite it to:
"Both readers honour the config-directory variable; the remaining
differences are gzip support and missing-root behaviour, which are that
script's own."

- [ ] **Step 9: Run the new module, then the full suite.** Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add plugins/p/bin/retro.py plugins/p/tests/test_retro_reporting.py
git commit -F - <<'MSG'
Split every retro report by harness

Packs print per-harness trend blocks with per-counter observable
denominators and a note counting sessions no ranking covers; skills reports
per-harness fired columns and dormant lists over per-harness installed
roots; the subagent lens confines mechanical failures to the harness
that emits them and splits endings and length; effect restricts to the
harness whose config history dated the comparison.
MSG
```

---

### Task 5b: Moments from both harnesses, liked examples included

Operator direction 2026-08-27 (spec Problem section, last paragraph): packs
quote the moments the operator liked (approvals) beside the frictional ones,
from both harnesses.

**Files:**
- Modify: `plugins/p/bin/retro.py` — `moments`/`_moments` (~1105), the
  moments section of `cmd_pack` (after Task 5's ranked list)
- Modify: `plugins/p/tests/test_retro_reporting.py`

**Interfaces:**
- Produces: `_moments_codex(row)`; `moments(row)` dispatching on
  `row["harness"]`; the pack's candidate-sampled Codex section.
- Consumes: Task 4's `_codex_text` and opener handling; Task 5's pack layout.

- [ ] **Step 1: Write failing tests**

Add to `test_retro_reporting.py` (reuse `base_row` and the rollout writer
from `test_retro_codex`; write the rollout under a fake `CODEX_HOME` inside
the temp dir and set the row's `transcript` to its root-relative path):

```python
    def test_pack_quotes_approvals_and_codex_moments(self):
        codex_home = Path(self.tmp.name) / "cx"
        import fixtures as fx
        from test_retro_codex import write, T
        rel = "2026/08/01/rollout-a.jsonl"
        write(codex_home / "sessions", [
            fx.rollout_meta(T), fx.rollout_user("do the thing", T),
            fx.rollout_assistant("x" * 400, T),
            fx.rollout_user("no, wrong file", T),
            fx.rollout_assistant("y" * 400, T),
            fx.rollout_user("perfect", T),
            fx.rollout_assistant("done", T),
        ], rel=rel)
        retro = self.load_with_ledger([
            base_row(transcript=rel, harness="codex", project_key="cx-1",
                     date="2026-08-01", correction_candidates=1,
                     approval_turns=1, user_prompts=3,
                     ineligible=["tool_errors", "queued_prompts",
                                 "permission_mode_changes"]),
        ])
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            self.run_cmd(retro, retro.cmd_pack, days=3650, sessions=8)
        pack = max(self.work.glob("pack-*.md")).read_text(encoding="utf-8")
        self.assertIn("candidate-sampled", pack)
        self.assertIn("**approval**", pack)
        self.assertIn("**correction**", pack)
```

Run: FAIL (`moments` returns [] for a codex row; no candidate-sampled
section).

- [ ] **Step 2: Add the approval kind to _moments**

In `_moments`, change `if kind in ("interrupt", "correction"):` to
`if kind in ("interrupt", "correction", "approval"):` and update the
docstring: "…the user turns that scored this session as frictional, plus the
approvals — the operator's liked behavior, by example — with the assistant
text immediately before each."

- [ ] **Step 3: Implement _moments_codex and the dispatch**

```python
def moments(row):
    """Redacted evidence for one session, or nothing at all if its
    transcript will not read or its harness has no reader."""
    try:
        if (row.get("harness") or "claude") == "codex":
            return _moments_codex(row)
        return _moments(row)
    except TranscriptUnreadable:
        return []


def _moments_codex(row):
    """The Codex counterpart of _moments: wrapper-tagged and empty user
    messages skipped with the same opener list, prior accumulated from
    assistant messages and reset on each counted user turn."""
    path = codex_sessions_dir() / row.get("transcript", "")
    if not path.is_file():
        return []
    out = []
    prior = ""
    for rec in read_records(path):
        if not isinstance(rec, dict) or rec.get("type") != "response_item":
            continue
        payload = rec.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "message":
            continue
        role = payload.get("role")
        body = _codex_text(payload)
        if role == "assistant":
            prior += body
        elif role == "user":
            if body.lstrip().startswith(MACHINE_PROMPT_OPENERS) \
                    or not body.strip():
                continue
            kind = classify_user_turn(body, len(prior))
            if kind in ("interrupt", "correction", "approval"):
                out.append({
                    "at": rec.get("timestamp") or "",
                    "kind": kind,
                    "said": redact(body.strip())[:400],
                    "after": redact(prior.strip()[-300:]),
                })
            prior = ""
        if len(out) >= MOMENTS_PER_SESSION:
            break
    return out
```

(Note `prior` accumulates across consecutive assistant messages — matching
`measure_codex`'s `prior_assistant_chars` — where the Claude `_moments`
replaces it per record; that difference mirrors each format's record
granularity and is deliberate.)

- [ ] **Step 4: Append the candidate-sampled Codex section in cmd_pack**

After the ranked-Claude loop (before `out_path = ...`), add (spec D4.3):

```python
    sampled = sorted(
        (r for r in main if r.get("harness") == "codex"
         and (int(r.get("correction_candidates") or 0)
              + int(r.get("interrupts") or 0)
              + int(r.get("approval_turns") or 0))),
        key=lambda r: (int(r.get("correction_candidates") or 0)
                       + int(r.get("interrupts") or 0)
                       + int(r.get("approval_turns") or 0)),
        reverse=True)[:args.sessions]
    if sampled:
        lines += ["## Codex moments — candidate-sampled, not ranked", "",
                  "Selected by candidate signals (a use the legacy rubric "
                  "allows); nothing here is a friction ranking.", ""]
    for row in sampled:
        evidence = moments(row)
        if not evidence:
            # A moved or unreadable rollout must not print a headed block
            # with nothing under it (spec D4.3).
            continue
        lines.append(f"### {row['date']} · {row.get('project') or '?'} · "
                     f"branch `{row.get('git_branch') or '-'}`")
        for moment in evidence:
            lines.append("")
            lines.append(f"- **{moment['kind']}** at {moment['at']}")
            lines.append(f"  - assistant, just before: _{moment['after']}_")
            lines.append(f"  - user said: **{moment['said']}**")
        lines.append("")
```

Apply the same guard to the ranked-Claude loop in `cmd_pack`: capture
`evidence = moments(row)` before appending that loop's `###` heading, and
`continue` on empty — same reason, same spec clause.

- [ ] **Step 5: Run the module, then the full suite.** Expected: PASS,
including Step 1's new test.

- [ ] **Step 6: Commit**

```bash
git add plugins/p/bin/retro.py plugins/p/tests/test_retro_reporting.py
git commit -F - <<'MSG'
Quote liked and frictional moments from both harnesses

Approval turns join corrections and interrupts as quotable moments -
the operator's liked behavior by example. Codex sessions enter the
moments section by candidate sampling under its allowed rubric use,
read by a rollout-format moment walker through the shared redaction.
MSG
```

---

### Task 6: Docs made false by the change (parallel with Tasks 2-5b)

**Files:**
- Modify: `plugins/p/EVALUATION.md`, `plugins/p/skills/finding-friction-in-recent-sessions/SKILL.md`, `plugins/p/skills/auditing-workflow-rules-against-behavior/SKILL.md`, `plugins/p/bin/stopped-promises.py` (docstring only), `plugins/p/bin/cache_ttl.py` (docstring only)

**Interfaces:** none — prose only. Do not touch `retro.py` (Lane A owns it).

- [ ] **Step 1: EVALUATION.md** — add two sentences where the retro commands
are described: "The measurement pipeline reads both harnesses' transcripts;
the evaluation adapters exclude non-user Codex threads, so an evaluation
report and a pack may legitimately disagree about corpus size. They may also
disagree on token totals for the handful of rollouts whose cumulative token
counter resets mid-file: the ledger banks across resets, the adapter takes
the last count."
- [ ] **Step 2: friction skill** — in the reading-the-signals table's
paragraph, add: "On a mixed corpus, `tool_errors`, `queued_prompts`, and
`permission_mode_changes` are not observable for Codex rows; the pack marks
those lines with the observable share, and per-session rates divide by it."
- [ ] **Step 3: audit skill** — where the `skills` command's output is
described, note the per-harness fired columns and per-harness never-fired
lists, and that a Codex-only skill is named with its harness.
- [ ] **Step 4: stopped-promises.py docstring** — append one line: "Reads
Claude transcript format only; Codex rollouts are a different format this
reader does not parse."
- [ ] **Step 5: cache_ttl.py docstring** — append the same one line.
- [ ] **Step 6: Run the full suite (README/CLAUDE mirroring tests must stay
green), commit:**

```bash
git add plugins/p/EVALUATION.md plugins/p/skills/finding-friction-in-recent-sessions/SKILL.md plugins/p/skills/auditing-workflow-rules-against-behavior/SKILL.md plugins/p/bin/stopped-promises.py plugins/p/bin/cache_ttl.py
git commit -F - <<'MSG'
Update docs the Codex ingestion makes false

Names the eval-versus-pack corpus asymmetry, the per-harness skills
output, the Codex-ineligible counters in the friction table, and the
two deliberately Claude-only transcript readers.
MSG
```

---

### Task 7: Release metadata (parallel with Tasks 2-6)

**Files:**
- Modify: `plugins/p/.claude-plugin/plugin.json`, `plugins/p/.codex-plugin/plugin.json`, `.claude-plugin/marketplace.json` — `"version": "1.9.0"` → `"1.10.0"`
- Modify: `plugins/p/tests/test_universal_plugin.py` — the `1.9.0` literal at ~line 37 and ~line 79, and rename `test_release_metadata_is_synchronized_at_1_9_0` → `test_release_metadata_is_synchronized_at_1_10_0`

- [ ] **Step 1: Bump all three version fields.**
- [ ] **Step 2: Update the test's two literals and its method name.**
- [ ] **Step 3: Run `sh plugins/p/bin/p-validate` and the full suite.** Expected: `RESULT: 3 passed`, suite OK.
- [ ] **Step 4: Commit:**

```bash
git add plugins/p/.claude-plugin/plugin.json plugins/p/.codex-plugin/plugin.json .claude-plugin/marketplace.json plugins/p/tests/test_universal_plugin.py
git commit -F - <<'MSG'
Release 1.10.0 metadata for Codex measurement ingestion
MSG
```

---

### Task 8: End-to-end gates

**Files:** none created; runs the contract.

- [ ] **Step 1: Full validation contract**

```bash
sh plugins/p/bin/python-launcher -B -m unittest discover -s plugins/p/tests -t plugins/p/tests
sh plugins/p/bin/python-launcher -B plugins/p/bin/format-e2e
sh plugins/p/bin/p-validate
sh plugins/p/bin/repo-privacy-audit -C .
git diff --check
```

Expected: all clean.

- [ ] **Step 2: Local end-to-end against the real corpora, scratch ledger**

```bash
RETRO_E2E="$(mktemp -d)/retro-e2e"
RETRO_HOME="$RETRO_E2E" sh plugins/p/bin/python-launcher plugins/p/bin/retro.py extract
```

Expected: the summary prints both harness lines; Codex measured ≥ 200; the
codex populations line shows a nonzero `main`. Then, same shell so the
variable survives:

```bash
RETRO_HOME="$RETRO_E2E" sh plugins/p/bin/python-launcher plugins/p/bin/retro.py skills --days 30
```

Expected: the fired table has claude and codex columns; per-harness dormant
lists print. These are the goal's evidence gates — capture both outputs.

- [ ] **Step 3: Commit nothing (read-only task); record the two outputs in
the session's report.**

---

## Self-review notes (already applied)

1. Spec coverage: D1 → Task 2 plus Task 5 Step 7 (constant retirement);
   D2 → Tasks 3-4; D3 → Tasks 4 and 5b (moments); D4.1 → Task 3 and Task 5
   Step 6.4 (`effect`); D4.2 → Task 5; D4.3 → Tasks 5 and 5b; D4.4-4.7 →
   Task 5; D4.8 → Task 2 Step 5 + Task 4 Step 6; D5.1-5.3/5.5 → Task 6 and
   no-op scoping; D5.4 → Task 5 Step 7.4; D6 → Tasks 5 (Step 8) and 6; the
   operator's liked-examples direction → Task 5b; Verification 1 → Task 1,
   2 → Tasks 2-5b, 3 → Tasks 7-8, 4 → Task 8. Lane D → Task 7.
2. The `meta_disagrees` field is defined in Task 4 Step 5's note and consumed
   in Step 6 — one name, one writer, one reader.
3. `installed_skills()`'s return-shape change (set → dict) has exactly one
   caller (`cmd_skills`), reworked in the same task.
