# Fix 1 — three transcript outcomes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `extract` reports a file that read fine but holds no conversation
separately from a file whose bytes would not read, retries the second kind, and
exits 1 when one occurs.

**Architecture:** `read_records` stops swallowing `OSError` and raises
`TranscriptUnreadable` instead, so both of its callers can tell a read failure
from an empty stream. `measure` keeps returning a row (or `None` for a file with
no conversation in it) and a thin `measure_outcome` wrapper turns the two signals
into one of three named outcomes. `moments` gets an equally thin wrapper so a
pack over hundreds of sessions cannot die on one bad file. `cmd_extract` counts
the three outcomes into three printed buckets that sum to the number of files
walked, stops writing a fingerprint for failures, and returns an exit code.

**Tech Stack:** Python 3 standard library only. No test framework, no
dependency, no build step.

**Spec:** `docs/plans/2026-08-18-retro-measurement-fixes.md`, section "Fix 1 —
separate 'not a transcript' from 'unreadable'", including all six numbered
constraints. Read it before Task 1.

## Global Constraints

- Stdlib only. No dependency, no build step, no daemon.
- Nothing personally identifying, secret, or belonging to another project enters
  any file, example, comment, or commit message — see `CLAUDE.md` at the repo
  root. No absolute path from this machine appears in a tracked file.
- Exit codes: `0` ran clean and flagged nothing, `1` ran clean and flagged
  something, `2` could not run.
- `plugins/*/bin/*` and `*.md` are `text eol=lf` in `.gitattributes`. If you
  write the file from a script rather than an editor, pass `newline="\n"`;
  otherwise the whole file re-writes as CRLF.
- Commit messages go through `git commit -F -` fed by a single-quoted heredoc,
  never a double-quoted `-m` string, and contain no backticks.
- Verification is read-only against the live corpus. Never write anywhere under
  the harness configuration directory, and never run `extract` against the
  default work directory — always point `RETRO_HOME` at a scratch directory.
- The ledger contract holds: this fix redefines no counter and forces no
  `--rebuild` (verified below — the ledger is byte-identical).

---

## Measured baseline (2026-08-19, live corpus, read-only)

Every number below was measured today, not recalled. They are what Task 3 checks
against.

| Fact | Value |
|---|---|
| Files walked under the transcript root | 1,920 |
| Rows the ledger holds | 1,898 |
| Files `measure()` currently rejects | 22 |
| Of those 22: opened and decoded cleanly | 22 (zero genuine read failures) |
| Files whose `stat()` fails | 0 |
| Files with no record of type `user` or `assistant` | 22 — the same 22 |
| Files the structural rule would classify differently from the current rule | 0 in either direction |
| Full `--rebuild` wall clock | 5.5 s |
| `extract` exit code, always | 0 |

Two consequences worth knowing before you start:

1. **The structural rule reproduces today's rejection set exactly.** No row
   enters or leaves the ledger because of this change, so the ledger must come
   out byte-identical. Task 3 checks that, and a difference is a defect.
2. **Constraint 5's non-summing counts are latent, not visible.** The spec's
   verification table says the printed counts do not sum today. On this corpus
   they do — 1,898 + 0 + 22 = 1,920 — because no file's `stat()` currently
   fails. The hole is real (a file that fails `stat()` lands in no bucket) and
   the fix still closes it; the synthetic corpus in Task 2 is what demonstrates
   it, because the live corpus cannot.

---

## Files

- **Modify:** `plugins/retro/bin/retro.py` — every code change in this plan.
- **Modify:** `docs/plans/2026-08-12-retro-design.md:156,170` — the verification
  row and the open question that this fix answers.
- **Modify:** `docs/plans/2026-08-18-retro-measurement-fixes.md` — append the
  measured results under Verification.
- **Modify:** the three `plugins/retro/skills/*/SKILL.md` files — one sentence
  each, because `extract` can now exit nonzero in a procedure that runs it.
- **Create (scratch, never committed):** `check_outcomes.py` in a scratch
  directory outside the repo — the synthetic-corpus harness.

### Functions changed in `retro.py`

Line numbers are as of the current file; a sibling fix landing first shifts them.

| Function | Line | What changes |
|---|---|---|
| module docstring | 15-19 | exit-code list gains the unreadable-transcript case |
| `TranscriptUnreadable` (new) | before 205 | the failure signal |
| `read_records` | 205-221 | raises instead of returning an empty stream |
| `MEASURED` / `NOT_TRANSCRIPT` / `UNREADABLE` (new) | after 221 | the outcome vocabulary |
| `measure` | 224-331 | docstring, a `conversation` counter, and the terminal condition |
| `measure_outcome` (new) | after 331 | maps row-or-`None`-or-raise to one of three outcomes |
| `cmd_extract` | 343-394 | three buckets, no fingerprint for failures, an exit code |
| `moments` | 445-471 | split into a wrapper that cannot raise and `_moments` |

---

## Sibling merge map

Three sibling fixes are being planned against this same file. Nothing here is
file-disjoint, so no lane can run concurrently with another — one file, one
implementer at a time, and the merges are ordered by hand.

**Recommended order: this fix first, then fix 3, then fixes 2 and 4.** Fix 3
rewrites the body of `read_records` to read gzip; landing this fix first means
fix 3 rebases onto a `read_records` that already raises, instead of the merge
having to reconstruct the failure signal inside a rewritten reader.

| Where | This fix does | Sibling likely to collide | Risk |
|---|---|---|---|
| `read_records` body, the `except OSError` clause | converts the swallow into a raise | **Fix 3** replaces the opener for gzip — same 15 lines | **High** — expect a hand merge |
| `cmd_extract`, the file walk and the stat loop | adds the unreadable bucket to the `stat()` failure path, renames `skipped` to `unchanged` | **Fix 3** widens the glob and walks extra roots — same block | **High** |
| `cmd_extract`, the printed summary line | adds a `not-transcripts:` count | **Fix 3** adds a duplicate-session overlap count to the same line | **High** — both edits belong in the final line; merge by hand |
| `cmd_extract`, the thread-pool loop | consumes `(outcome, row)` and skips fingerprinting failures | **Fix 3** may key state or rows by root | Medium |
| `measure`, the tail where `rel` is derived | replaces the two lines immediately above it (`if session_id is None and not m`) | **Fix 3** rewrites the `rel`/`is_subagent` derivation directly below | Medium — adjacent hunks |
| `measure`, top of the record loop | inserts a 5-line `conversation` counter after `rtype = rec.get("type")` | **Fix 4** adds subagent counters in the same loop; **Fix 2b** edits the `user` branch ~50 lines below | Medium |
| `moments` | renames the function to `_moments` and adds a wrapper above it | **Fix 2c** edits the loop body ~8 lines below the def; **Fix 3** changes the path resolution 2 lines below the def | Medium-high for fix 3, low for fix 2c |
| module docstring | edits the exit-code list | **Fix 2d** must edit the "exactly one place" sentence four lines above | Medium |
| the spec's Verification table | appends a subsection rather than editing rows | all three siblings record results in the same section | Medium — append, never restructure |

Two semantic notes for whoever merges fix 3 on top of this:

1. `gzip.BadGzipFile` subclasses `OSError`, so a corrupt archive already raises
   `TranscriptUnreadable` through the clause this fix adds. A **truncated** gzip
   stream raises `EOFError`, which does not — fix 3 should widen the clause to
   `except (OSError, EOFError)` when it adds the reader, or truncated archives
   will crash a run instead of counting as unreadable.
2. `moments()` resolves `PROJECTS_DIR / row["transcript"]` inside `_moments`
   after this fix, not inside `moments`. Fix 3's root resolution goes in
   `_moments`.

---

## Task 1: Report three outcomes instead of two

**Files:**
- Modify: `plugins/retro/bin/retro.py`
- Create: `check_outcomes.py` in a scratch directory outside the repo

**Interfaces:**
- Produces: `TranscriptUnreadable(Exception)`; module constants `MEASURED`,
  `NOT_TRANSCRIPT`, `UNREADABLE` (the strings `"measured"`, `"not-transcript"`,
  `"unreadable"`); `measure_outcome(path) -> (str, dict | None)`;
  `read_records(path)` now raises `TranscriptUnreadable`; `moments(row)` never
  raises; `_moments(row)` holds what `moments` used to do.
- Consumes: nothing from another task.

- [ ] **Step 1: Create the scratch workspace**

The harness and every verification run write here, never into the repo and never
into the default work directory.

```bash
SCRATCH="$(python -c 'import tempfile;print(tempfile.mkdtemp(prefix="retro-fix1-"))')"
echo "$SCRATCH"
```

Keep `$SCRATCH` for the whole plan; if the shell is lost, make a new one and
re-create the harness file.

- [ ] **Step 2: Write the failing harness**

Write this to `$SCRATCH/check_outcomes.py`. It builds a four-file synthetic
corpus in its own temp directory, points the module's transcript root at it, and
never touches the real corpus, the default work directory, or prints a path.

```python
"""Verification harness for retro's three transcript outcomes.

Builds a synthetic corpus in a temp directory and exercises measure_outcome,
moments, extract and pack against it. Never reads the real corpus, never writes
to the default work directory, and never prints a path.

Usage: python check_outcomes.py <path-to-retro.py> [section ...]
Sections: outcomes moments extract pack   (default: all four)
"""
import argparse
import builtins
import importlib.util
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = Path(sys.argv[1]).resolve()
WANTED = sys.argv[2:] or ["outcomes", "moments", "extract", "pack"]
SANDBOX = Path(tempfile.mkdtemp(prefix="retro-check-"))
os.environ["RETRO_HOME"] = str(SANDBOX / "work")

spec = importlib.util.spec_from_file_location("retro_under_test", SCRIPT)
retro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retro)

TODAY = datetime.now(timezone.utc).date().isoformat()
CORPUS = SANDBOX / "projects"
(CORPUS / "proj").mkdir(parents=True)
retro.PROJECTS_DIR = CORPUS
GOOD = CORPUS / "proj" / "good.jsonl"
JOURNAL = CORPUS / "proj" / "journal.jsonl"
LOCKED = CORPUS / "proj" / "locked.jsonl"
NOSTAT = CORPUS / "proj" / "nostat.jsonl"


def jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


jsonl(GOOD, [
    {"type": "user", "sessionId": "s1", "cwd": "/w", "timestamp": f"{TODAY}T10:00:00Z",
     "message": {"role": "user", "content": "please do the thing"}},
    {"type": "assistant", "sessionId": "s1", "timestamp": f"{TODAY}T10:00:05Z",
     "message": {"role": "assistant", "content": [{"type": "text", "text": "x" * 500}]}},
    {"type": "user", "sessionId": "s1", "timestamp": f"{TODAY}T10:00:09Z",
     "message": {"role": "user", "content": "no, revert that"}},
])
# reads fine, holds no record of type user or assistant
jsonl(JOURNAL, [{"type": "progress", "note": "step 1"}, {"type": "progress", "note": "step 2"}])
# a directory wearing a transcript name: stat() succeeds, open() fails
LOCKED.mkdir()
# the stat() call on this one fails
jsonl(NOSTAT, [{"type": "user", "message": {"content": "hi"}}])

_real_stat = Path.stat


def _stat(self, *a, **k):
    if self.name == "nostat.jsonl":
        raise PermissionError("simulated stat failure")
    return _real_stat(self, *a, **k)


class BreakingFile:
    """Hands back one record, then fails - the mid-iteration case."""

    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        yield from self.lines
        raise OSError("simulated failure partway through")


_real_open = builtins.open


def breaking_open(file, *a, **k):
    if str(file).endswith("good.jsonl"):
        return BreakingFile([json.dumps(
            {"type": "user", "sessionId": "s1", "message": {"content": "hi"}}) + "\n"])
    return _real_open(file, *a, **k)


failures = []


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + ("" if ok else f"   [{detail}]"))
    if not ok:
        failures.append(label)


def run_extract(rebuild):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = retro.cmd_extract(argparse.Namespace(rebuild=rebuild))
    line = [l for l in buf.getvalue().splitlines() if l.startswith("transcripts:")][0]
    parts = line.split()
    print("  " + line)
    return code, {parts[i].rstrip(":"): int(parts[i + 1]) for i in range(0, len(parts) - 1, 2)}


def section_outcomes():
    check("a transcript measures", retro.measure_outcome(GOOD)[0] == retro.MEASURED,
          retro.measure_outcome(GOOD)[0])
    check("a file with no user or assistant record is not a transcript",
          retro.measure_outcome(JOURNAL) == (retro.NOT_TRANSCRIPT, None),
          retro.measure_outcome(JOURNAL)[0])
    check("a file that will not open is unreadable",
          retro.measure_outcome(LOCKED) == (retro.UNREADABLE, None),
          retro.measure_outcome(LOCKED)[0])
    builtins.open = breaking_open
    try:
        outcome, row = retro.measure_outcome(GOOD)
        check("a failure partway through reports unreadable",
              outcome == retro.UNREADABLE, outcome)
        check("no partial row survives a failure partway through", row is None, row)
    finally:
        builtins.open = _real_open


def section_moments():
    row = {"transcript": "proj/good.jsonl", "date": TODAY, "correction_turns": 3,
           "is_subagent": False}
    check("moments reads a healthy transcript", len(retro.moments(row)) > 0)
    builtins.open = breaking_open
    try:
        check("moments returns nothing rather than raising", retro.moments(row) == [])
    finally:
        builtins.open = _real_open


def section_extract():
    code, c = run_extract(True)
    check("four files walked", c.get("transcripts") == 4, c)
    check("one measured", c.get("measured") == 1, c)
    check("one not-transcript", c.get("not-transcripts") == 1, c)
    check("two unreadable", c.get("unreadable") == 2, c)
    check("buckets sum to the file total",
          sum(c.get(k, 0) for k in ("measured", "unchanged", "not-transcripts", "unreadable"))
          == c.get("transcripts"), c)
    check("exit 1 with an unreadable file", code == retro.EXIT_FLAGGED, code)
    state = json.loads((SANDBOX / "work" / "state.json").read_text(encoding="utf-8"))
    names = sorted(Path(k).name for k in state)
    check("neither unreadable file is fingerprinted",
          "locked.jsonl" not in names and "nostat.jsonl" not in names, names)
    check("the not-transcript is fingerprinted", "journal.jsonl" in names, names)

    code, c = run_extract(False)
    check("unreadable files are retried, not carried as unchanged",
          c.get("unreadable") == 2, c)
    check("measured and not-transcript are now unchanged", c.get("unchanged") == 2, c)
    check("a second run still exits 1", code == retro.EXIT_FLAGGED, code)


def section_pack():
    row = {"transcript": "proj/good.jsonl", "is_subagent": False, "session_id": "s1",
           "project": "w", "git_branch": "b", "cc_version": "1", "date": TODAY,
           "duration_s": 9, "tokens_in": 0, "tokens_out": 0, "cache_read": 0,
           "skills_used": [], "turns": 1, "user_prompts": 2, "tool_calls": 0,
           "tool_errors": 0, "tool_retries": 0, "correction_turns": 3, "interrupts": 0,
           "permission_mode_changes": 0, "queued_prompts": 0, "skill_runs": 0}
    (SANDBOX / "work").mkdir(parents=True, exist_ok=True)
    (SANDBOX / "work" / "metrics.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    builtins.open = breaking_open
    try:
        with redirect_stdout(io.StringIO()):
            retro.cmd_pack(argparse.Namespace(days=7, sessions=8))
        check("pack completes with an unreadable transcript in the window", True)
    finally:
        builtins.open = _real_open


SECTIONS = {"outcomes": section_outcomes, "moments": section_moments,
            "extract": section_extract, "pack": section_pack}

Path.stat = _stat
try:
    for name in WANTED:
        print(f"== {name} ==")
        try:
            SECTIONS[name]()
        except Exception as exc:
            check(f"{name}: section ran to completion", False, f"{type(exc).__name__}: {exc}")
finally:
    Path.stat = _real_stat

print()
print(f"{len(failures)} check(s) failed" if failures else "all checks passed")
sys.exit(1 if failures else 0)
```

- [ ] **Step 3: Run it and watch this task's sections fail**

```bash
python "$SCRATCH/check_outcomes.py" plugins/retro/bin/retro.py outcomes moments pack
```

Expected: the `outcomes` section reports
`AttributeError: module 'retro_under_test' has no attribute 'measure_outcome'`,
`moments` and `pack` pass (they are regression guards — they pass today and must
keep passing once `read_records` starts raising), and the run exits 1.

- [ ] **Step 4: Add the failure signal to `read_records`**

Replace the head of the function:

```python
def read_records(path):
    """Yield parsed records, skipping malformed lines. A live session is being
    appended to while we read it; a truncated final line is normal, not an
    error."""
    try:
```

with:

```python
class TranscriptUnreadable(Exception):
    """The bytes could not be read.

    Distinct from a file that read fine and holds no conversation: one is a
    fault worth retrying, the other is a settled fact about the file. Reporting
    both as "unreadable" is what put 22 workflow journals in that bucket.
    """


def read_records(path):
    """Yield parsed records, skipping malformed lines. A live session is being
    appended to while we read it; a truncated final line is normal, not an
    error.

    Raises TranscriptUnreadable if the file cannot be opened, or if reading
    fails partway through. Returning an empty stream instead is what made a
    read failure indistinguishable from a file with no conversation in it.
    """
    try:
```

- [ ] **Step 5: Raise instead of returning, and name the three outcomes**

Replace:

```python
    except OSError:
        return


def measure(path):
    """Reduce one transcript to a metrics row."""
```

with:

```python
    except OSError as exc:
        raise TranscriptUnreadable(path) from exc


# The three ways a walked file can end up. `extract` prints them as three
# separate counts, and they must sum to the number of files walked.
MEASURED, NOT_TRANSCRIPT, UNREADABLE = "measured", "not-transcript", "unreadable"


def measure(path):
    """Reduce one transcript to a metrics row.

    Returns None for a file that read fine and holds no conversation. Raises
    TranscriptUnreadable for one whose bytes would not read. Callers wanting
    the three outcomes as a single value call measure_outcome() instead.
    """
```

The `try:` block spans the whole `with open(...)` including the read loop, so a
failure partway through is caught by the same clause as a failure to open.

- [ ] **Step 6: Detect "not a transcript" by structure**

Three edits inside `measure`. First, the counter, next to the other
per-transcript state. Replace:

```python
    prior_assistant_chars = 0
    tokens_in = tokens_out = cache_read = 0
```

with:

```python
    prior_assistant_chars = 0
    conversation = 0
    tokens_in = tokens_out = cache_read = 0
```

Second, count conversation records. Replace:

```python
        rtype = rec.get("type")
        ts = parse_ts(rec.get("timestamp"))
```

with:

```python
        rtype = rec.get("type")
        # Structural test for "is this a transcript at all". Filenames would
        # need a new rule for every sidecar format the CLI adds; record types
        # do not.
        if rtype in ("user", "assistant"):
            conversation += 1
        ts = parse_ts(rec.get("timestamp"))
```

Third, the terminal condition. Replace:

```python
    if session_id is None and not m:
        return None
```

with:

```python
    if not conversation:
        return None
```

Measured on the live corpus today: the new rule and the old one reject exactly
the same 22 files, with no file moving in either direction. If Task 3's ledger
comparison comes out non-identical, this edit is where to look first.

- [ ] **Step 7: Add the outcome wrapper**

After `measure` ends, before the `# --- extract ---` banner. Replace:

```python
    for key in COUNTERS:
        row[key] = m[key]
    return row


# --- extract ---
```

with:

```python
    for key in COUNTERS:
        row[key] = m[key]
    return row


def measure_outcome(path):
    """One file's outcome: (MEASURED, row), (NOT_TRANSCRIPT, None) or
    (UNREADABLE, None). Never raises for an unreadable file - the thread pool
    in cmd_extract abandons its whole result stream on the first exception."""
    try:
        row = measure(path)
    except TranscriptUnreadable:
        return UNREADABLE, None
    return (MEASURED, row) if row is not None else (NOT_TRANSCRIPT, None)


# --- extract ---
```

- [ ] **Step 8: Keep a pack alive when one transcript will not read**

Split `moments` into a wrapper that cannot raise and the body it had. Replace:

```python
def moments(row):
    """Pull the user turns
```

with:

```python
def moments(row):
    """Redacted evidence for one session, or nothing at all if its transcript
    will not read. A pack covers hundreds of sessions and must not die because
    one file went unreadable."""
    try:
        return _moments(row)
    except TranscriptUnreadable:
        return []


def _moments(row):
    """Pull the user turns
```

Nothing else in `moments` moves — the rest of the body becomes `_moments`
unchanged, and `cmd_pack` keeps calling `moments`.

- [ ] **Step 9: Count the three outcomes in `cmd_extract`**

Three edits. First, the counters. Replace:

```python
    stale = []
    skipped = 0
```

with:

```python
    stale = []
    unchanged = 0
    unreadable = 0
```

Second, its one use. Replace:

```python
        if state.get(str(path)) == fingerprint:
            skipped += 1
            continue
```

with:

```python
        if state.get(str(path)) == fingerprint:
            unchanged += 1
            continue
```

Third, the pool loop. Replace:

```python
    processed = failed = 0
    with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4))) as pool:
        for (path, fingerprint), row in zip(
                stale, pool.map(lambda item: measure(item[0]), stale)):
            if row is None:
                failed += 1
            else:
                rows[row["transcript"]] = row
                processed += 1
            state[str(path)] = fingerprint
```

with:

```python
    measured = not_transcripts = 0
    with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4))) as pool:
        for (path, fingerprint), (outcome, row) in zip(
                stale, pool.map(lambda item: measure_outcome(item[0]), stale)):
            if outcome == MEASURED:
                rows[row["transcript"]] = row
                measured += 1
            elif outcome == NOT_TRANSCRIPT:
                not_transcripts += 1
            else:
                unreadable += 1
            state[str(path)] = fingerprint
```

The fingerprint policy is deliberately untouched here; Task 2 changes it.

- [ ] **Step 10: Print the third bucket**

Replace:

```python
    print(f"transcripts: {len(transcripts)}  measured: {processed}  "
          f"unchanged: {skipped}  unreadable: {failed}")
```

with:

```python
    print(f"transcripts: {len(transcripts)}  measured: {measured}  "
          f"unchanged: {unchanged}  not-transcripts: {not_transcripts}  "
          f"unreadable: {unreadable}")
```

`unchanged` and `not-transcripts` are different words in both the code and the
output line, which is what constraint 1 requires.

- [ ] **Step 11: Run this task's sections and see them pass**

```bash
python "$SCRATCH/check_outcomes.py" plugins/retro/bin/retro.py outcomes moments pack
```

Expected: `all checks passed`, exit 0 — five outcome checks, two moments checks,
one pack check.

- [ ] **Step 12: Confirm the diff is the change and not a line-ending rewrite**

```bash
git diff --stat plugins/retro/bin/retro.py
```

Expected: 60 insertions and 16 deletions in one file. A diff that shows
the whole file rewritten means the editor converted the file to CRLF — restore
LF endings before committing.

- [ ] **Step 13: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: tell a file with no conversation apart from one that will not read

read_records swallowed OSError and yielded nothing, so measure() returned the
same None for a file that failed to open and for a file that opened fine and
held no conversation. extract printed both as unreadable. On this corpus all 22
of them are the second kind, and the count was read as 22 read failures.

read_records now raises TranscriptUnreadable, at open or partway through the
read. measure() returns None only for a file with no record of type user or
assistant in it - a structural test, so the next sidecar format the CLI adds
needs no filename rule. measure_outcome() turns the two signals into one of
three named outcomes and extract prints all three.

moments() gains a wrapper that swallows the same exception, because a pack
covers hundreds of sessions and must not die on one bad file.

Measured against the live corpus: the structural rule rejects exactly the files
the old condition rejected, no row enters or leaves the ledger, and a full
rebuild costs what it did before.
MSG
```

---

## Task 2: Retry what failed, and flag it

**Files:**
- Modify: `plugins/retro/bin/retro.py:15-19,355-394`

**Interfaces:**
- Consumes: `MEASURED`, `NOT_TRANSCRIPT`, `UNREADABLE`, `measure_outcome` from
  Task 1.
- Produces: `cmd_extract` returns `EXIT_FLAGGED` when any file was unreadable and
  `EXIT_CLEAN` otherwise.

- [ ] **Step 1: Run the extract section and watch it fail**

```bash
python "$SCRATCH/check_outcomes.py" plugins/retro/bin/retro.py extract
```

Expected, after Task 1 and before this task: 7 failures — `two unreadable`
(the `stat()` failure is in no bucket), `buckets sum to the file total`,
`exit 1 with an unreadable file` (the command returns `None`), `neither
unreadable file is fingerprinted`, and all three second-run checks (the failed
file was fingerprinted, so the retry never happens and the second run reports
zero unreadable).

- [ ] **Step 2: Put `stat()` failures in a bucket**

Replace:

```python
        try:
            stat = path.stat()
        except OSError:
            continue
```

with:

```python
        try:
            stat = path.stat()
        except OSError:
            # Nothing to compare and nothing to record: this is a read failure
            # like any other, and belongs in a bucket rather than in none.
            unreadable += 1
            continue
```

- [ ] **Step 3: Stop fingerprinting failures**

Replace:

```python
            if outcome == MEASURED:
                rows[row["transcript"]] = row
                measured += 1
            elif outcome == NOT_TRANSCRIPT:
                not_transcripts += 1
            else:
                unreadable += 1
            state[str(path)] = fingerprint
```

with:

```python
            if outcome == UNREADABLE:
                # Deliberately not fingerprinted. Recording one would retire
                # the file until it changes, so a live transcript that was
                # briefly locked would be dropped for good.
                unreadable += 1
                continue
            if outcome == MEASURED:
                rows[row["transcript"]] = row
                measured += 1
            else:
                not_transcripts += 1
            state[str(path)] = fingerprint
```

A file that is not a transcript stays fingerprinted on purpose: it read fine, the
answer will not change until the file does, and re-reading those files every run
is waste. Only failures are left un-fingerprinted.

A row already in the ledger for a file that later goes unreadable is left alone —
an incremental run keeps the last good measurement rather than dropping the
session. This is the behaviour the code already had and this fix does not change
it.

- [ ] **Step 4: Return an exit code**

Replace:

```python
    print(f"sessions in ledger: {len(rows)}")
    print(f"ledger: {METRICS_FILE}")
```

with:

```python
    print(f"sessions in ledger: {len(rows)}")
    print(f"ledger: {METRICS_FILE}")
    return EXIT_FLAGGED if unreadable else EXIT_CLEAN
```

The ledger and the state file are written before this line, so a flagged run
still leaves a complete ledger behind. `main()` already turns the return value
into the process exit code.

- [ ] **Step 5: Make the module docstring true**

Replace:

```python
    1  ran clean, something was flagged (friction in the window, dormant skills)
```

with:

```python
    1  ran clean, something was flagged (a transcript that would not read,
       friction in the window, dormant skills)
```

- [ ] **Step 6: Run the whole harness and see it pass**

```bash
python "$SCRATCH/check_outcomes.py" plugins/retro/bin/retro.py
```

Expected: `all checks passed`, exit 0 — 19 checks over all four sections,
including that the second run still reports the two unreadable files rather than
carrying them as unchanged.

- [ ] **Step 7: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: retry a transcript that would not read, and flag the run

A failed file was fingerprinted like a measured one, so it was never retried
until it changed on disk - one transient lock on a live transcript dropped it
from the ledger permanently. Failures are no longer fingerprinted, so the next
run reads them again.

A file whose stat() failed fell into no bucket at all, so the printed counts
could add up to less than the number of files walked. It now counts as
unreadable, and the three buckets plus unchanged sum to the file total.

extract returned None and therefore always exited 0. It now returns 1 when any
file was unreadable, matching the convention the sibling scripts follow. The
ledger is written before the return, so a flagged run is still a complete one.
MSG
```

---

## Task 3: Verify against the live corpus and fix what the change made false

**Files:**
- Modify: `docs/plans/2026-08-12-retro-design.md:156,170`
- Modify: `docs/plans/2026-08-18-retro-measurement-fixes.md` (Verification section)
- Modify: `plugins/retro/skills/finding-friction-in-recent-sessions/SKILL.md`
- Modify: `plugins/retro/skills/auditing-workflow-rules-against-behavior/SKILL.md`
- Modify: `plugins/retro/skills/scouting-tools-for-open-frictions/SKILL.md`

**Interfaces:**
- Consumes: the finished `extract` from Task 2.
- Produces: measured numbers for the spec's Verification section.

- [ ] **Step 1: Build a baseline ledger from the pre-change script**

Run both builds back to back — the corpus is live, and a session in flight
changes rows between runs.

```bash
git show HEAD~2:plugins/retro/bin/retro.py > "$SCRATCH/retro-baseline.py"
mkdir -p "$SCRATCH/work-base" "$SCRATCH/work-new"
RETRO_HOME="$SCRATCH/work-base" python "$SCRATCH/retro-baseline.py" extract --rebuild
```

Expected: `transcripts: 1920  measured: 1898  unchanged: 0  unreadable: 22`
(the file total drifts upward as sessions accumulate; measured + unreadable must
equal it).

- [ ] **Step 2: Build the same ledger from the changed script and compare**

```bash
RETRO_HOME="$SCRATCH/work-new" python plugins/retro/bin/retro.py extract --rebuild
echo "exit=$?"
```

Expected: `transcripts: 1920  measured: 1898  unchanged: 0  not-transcripts: 22
unreadable: 0`, and `exit=0` — nothing on this corpus is genuinely unreadable,
so the run flags nothing.

```bash
python - "$SCRATCH/work-base/metrics.jsonl" "$SCRATCH/work-new/metrics.jsonl" <<'PY'
import json, sys
def rows(p):
    return [json.loads(l) for l in open(p, encoding="utf-8")]
a, b = rows(sys.argv[1]), rows(sys.argv[2])
print("rows:", len(a), len(b))
today = max(r["date"] for r in a)
sa = {r["transcript"]: json.dumps(r, sort_keys=True) for r in a if r["date"] < today}
sb = {r["transcript"]: json.dumps(r, sort_keys=True) for r in b if r["date"] < today}
print("rows older than the current day:", len(sa), len(sb))
print("identical excluding the current day:", sa == sb)
PY
```

Expected: the same row count from both, and `identical excluding the current
day: True`. Rows dated today can differ because a live session grew between the
two runs; anything older differing means the structural rule changed what gets
measured, which it must not.

- [ ] **Step 3: Time a full rebuild**

```bash
time RETRO_HOME="$SCRATCH/work-new" python plugins/retro/bin/retro.py extract --rebuild
```

Expected: about 5.5 s, against a 5.5 s baseline measured today. A rebuild that
takes materially longer means the conversation counter is being computed
somewhere costly.

- [ ] **Step 4: Confirm an incremental run is still cheap and still clean**

```bash
RETRO_HOME="$SCRATCH/work-new" python plugins/retro/bin/retro.py extract
echo "exit=$?"
```

Expected: `unchanged` near the file total, `not-transcripts: 0` (they were
fingerprinted on the rebuild and now count as unchanged), `unreadable: 0`, and
`exit=0`.

- [ ] **Step 5: Correct the earlier design document**

In `docs/plans/2026-08-12-retro-design.md`, replace:

```markdown
| Full rebuild over 1,800 transcripts | 5.9 s, 1,778 rows, 22 unreadable |
```

with:

```markdown
| Full rebuild over 1,800 transcripts | 5.9 s, 1,778 rows, 22 files that hold no conversation |
```

and replace:

```markdown
- The 22 unreadable transcripts are counted but not characterized.
```

with:

```markdown
- The 22 files this table calls unreadable were measured on 2026-08-19: every
  one opened and decoded cleanly and holds no record of type `user` or
  `assistant`. `extract` now reports that outcome under its own name and keeps
  `unreadable` for files whose bytes would not read.
```

- [ ] **Step 6: Record the measured results in the spec**

In `docs/plans/2026-08-18-retro-measurement-fixes.md`, append this subsection at
the end of the `## Verification` section, immediately before `## Non-goals`.
Append — do not restructure the shared table, which the three sibling fixes also
have to write into. Fill the right column with what Steps 1-4 actually printed.

```markdown
### Fix 1 — measured after implementation

| Check | Before | After |
|---|---|---|
| unreadable / not-transcript split | 22 / 0 | 0 / 22 |
| counts sum to the file total | latent hole: a file whose `stat()` fails is in no bucket, and none currently does | they sum, and a synthetic `stat()` failure now counts as unreadable |
| exit code with an unreadable file | 0 | 1, and the file is retried on the next run |
| ledger rows | 1,898 | 1,898, byte-identical for every row older than the current day |
| full rebuild | 5.5 s over 1,920 files | 5.5 s |
```

- [ ] **Step 7: Say in the skills that a run can now exit 1**

All three skills run `extract` as a procedure step. Add the same sentence
directly after the bash block in each, so a nonzero exit is not read as a broken
run.

In `plugins/retro/skills/finding-friction-in-recent-sessions/SKILL.md`, after the
block ending `pack --days 7`:

```markdown
`extract` exits 1 when a transcript would not read. It still writes the ledger,
and retries that file on the next run.
```

In `plugins/retro/skills/auditing-workflow-rules-against-behavior/SKILL.md`,
after the block ending `pack --days 30`, and in
`plugins/retro/skills/scouting-tools-for-open-frictions/SKILL.md`, after the
block ending `pack --days 30`: the same two lines, verbatim.

- [ ] **Step 8: Check the repo for what the change made false, and for private data**

```bash
grep -rn "unreadable" --include="*.md" --include="*.py" .
plugins/core/bin/repo-privacy-audit
git diff --stat HEAD~2
```

Expected: `unreadable` survives only in `retro.py`, in the spec, and in the two
lines of the earlier design document that now explain the distinction; the
privacy audit reports only the known accepted commit-metadata hit; the diff
touches `retro.py`, two plan documents, and three skills, and nothing else.

- [ ] **Step 9: Commit**

```bash
git add docs/plans plugins/retro/skills
git commit -F - <<'MSG'
docs: the 22 were never read failures, and extract can now exit 1

Records what the corpus measurement showed - every file the old code called
unreadable opened and decoded cleanly and holds no conversation - and replaces
the open question the earlier design left about them.

Adds the measured before-and-after numbers to the fixes spec as its own
subsection, and tells the three skills that run extract that a nonzero exit
means a file would not read, not that the run failed.
MSG
```

---

## Questions for the operator

1. The spec's verification table says the printed counts "do not [sum] today", but on this corpus they do — 1,898 + 0 + 22 = 1,920 — because no file's `stat()` currently fails; may I correct that row's wording to "latent" as Task 3 Step 6 does, or should the spec's original wording stand?
2. The two oldest commits here carry a `Co-Authored-By` trailer and the six most recent do not, while the standing global instruction says every commit message should end with one — the plan's commit blocks follow recent practice and omit it, so should these three commits carry the trailer instead?
3. The synthetic-corpus harness is the only thing that can exercise an unreadable file, a `stat()` failure, and a mid-read failure, and this plan keeps it in a scratch directory outside the repo — should it instead be committed, and if so where, given the repo has no test directory?
