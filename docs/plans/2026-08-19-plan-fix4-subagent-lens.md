# Fix 4 — a subagent lens: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every transcript four new mechanical-failure counters and add a
`subagents` subcommand that reports them, plus turn length, over a window.

**Architecture:** `measure()` gains a per-transcript map from tool-use id to tool
name, built as it already walks assistant records. Failed `tool_result` blocks are
matched against that map plus one exact marker string each, so a category is
attributed to a specific tool rather than to error text alone. The four counters
live in a second schema list beside `COUNTERS` so the pack's trend table is
untouched. A new `cmd_subagents` aggregates them over rows where `is_subagent` is
true. Nothing in `pack`, `totals()`, `friction_score`, or `moments()` changes.

**Tech Stack:** Python 3 stdlib only. No test framework exists in this repo;
verification is by measurement against the live corpus, read-only, with an
isolated `RETRO_HOME`.

**Spec:** `docs/plans/2026-08-18-retro-measurement-fixes.md`, section "Fix 4 — a
subagent lens". Read it before task 1.

## Global Constraints

- Stdlib only. No dependency, no build step, no daemon.
- No other project is named — not in code, comments, output, plan text, or commit
  messages. The marker strings chosen below are harness text and name no project.
- No absolute path from the author's machine enters a tracked file.
- Adding a counter is a ledger-contract change: `extract --rebuild` is required
  before any trend over it means anything.
- Message text leaves the tool only through `redact()`. This fix adds **no** new
  text egress: it counts, and the `subagents` report prints only numbers plus the
  already-redacted `project` field that `pack` prints today.
- Exit codes: `0` ran clean and flagged nothing, `1` ran clean and flagged
  something, `2` could not run.
- **Never write anywhere under the user's Claude configuration directory.** Every
  verification run sets `RETRO_HOME` to a scratch directory outside any
  repository, and reads the corpus read-only.

---

## The discipline this fix lives or dies by

An error rate is not a mistake rate. A command that ran and returned a non-zero
exit is information — a failing test, a search with no match, a probe that proved
something absent. It is not an agent error and is **not counted here**.

Only four categories are counted. Each one is an action the agent took that the
harness refused or rejected on its own terms:

| Counter | Counted when |
|---|---|
| `returned_nothing` | the whole transcript produced no assistant text |
| `schema_rejections` | a `StructuredOutput` call was rejected against its schema |
| `write_precondition_failures` | a `Write` was refused because the file had not been read |
| `read_missing_path` | a `Read` was refused because the path does not exist |

Deliberately **not** counted, and why:

| Excluded | Measured on the subagent corpus | Why not counted |
|---|---|---|
| Non-zero command exit | 205 of 428 failed `Bash` results, 71 of 75 failed `PowerShell` results | The command ran. The exit code is the answer, not a mistake. |
| Refusal by a permission or isolation rule | 217 of the remaining failed `Bash` results | A policy decision about the environment, not an agent error. |
| `Edit` refused because the file had not been read | 8 on subagent transcripts | The spec scopes the precondition counter to `Write`. Same marker text, different tool — which is exactly why attribution is by tool id, not by text. |
| Malformed tool input, oversized read, unavailable tool | the remainder of the 76 failed `Read` results | Not in the spec's list. Adding one is a scope decision, not an implementation detail. |

## Re-derived measurements

Every number in the spec's Fix 4 table was re-derived against the live corpus on
2026-08-19 rather than trusted. Corpus at time of measurement: 1,920 transcript
files, 1,489 of them subagent transcripts (the spec measured 1,481 on 2026-08-18;
the corpus grows).

| Spec claim | Re-derived | Agrees? |
|---|---|---|
| no assistant text at all: 177 (12%) | 177 of 1,489 = 11.9% | yes |
| `StructuredOutput` rejected: 53 of 811 calls | 53 of 811, every one a schema/validation rejection | yes |
| `Write` refused on a precondition: 20 of 29 | 20 of 29 failed `Write` results | yes |
| `Read` on a path that does not exist: 26 of 80 | 26 of **76** failed `Read` results | numerator yes, **denominator differs** |
| turns: median 11, p90 102, max 707 | median 11, p90 102, p95 142, max 707 | yes |
| non-zero exit: 206 `Bash`, 63 `PowerShell` | 205 `Bash`, 71 `PowerShell` | close; both stay excluded either way |

Two corrections the implementer must not paper over:

1. **Failed `Read` results number 76, not 80.** The 26 nonexistent-path reads are
   exact; the denominator is not. Every failed result block in the corpus resolves
   to a named tool — exactly one block corpus-wide, in a main-session transcript,
   has no matching tool-use record — so there is no hidden bucket that would make
   up the difference.
2. **"nearly all of both are commands that ran and returned non-zero" over-states
   the `Bash` case.** For `PowerShell` it holds (71 of 75). For `Bash` it is 205
   of 428; most of the remainder were refused by a permission or isolation rule
   before running. The conclusion is unchanged — neither is counted — but the
   sentence in the spec is not what the corpus says.

### The number the tool can actually report

`measure()` returns `None` for a transcript with no records it recognises, and
`cmd_extract` writes no row for it. Measured: exactly 22 subagent transcript files
have zero assistant records, and those same 22 files are exactly the files that
produce no ledger row.

So the ledger sees **1,467** subagent rows, not 1,489, and `returned_nothing` will
read **155**, not 177. The missing 22 are the files Fix 1 reclassifies as "not a
transcript" — Fix 1 changes how they are *reported*, not whether they yield a row.
A `subagents` report claiming 177 would be claiming a number it cannot derive.

Expected ledger-level values after this fix, all measured directly:

| Counter | Occurrences | Rows carrying at least one |
|---|---|---|
| `returned_nothing` | 155 | 155 (it is 0 or 1 per row) |
| `schema_rejections` | 53 | 24 |
| `write_precondition_failures` | 20 | 19 |
| `read_missing_path` | 26 | 14 |
| `turns` (existing column) | median 11, p90 103, p95 142, max 707 | 159 rows at 100 turns or more |

Main-session rows also carry non-zero values (6 schema rejections, 5 write
precondition failures, 72 nonexistent-path reads). The counters are computed for
every transcript because `is_subagent` is not known until `measure()` reaches its
tail; `cmd_subagents` filters. Task 3 explains why that is the right shape.

### This plan's code was run before the plan was written

Every code block in tasks 2, 3, and 4 was applied to a throwaway copy of
`retro.py` outside the repository and run against the corpus with an isolated
`RETRO_HOME`. It parses, rebuilds the ledger, and prints a report whose four
figures equal the oracle's exactly. The numbers above are therefore a measured
result, not a projection. Rebuild cost is unchanged: three paired runs measured
5.51 / 5.70 / 5.47 seconds before the change against 5.87 / 5.58 / 5.41 after,
which is noise in both directions.

## File Structure

One file changes: `plugins/retro/bin/retro.py`.

| Region (line numbers as of commit 7986496) | What it becomes |
|---|---|
| module docstring, lines 2-19 | lists four subcommands instead of two |
| tuning constants, after line 51 | gains `RUNAWAY_TURNS` |
| schema block, lines 55-57 | `COUNTERS` unchanged; new `SUBAGENT_COUNTERS` beside it |
| `tool_calls_of`, lines 125-134 | yields the block id as well as name and signature |
| after `tool_calls_of` | marker constants and `tool_errors_of` |
| `measure`, lines 224-331 | builds the id map, counts the four categories, writes the new columns |
| after `cmd_skills`, line 566 | `cmd_subagents` and its two helpers |
| `main`, lines 569-590 | `subagents` subparser |

---

## Task 1: Freeze an independent oracle

The repo has no test suite, so the "failing test" is an oracle script that
measures the corpus by a second, independently written path. It lives in scratch,
is never committed, and never writes inside the repo or under the user's Claude
configuration directory.

**Files:**
- Create: `$SCRATCH/oracle_fix4.py`, where `$SCRATCH` is a directory outside any
  repository, chosen by the implementer. Not committed.
- Read only: the transcript corpus.

**Interfaces:**
- Consumes: nothing.
- Produces: a printed block of numbers that tasks 3 and 4 must reproduce exactly —
  `returned_nothing`, `schema_rejections`, `write_precondition_failures`,
  `read_missing_path`, the subagent row count, and the turn quantiles.

- [ ] **Step 1: Choose a scratch directory and export it**

```bash
export SCRATCH="<a directory outside any git repository>"
export RETRO_HOME="$SCRATCH/retro-home"
mkdir -p "$RETRO_HOME"
```

Every later step assumes both variables are set. If `RETRO_HOME` is unset,
`extract` writes to the default work directory — do not run it without it.

- [ ] **Step 2: Write the oracle**

```python
# $SCRATCH/oracle_fix4.py - read-only measurement, never committed.
import json, statistics
from pathlib import Path

ROOT = Path.home() / ".claude" / "projects"
SCHEMA = "Output does not match required schema"
UNREAD = "File has not been read yet."
NO_PATH = "File does not exist."


def records(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line and not line.isspace():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
    except OSError:
        return


def assistant_text(message):
    content = (message or {}).get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(b.get("text") or "" for b in content
                     if isinstance(b, dict) and b.get("type") == "text")


files = [p for p in sorted(ROOT.rglob("*.jsonl"))
         if "subagents/" in p.relative_to(ROOT).as_posix()]
nothing = schema = unread = no_path = rows = 0
turns = []
for path in files:
    by_id = {}
    chars = assistants = 0
    c_schema = c_unread = c_no_path = 0
    for rec in records(path):
        if not isinstance(rec, dict):
            continue
        content = (rec.get("message") or {}).get("content")
        if rec.get("type") == "assistant":
            assistants += 1
            chars += len(assistant_text(rec.get("message")).strip())
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        by_id[b.get("id")] = b.get("name") or ""
        elif rec.get("type") == "user" and isinstance(content, list):
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"
                        and b.get("is_error")):
                    continue
                text = b.get("content")
                if not isinstance(text, str):
                    continue
                name = by_id.get(b.get("tool_use_id"), "")
                if name == "StructuredOutput" and SCHEMA in text:
                    c_schema += 1
                elif name == "Write" and UNREAD in text:
                    c_unread += 1
                elif name == "Read" and NO_PATH in text:
                    c_no_path += 1
    if assistants == 0:      # measure() writes no row for these
        continue
    rows += 1
    turns.append(assistants)
    schema += c_schema
    unread += c_unread
    no_path += c_no_path
    if chars == 0:
        nothing += 1

turns.sort()
q = lambda f: turns[int(round((len(turns) - 1) * f))]
print(f"files {len(files)}  rows {rows}")
print(f"returned_nothing {nothing}")
print(f"schema_rejections {schema}")
print(f"write_precondition_failures {unread}")
print(f"read_missing_path {no_path}")
print(f"turns median {statistics.median(turns)} p90 {q(0.90)} "
      f"p95 {q(0.95)} max {max(turns)}")
print(f"turns_ge_100 {sum(1 for t in turns if t >= 100)}")
```

- [ ] **Step 3: Run the oracle and record its output**

Run: `python "$SCRATCH/oracle_fix4.py"`

Expected, as measured 2026-08-19 (the corpus grows, so these drift upward; what
matters is that tasks 3 and 4 reproduce **this run's** numbers, not these):

```
files 1489  rows 1467
returned_nothing 155
schema_rejections 53
write_precondition_failures 20
read_missing_path 26
turns median 11 p90 103 p95 142 max 707
turns_ge_100 159
```

The oracle skips zero-assistant-record files so its population matches
`measure()`'s. `p90` reads 103 here against the spec's 102 because the population
is the 1,467 rows, not the 1,489 files.

- [ ] **Step 4: Capture the pre-change baseline**

Run, from the worktree root:

```bash
time python plugins/retro/bin/retro.py extract --rebuild
```

Expected: `transcripts: 1920  measured: 1898  unchanged: 0  unreadable: 22`
(numbers grow with the corpus), completing in roughly 5 seconds. Record the wall
clock — task 3 compares against it.

- [ ] **Step 5: No commit**

Nothing in the repository changed. Do not commit scratch files.

---

## Task 2: Detection primitives

**Files:**
- Modify: `plugins/retro/bin/retro.py` — the tuning constants block, the schema
  block, `tool_calls_of` (lines 125-134), and a new block immediately after it.

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, for task 3:
  - `tool_calls_of(message) -> Iterator[tuple[str, str, str]]` yielding
    `(block_id, tool_name, input_signature)`. **The arity changed from 2 to 3 and
    the new element is first.** Its only call site is inside `measure`.
  - `tool_errors_of(message, tool_by_id) -> Iterator[tuple[str, str]]` yielding
    `(tool_name, error_text)` for each failed tool result.
  - `SCHEMA_REJECTED`, `WRITE_UNREAD`, `READ_NO_SUCH_PATH` — exact marker strings.
  - `SUBAGENT_COUNTERS: list[str]` — the four new column names, in order.
  - `RUNAWAY_TURNS: int` — turn count at or above which a transcript is listed as
    over-long.

- [ ] **Step 1: Add the runaway constant to the tuning block**

Insert after `CORRECTION_MIN_PRIOR_CHARS = 400` (line 51):

```python
# Turn count at or above which a transcript is worth a look for its length alone.
# Measured over 1,467 subagent rows: median 11, p90 103, p95 142, max 707, with
# 159 rows at or above this line. A judgement call, not a derived value - see the
# operator question at the end of docs/plans/2026-08-19-plan-fix4-subagent-lens.md
RUNAWAY_TURNS = 100
```

- [ ] **Step 2: Add the second schema list beside `COUNTERS`**

Insert immediately after the `COUNTERS` list (line 57):

```python
# The subagent lens. Same ledger contract as COUNTERS - each name is a column and
# adding one means an extract --rebuild - but kept as a separate list so the
# pack's trend table and per-session line, which iterate COUNTERS, are unchanged
# by this fix. Only `subagents` reads these.
SUBAGENT_COUNTERS = ["returned_nothing", "schema_rejections",
                     "write_precondition_failures", "read_missing_path"]
```

- [ ] **Step 3: Give `tool_calls_of` the block id**

Replace lines 125-134 with:

```python
def tool_calls_of(message):
    """Yield (block_id, tool_name, input_signature) for each tool use in a
    message. The id is how a later failed tool_result is attributed back to the
    tool that produced it - the result block carries only the id, never a name."""
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield (block.get("id") or "", block.get("name") or "?",
                   signature(block.get("input")))
```

Measured: all 29,732 tool-use blocks in the subagent corpus carry an id, so the
`or ""` fallback is a guard, not a path anything takes today.

- [ ] **Step 4: Add the markers and the failed-result walker**

Insert immediately after `tool_calls_of`:

```python
# Exact harness refusal text, one marker per counted category. Matched as a
# substring against the failed result body AND against the tool that produced it:
# the "not been read" text is emitted for Edit as well as Write, and the "does not
# exist" text for Edit as well as Read, so text alone would count the wrong thing.
SCHEMA_REJECTED = "Output does not match required schema"
WRITE_UNREAD = "File has not been read yet."
READ_NO_SUCH_PATH = "File does not exist."


def tool_errors_of(message, tool_by_id):
    """Yield (tool_name, error_text) for each failed tool result in a message.

    A block whose id has no matching tool_use record yields a tool name of "" and
    therefore matches no category - measured across the whole corpus, exactly one
    block is in that state, in a main-session transcript. A non-string result body
    is skipped rather than serialised: every failed result in the corpus carries a
    string, and JSON-dumping the alternative would invent text for a marker to
    match against.
    """
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        if not block.get("is_error"):
            continue
        body = block.get("content")
        if not isinstance(body, str):
            continue
        yield tool_by_id.get(block.get("tool_use_id"), ""), body
```

- [ ] **Step 5: Prove the module still parses and the arity change is contained**

Run:

```bash
python -c "import ast; ast.parse(open('plugins/retro/bin/retro.py', encoding='utf-8').read())"
grep -n "tool_calls_of" plugins/retro/bin/retro.py
```

Expected: the parse is silent, and `grep` prints exactly two lines — the
definition and the one call site inside `measure`. If it prints three, a sibling
fix added a caller; update that caller to the three-element form before going on.

- [ ] **Step 6: Run the tool to confirm the call site is where this plan says**

Run: `python plugins/retro/bin/retro.py extract --rebuild`

Expected: **fails** with a `ValueError` about unpacking, because `measure` still
destructures two values. That failure is the point — it proves the only call site
is the one task 3 rewrites. Do not fix it here.

- [ ] **Step 7: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
feat(retro): detection primitives for the subagent lens

tool_calls_of now yields the tool-use block id, so a failed tool_result can be
attributed to the tool that produced it. Adds tool_errors_of, the three exact
refusal markers, the SUBAGENT_COUNTERS column list, and RUNAWAY_TURNS.

measure() is updated in the next commit; the call site is deliberately left
broken here so the arity change is visible in one place.
MSG
```

---

## Task 3: Count the four categories in `measure()`

**Files:**
- Modify: `plugins/retro/bin/retro.py` — `measure` (lines 224-331).

**Interfaces:**
- Consumes: `tool_calls_of`, `tool_errors_of`, `SCHEMA_REJECTED`, `WRITE_UNREAD`,
  `READ_NO_SUCH_PATH`, `SUBAGENT_COUNTERS` from task 2.
- Produces, for task 4: every ledger row carries the four keys in
  `SUBAGENT_COUNTERS` as integers.

**Why the counters are computed for every transcript, not only subagent ones:**
`is_subagent` is derived at the *end* of `measure()`, from the path, after the
record loop has finished. Gating the loop on it would mean hoisting that
derivation above the loop — which is precisely the code Fix 3 rewrites. Counting
unconditionally costs one dictionary per transcript and leaves Fix 3's territory
alone. `cmd_subagents` filters.

- [ ] **Step 1: Add the two new locals**

In `measure`, alongside the existing local initialisers (lines 226-235), add:

```python
    tool_by_id = {}
    assistant_text_chars = 0
```

- [ ] **Step 2: Accumulate assistant text and build the id map**

In the `elif rtype == "assistant":` branch, replace lines 280-287 with:

```python
            body = text_of(msg)
            prior_assistant_chars = len(body)
            assistant_text_chars += len(body.strip())
            for block_id, name, sig in tool_calls_of(msg):
                tool_by_id[block_id] = name
                m["tool_calls"] += 1
                key = (name, sig)
                if sig and key in seen_sigs:
                    m["tool_retries"] += 1
                seen_sigs.add(key)
```

`text_of` on an assistant message returns its text blocks joined; thinking blocks
and tool-use blocks contribute nothing, which is what "returned nothing" means —
the caller received no prose.

- [ ] **Step 3: Classify failed tool results**

Immediately after the existing tool-error lines (lines 299-302):

```python
        # Only user records carry tool results; checking the rest re-walks
        # message content for nothing.
        if rtype == "user" and is_error_record(rec):
            m["tool_errors"] += 1
```

add:

```python
        if rtype == "user":
            # Mechanical failures the harness refused on its own terms. A
            # non-zero command exit is NOT one of these: the command ran, and the
            # exit code is information, not an agent error.
            for name, body in tool_errors_of(rec.get("message"), tool_by_id):
                if name == "StructuredOutput" and SCHEMA_REJECTED in body:
                    m["schema_rejections"] += 1
                elif name == "Write" and WRITE_UNREAD in body:
                    m["write_precondition_failures"] += 1
                elif name == "Read" and READ_NO_SUCH_PATH in body:
                    m["read_missing_path"] += 1
```

`tool_errors` counts a record once; these count a block. Measured: no record in
the corpus carries more than one failed result block, so the two cannot disagree
today — the block-level form is used because attribution needs the block's id.

- [ ] **Step 4: Write the new columns onto the row**

After the existing `for key in COUNTERS:` loop (lines 329-330) and before
`return row`, add:

```python
    m["returned_nothing"] = 1 if assistant_text_chars == 0 else 0
    for key in SUBAGENT_COUNTERS:
        row[key] = m[key]
```

`returned_nothing` is stored as 0 or 1 so that summing the column over a
population gives the number of transcripts that returned nothing.

- [ ] **Step 5: Rebuild into the isolated work directory**

Run:

```bash
echo "RETRO_HOME=$RETRO_HOME"   # must be set, and must not be under ~/.claude
time python plugins/retro/bin/retro.py extract --rebuild
```

Expected: the same `transcripts: / measured: / unchanged: / unreadable:` line as
the task 1 baseline, and no measurable slowdown. Three paired runs of the finished
change measured 5.51 / 5.70 / 5.47 seconds before against 5.87 / 5.58 / 5.41
after — noise, not cost. A run that is consistently slower means the id map is
being rebuilt somewhere it should not be.

- [ ] **Step 6: Check the ledger against the oracle**

Run:

```bash
python - <<'PY'
import json, os, statistics
from pathlib import Path
rows = [json.loads(l) for l in
        (Path(os.environ["RETRO_HOME"]) / "metrics.jsonl")
        .read_text(encoding="utf-8").splitlines() if l.strip()]
sub = [r for r in rows if r.get("is_subagent")]
print("subagent rows", len(sub))
for key in ["returned_nothing", "schema_rejections",
            "write_precondition_failures", "read_missing_path"]:
    total = sum(int(r.get(key) or 0) for r in sub)
    carrying = sum(1 for r in sub if int(r.get(key) or 0))
    print(f"{key} {total} across {carrying} rows")
turns = sorted(int(r.get("turns") or 0) for r in sub)
q = lambda f: turns[int(round((len(turns) - 1) * f))]
print(f"turns median {statistics.median(turns)} p90 {q(0.90)} "
      f"p95 {q(0.95)} max {max(turns)}")
print("turns_ge_100", sum(1 for t in turns if t >= 100))
PY
```

Expected: every number equals the task 1 oracle run, exactly. As measured
2026-08-19 that is 1,467 subagent rows, `returned_nothing` 155,
`schema_rejections` 53 across 24 rows, `write_precondition_failures` 20 across 19
rows, `read_missing_path` 26 across 14 rows, turns median 11 / p90 103 / p95 142 /
max 707, and 159 rows at 100 turns or more.

If any number differs, the oracle and the implementation disagree about a
definition — settle which is right before continuing. Do not adjust the oracle to
match the tool.

- [ ] **Step 7: Confirm `pack` is unchanged**

Run: `python plugins/retro/bin/retro.py pack --days 30`

Expected: exits 0 or 1, and its trend table lists exactly the `COUNTERS` names —
none of the four new ones. That is the point of the separate list.

- [ ] **Step 8: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
feat(retro): count mechanical subagent failures in measure()

Four new ledger columns: returned_nothing, schema_rejections,
write_precondition_failures, read_missing_path. Each is attributed to a specific
tool via the tool-use block id, because two of the three refusal markers are
emitted for more than one tool.

Non-zero command exits are deliberately not counted. The command ran; the exit
code is information, not an agent error.

Verified against an independent measurement of the corpus: 1,467 subagent rows,
155 / 53 / 20 / 26.

Requires extract --rebuild.
MSG
```

---

## Task 4: The `subagents` subcommand

**Files:**
- Modify: `plugins/retro/bin/retro.py` — a new block after `cmd_skills`
  (line 566), `main` (lines 569-590), and the module docstring (lines 2-19).

**Interfaces:**
- Consumes: `SUBAGENT_COUNTERS`, `RUNAWAY_TURNS`, `load_rows`, the `EXIT_*`
  constants.
- Produces: `cmd_subagents(args)` returning `EXIT_FLAGGED` or `EXIT_CLEAN`;
  `quantile(sorted_values, fraction)`.

- [ ] **Step 1: Write the command**

Insert after `cmd_skills`, before `main`:

```python
# --- subagents -------------------------------------------------------------

# What each counter means when it fires, in one line, printed beside the number
# so the report reads without this file open.
SUBAGENT_SIGNALS = [
    ("returned_nothing", "produced no assistant text at all"),
    ("schema_rejections", "structured output rejected against its schema"),
    ("write_precondition_failures", "wrote a file it had not read"),
    ("read_missing_path", "read a path that does not exist"),
]


def quantile(sorted_values, fraction):
    """Nearest-rank quantile over a pre-sorted list. Empty input has no
    quantile; callers guard."""
    return sorted_values[int(round((len(sorted_values) - 1) * fraction))]


def cmd_subagents(args):
    rows = [r for r in load_rows() if r.get("is_subagent")]
    if args.days:
        start = (datetime.now(timezone.utc).date()
                 - timedelta(days=args.days)).isoformat()
        rows = [r for r in rows if (r.get("date") or "") >= start]
    window = f"last {args.days} days" if args.days else "all history"
    if not rows:
        print(f"# Subagent lens - {window}\n\nNo subagent transcripts in window.")
        return EXIT_CLEAN

    # A ledger built before these columns existed reports zero for every signal,
    # which is indistinguishable from a clean window. Say so rather than lie.
    stale = sum(1 for r in rows if any(k not in r for k in SUBAGENT_COUNTERS))
    print(f"# Subagent lens - {window}, {len(rows)} transcripts\n")
    if stale:
        print(f"WARNING: {stale} of {len(rows)} rows predate these counters - "
              f"run `extract --rebuild` before trusting the numbers below.\n")

    tallies = []
    flagged = 0
    for key, label in SUBAGENT_SIGNALS:
        occurrences = sum(int(r.get(key) or 0) for r in rows)
        carrying = sum(1 for r in rows if int(r.get(key) or 0))
        tallies.append((occurrences, carrying, key, label))
        flagged += occurrences

    print("## Mechanical failures\n")
    print("| signal | transcripts | occurrences | share |")
    print("|---|---|---|---|")
    for occurrences, carrying, key, label in sorted(tallies, reverse=True):
        share = carrying / len(rows) * 100
        print(f"| {key} - {label} | {carrying} | {occurrences} | {share:.1f}% |")

    turns = sorted(int(r.get("turns") or 0) for r in rows)
    long_runs = sum(1 for t in turns if t >= RUNAWAY_TURNS)
    print(f"\n## Length\n\nturns: median {quantile(turns, 0.5)}, "
          f"p90 {quantile(turns, 0.90)}, p95 {quantile(turns, 0.95)}, "
          f"max {turns[-1]}. {long_runs} at or above {RUNAWAY_TURNS} turns.")

    print("\nNot counted as failures: a command that ran and returned a non-zero "
          "exit, and a call a permission rule refused. Neither is an agent "
          "mistake.")

    def failures(row):
        return sum(int(row.get(k) or 0) for k in SUBAGENT_COUNTERS)

    print("\n## Most affected transcripts\n")
    ranked = sorted(rows, key=lambda r: (failures(r), int(r.get("turns") or 0)),
                    reverse=True)[:args.top]
    for row in ranked:
        if not failures(row) and int(row.get("turns") or 0) < RUNAWAY_TURNS:
            continue
        detail = ", ".join(f"{k} {row.get(k)}" for k in SUBAGENT_COUNTERS
                           if int(row.get(k) or 0))
        print(f"  {row.get('date') or '?'}  {row.get('project') or '?'}  "
              f"turns {row.get('turns')}  {detail or '-'}")
    return EXIT_FLAGGED if flagged else EXIT_CLEAN
```

`project` was written by `measure()` through `redact()`, so this prints exactly
what `pack` already prints for the same field. The standing limitation recorded in
the spec still holds and matters here, because this report ranks transcripts and
therefore names directories: `redact()` mirrors the mechanical categories of the
privacy audit and cannot recognise a project name, so **the "Most affected
transcripts" lines do carry other projects' absolute paths**. Verified by running
it. That output is for the terminal only — it must never be pasted into a tracked
file, a commit message, a plan, or anywhere published. This is the same standing
exposure `pack` has, not a new one, but the ranked list makes it far more likely
to be copied.

- [ ] **Step 2: Register the subparser**

In `main`, after the `skills` parser block (line 587):

```python
    p_sub = sub.add_parser("subagents",
                           help="mechanical failures in subagent transcripts")
    p_sub.add_argument("--days", type=int, default=30,
                       help="restrict to a window; 0 means all history")
    p_sub.add_argument("--top", type=int, default=10,
                       help="how many affected transcripts to list")
    p_sub.set_defaults(func=cmd_subagents)
```

`--days 0` meaning all history matches the `skills` subcommand's existing
convention.

- [ ] **Step 3: Update the module docstring**

Replace docstring lines 4-7:

```
Two subcommands:

    extract   walk session transcripts, append one metrics row per session
    pack      build an evidence pack (trends + redacted moments) for a window
```

with:

```
Four subcommands:

    extract    walk session transcripts, append one metrics row per session
    pack       build an evidence pack (trends + redacted moments) for a window
    skills     which installed skills actually fire
    subagents  mechanical failures in subagent transcripts, over a window
```

`skills` already existed and was missing from the list; adding it is part of
making the docstring true.

- [ ] **Step 4: Run it over all history and check against the oracle**

Run: `python plugins/retro/bin/retro.py subagents --days 0`

Expected: `1467 transcripts` (or the current corpus count), no stale-rows warning,
and a failures table whose four occurrence figures equal the task 1 oracle output
exactly — 155, 53, 20, 26 as measured 2026-08-19. The length line reads median 11,
p90 103, p95 142, max 707, with 159 at or above 100 turns.

- [ ] **Step 5: Check the exit code**

Run:

```bash
python plugins/retro/bin/retro.py subagents --days 0; echo "exit=$?"
```

Expected: `exit=1` — the window carries failures, and the repo convention is that
`1` means ran clean and flagged something.

- [ ] **Step 6: Check the narrow window and the stale-ledger warning**

Run:

```bash
python plugins/retro/bin/retro.py subagents --days 1; echo "exit=$?"
python - <<'PY'
import json, os
from pathlib import Path
led = Path(os.environ["RETRO_HOME"]) / "metrics.jsonl"
out = []
for line in led.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    row = json.loads(line)
    for key in ["returned_nothing", "schema_rejections",
                "write_precondition_failures", "read_missing_path"]:
        row.pop(key, None)
    out.append(json.dumps(row))
led.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
python plugins/retro/bin/retro.py subagents --days 0; echo "exit=$?"
python plugins/retro/bin/retro.py extract --rebuild
```

Expected, in order: the one-day window prints either a report or the
no-transcripts line and exits 0 or 1 with no traceback; the stripped ledger prints
the WARNING naming every row and exits 0; the final rebuild restores the columns.
This is the only step in the plan that writes a ledger, and it writes under the
isolated `RETRO_HOME`.

- [ ] **Step 7: Confirm the other subcommands still work**

Run:

```bash
python plugins/retro/bin/retro.py pack --days 30; echo "pack exit=$?"
python plugins/retro/bin/retro.py skills --days 30; echo "skills exit=$?"
```

Expected: both run to completion with no traceback and the same output shape as
before this fix.

- [ ] **Step 8: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
feat(retro): subagents subcommand

Reports the four mechanical-failure counters over a window, with the turn-length
distribution and a ranked list of the most affected transcripts. Warns when the
ledger predates the columns, rather than reporting zeros as a clean window.

Prints numbers plus the already-redacted project field; no new message text
leaves the tool.
MSG
```

---

## Task 5: Record the measurement in the spec

**Files:**
- Modify: `docs/plans/2026-08-18-retro-measurement-fixes.md` — the Fix 4 table's
  `Read` row, the sentence below it, and the Verification table.

**Interfaces:**
- Consumes: the task 4 run output.
- Produces: nothing code depends on.

**This file is edited by all four sibling fixes.** Keep the edit to the three
spots named below so the merge is a small conflict at worst.

- [ ] **Step 1: Correct the two figures the re-derivation disproved**

In the Fix 4 table, change the `Read on a path that does not exist` measured cell
from `26 of 80 Read errors` to `26 of 76 Read errors`.

In the paragraph below the table, replace

```
but nearly all of both are commands that ran and returned non-zero
```

with

```
and while that holds for the second (71 of 75), for the first it is 205 of 428,
with most of the remainder refused by a permission rule rather than run
```

- [ ] **Step 2: Add the Fix 4 rows to the Verification table**

Append to the Verification table:

```markdown
| subagent transcripts returning nothing | 155 of 1,467 ledger rows (177 of 1,489 files; 22 files yield no row) |
| structured-output schema rejections | 53, across 24 transcripts |
| write-precondition failures | 20, across 19 transcripts |
| nonexistent-path reads | 26, across 14 transcripts |
| subagent turn length | median 11, p90 103, p95 142, max 707; 159 rows at 100+ |
| rebuild time with the lens | compare against the pre-change baseline from task 1 |
```

- [ ] **Step 3: Commit**

```bash
git add docs/plans/2026-08-18-retro-measurement-fixes.md
git commit -F - <<'MSG'
docs: record the subagent lens measurements, and two corrections

Re-derived every Fix 4 figure against the corpus. Two did not hold: failed Read
results number 76 not 80, and the claim that nearly all command failures are
non-zero exits holds for one shell and not the other.

Also records that the ledger sees 155 transcripts returning nothing, not 177 -
22 of those files produce no row at all.
MSG
```

---

## Merge ordering against the three sibling fixes

All four fixes edit `plugins/retro/bin/retro.py`. Every function this plan
modifies, and who else is expected in the same lines:

| Function or region | This fix does | Sibling likely in the same lines |
|---|---|---|
| module docstring | rewrites the subcommand list | **all three** — Fix 1 changes the `extract` output description, Fix 2 changes the "exactly one place" sentence, Fix 3 adds configuration. Textual conflict, no semantic conflict. |
| tuning constants block | adds `RUNAWAY_TURNS` | Fix 2c may add approval/question constants. Adjacent lines. |
| `COUNTERS` / `SUBAGENT_COUNTERS` | adds a **new list beside** `COUNTERS`; does not modify `COUNTERS` | Fix 2 appends `approval_turns` to `COUNTERS`. Adjacent lines, no semantic conflict — this fix stays out of `COUNTERS` on purpose so the pack table remains Fix 2's alone. |
| `tool_calls_of` | changes the yield from 2 elements to 3 | none expected. If a sibling adds a caller it must be updated; task 2 step 5 greps for exactly this. |
| `tool_errors_of` (new) | adds it | none. |
| `measure` — assistant branch | adds two lines, rebinds the tool-call loop variables | Fix 2 works on the user branch, Fix 3 on the tail. **Low but real.** |
| `measure` — user branch | adds a block after the existing `tool_errors` line | **Fix 2 rewrites this branch** (2b's marker guard, 2c's classification). **The highest-risk overlap in this fix.** The added block is independent of what Fix 2 changes — it reads the record's message content, not the classifier — so a conflict here resolves by keeping both. |
| `measure` — row tail | adds a second `for key in ...` loop after the `COUNTERS` loop | **Fix 3 rewrites the tail** (`rel`, `is_subagent`, the ledger key). The new loop appends after whatever Fix 3 produces. |
| `measure` — early return | untouched | **Fix 1 changes the return contract.** See the ordering risk below. |
| `main` | adds the `subagents` subparser | Fix 2d adds a `label` subparser, Fix 3 may add `extract` arguments. Adjacent lines. |
| `cmd_subagents`, `quantile`, `SUBAGENT_SIGNALS` (new) | adds them | none. |
| `totals`, `cmd_pack`, `moments`, `friction_score`, `is_error_record`, `read_records`, `cmd_extract`, `load_rows`, `cmd_skills` | **not touched by this fix** | Fixes 1, 2, and 3 own these outright. |

Suggested merge order: **Fix 3, then Fix 1, then Fix 2, then Fix 4.** This fix has
the smallest surface and the most dependence on the others, so taking it last
makes every conflict one it resolves rather than one it creates.

### Ordering risk: `is_subagent` and foreign roots

**If Fix 3 lands with `is_subagent` still wrong for a foreign root, this lens
reports on the wrong population.** `cmd_subagents` filters on exactly that flag.
Today `measure()` derives it from `path.relative_to(PROJECTS_DIR)` and falls back
to the bare filename, which drops the `subagents/` marker for any path outside the
default root — so every archived subagent transcript would be classified as a main
session and become invisible to this report, while the denominator it divides by
stays the live corpus only.

The failure is silent in both directions: the report would show a smaller
population and a lower share, and nothing in the output would say a root was
skipped. After Fix 3 lands, re-run task 4 step 4 with an extra root configured and
confirm the transcript count rises by the number of subagent files in that root.

### Ordering risk: Fix 1 and the 22 rowless files

Fix 1 reclassifies the files that produce no row from "unreadable" to "not a
transcript". Measured: those 22 files are exactly the subagent transcripts with
zero assistant records — the extreme case of "returned nothing". Fix 1 does **not**
make them produce rows, so `returned_nothing` stays 155 rather than 177 after Fix 1
lands. If Fix 1's implementation does start producing rows for them, this number
moves to 177 and the task 3 step 6 check must be re-derived, not adjusted.

---

## Verification

Run after all tasks, with `RETRO_HOME` set to the scratch directory.

| Check | Command | Expected |
|---|---|---|
| oracle and tool agree | task 1 step 3 vs task 3 step 6 | identical on every figure |
| `returned_nothing` | `subagents --days 0` | 155 of 1,467 rows (as of 2026-08-19) |
| `schema_rejections` | same | 53 occurrences across 24 rows |
| `write_precondition_failures` | same | 20 across 19 rows |
| `read_missing_path` | same | 26 across 14 rows |
| turn distribution | same | median 11, p90 103, p95 142, max 707 |
| command exits not counted | read the code | no counter reads a `Bash` or `PowerShell` result |
| `pack` unchanged | `pack --days 30` | trend table lists only `COUNTERS` names |
| `skills` unchanged | `skills --days 30` | same shape as before |
| rebuild time | `time extract --rebuild` | no measurable slowdown against the task 1 baseline |
| exit code | `subagents --days 0` | 1 when the window carries failures, 0 when it does not |
| stale ledger | task 4 step 6 | WARNING printed, exit 0, no false clean window |
| nothing written under the Claude configuration directory | `RETRO_HOME` set for every run | the tool writes only under `RETRO_HOME` |

---

## Questions for the operator

1. `RUNAWAY_TURNS` is set to 100 in this plan because p90 of the subagent population is 103 — is 100 the right line, or should it sit at p95 (142, which is 65 rows) or higher?
2. Should `returned_nothing` report 155 (what the ledger can see) or 177 (the file-level truth, which needs the 22 zero-record files to produce rows and therefore changes Fix 1's scope)?
3. Should a call refused by a permission or isolation rule count as a mistake category, given it is 217 of the 428 failed `Bash` results and is currently excluded alongside non-zero exits?
4. Should `read_missing_path` count only `Read`, or also the two `Edit` calls in the corpus that failed the same way?
5. Should the `subagents` report also show the main-session occurrences (6 schema rejections, 5 write-precondition failures, 72 nonexistent-path reads), or stay strictly a subagent lens as the spec scopes it?

## Operator rulings (2026-08-19) — these supersede the questions above

1. **`returned_nothing` reports 177, the file-level truth**, not the 155 the
   ledger can currently see. The 22 files that produce no row must therefore
   still be counted for this signal. Coordinate with Fix 1, whose reclassifying
   of those same 22 files is what makes them reachable.
2. **A call stopped by a workspace-isolation rule is a mistake category of its
   own.** Measured: 212 of 279 stopped calls, and the largest single cause. What
   the agent was attempting when stopped: git writes 45, redirecting output to a
   file 41, deleting files 31, build or test 30, creating a directory 16, other
   45. The agent was given an isolated workspace and reached outside it.
3. **A call the operator declined at the prompt is NOT a mistake** (22
   occurrences) — that is the operator exercising judgement, not the agent
   erring.
4. **An OS or filesystem permission error is not counted** (32 occurrences) —
   ambiguous between an agent error and a genuinely locked file.
5. **A non-zero exit code from a command that ran is not counted**, as already
   specified.
6. Runaway threshold stays at 100 turns. `Edit` counts alongside `Read` for
   nonexistent-path reads. The report stays subagent-scoped.
