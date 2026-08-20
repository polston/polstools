# Subagent lens (rebuild) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every transcript a set of mechanical-failure columns that were
each re-earned against the corpus, plus a `subagents` subcommand that reports
them over a window, so an edit to a dispatch instruction, a schema, or a
standing rule can be made and then checked by the same counter.

**Architecture:** `measure()` builds a map from tool-use block id to tool name as
it walks assistant records, then classifies each *failed* tool result by two
conditions together — the refusal text must start the result body, and the tool
that produced it must be one that emits that refusal. Each category becomes a
ledger column. A separate string column records how the run ended. A new
`cmd_subagents` reads the ledger only: it never stats a file, never walks the
filesystem, and never treats a file's absence from the ledger as a signal.

**Tech Stack:** Python 3 stdlib only. No test framework exists in this repo;
verification is by measurement, read-only against the live corpus, with
`RETRO_HOME` pointed at a throwaway directory.

**Spec:** this document. The predecessor spec section — "Fix 4 — a subagent
lens" in `docs/plans/2026-08-18-retro-measurement-fixes.md` — and the plan built
from it are superseded; the "What the first attempt got wrong" section below
records what was disproved, and Task 5 marks the old section.

## Global Constraints

- Stdlib only. No dependency, no build step, no daemon.
- No other project is named — not in code, comments, output, plan text, or
  commit messages. Every marker string below is harness text and names nothing.
- No absolute path from the author's machine enters a tracked file.
- Adding a column is a ledger-contract change: `extract --rebuild` is required
  before any trend over it means anything.
- Message text leaves the tool only through `redact()`. This change adds **no**
  new text egress: it counts, and the report prints numbers only.
- Exit codes: `0` ran clean and flagged nothing, `1` ran clean and flagged
  something, `2` could not run.
- **Never write anywhere under the user's Claude configuration directory.**
  Every verification run sets `RETRO_HOME` to a scratch directory outside any
  repository, and reads the corpus read-only. Never run `extract` with
  `RETRO_HOME` unset — it would write to the default work directory.
- **No counter may use file modification time as a proxy for when a session
  happened**, and no counter may be built from files that are absent from the
  ledger. Windowing is on the row's `date`, which comes from the first
  timestamp inside the transcript.

---

## Corrections folded in at implementation

An independent recount of the corpus disproved parts of this plan before it was
built. The code is the current truth; this section records what changed and why,
so the numbers below are read as the plan's state and not as the tool's.

1. **Every share had the wrong denominator.** The plan divided each signal by
   every subagent row. A workspace-guard refusal can only arise in a run that
   had an isolated workspace - measured, 465 of 1,492 rows - so dividing it by
   all of them states it at a third of its rate. That is the same defect this
   rebuild exists to fix, with the sign flipped. Each row now carries an
   `eligible` column naming the signals it could have produced, and the report
   divides by that population and prints what the population is.
2. **The failure-to-answer figure was 5 and is not.** Most of what it counted
   were transcripts still being written, one of them belonging to the session
   running the report. The report now drops the reporting session's rows, read
   from the environment and never printed, and says how many it dropped. On the
   run that verified this change the figure was 3, of which one was a
   same-day transcript from another live session; the settled figure was 2.
3. **The counts are not stable and this plan said they were.** They move
   between runs because the corpus is appended to while it is read. The report
   is stamped with the time it was taken and the rows it covers, and asserts no
   stability. The paragraph below claiming the category columns are stable is
   wrong and is marked so.
4. **Concentration was hidden.** Two of the seven counts are mostly one
   project: 77% of one and 61% of another come from a single project. A count
   that is one workflow repeating itself is not a pattern, and a reader given
   the count alone cannot tell. Each signal now reports the share of its
   occurrences held by its largest single project. No project is named - both
   constraints hold at once.
5. **The specificity claim credited the wrong half of the rule.** Measured, 73
   successful results contain one of these texts and **zero** start with one, so
   the start-of-body anchor alone excludes every one and the error flag excludes
   nothing the anchor has not. The gate is kept because it is the harness's own
   record that a call failed; the code comments say that rather than crediting
   it with specificity.
6. **The text-versus-structured split turned on record ordering.** Under this
   plan's rule, 43 rows that had handed a result back through a structured
   call read as `text`, because their last record happened to carry prose. The
   rule is now: a run that made a structured-result call at any point answered
   structurally. It does not depend on order.
7. **One category is real but not actionable.** `invalid_tool_input` is 23 of
   28 a tool input that would not parse - no instruction and no schema reaches
   it. It is kept, and the report marks per signal whether a person can act on
   it, so nobody spends an instruction edit on the one that cannot move.

Two smaller departures from the task list below: the primitives and the
`measure()` change landed as one commit rather than two, so no commit leaves the
tool unable to run; and the stale-ledger check inside `cmd_subagents` was not
written, because the ledger now carries a schema version and `load_rows` already
refuses a mismatched ledger with exit 2 - which was verified rather than assumed.


## What the first attempt got wrong

Recorded so the same categories are not re-imported by accident. Each line was
re-measured here, not taken on trust.

| First attempt claimed | What the corpus says |
|---|---|
| 155 subagent transcripts "returned nothing" (reported as 177) | All 155 produced assistant records and all 155 called tools. 152 handed a result back through a structured-output call. Zero were silent. Not a mistake category. |
| 212 transcripts "reached outside their isolated workspace", the top signal | Two unrelated refusals sharing an opening sentence. 37 name a target outside the workspace; 175 are the guard declining because it could not statically verify a command's shape — those may never have left the workspace at all. One further block was a command echoing a refusal it had read out of a file. |
| 22 extra files held no conversation and were failed runs | They are workflow run journals carrying 771 result records. The branch already classifies them as `not-transcript`; a verified `extract --rebuild` here reports `transcripts: 1943  measured: 1921  not-transcripts: 22  unreadable: 0`. |
| A share column | Divided a numerator counted over ledger rows by a denominator counted over rows plus non-ledger files. Every share in this plan is rows-carrying-the-signal over rows-in-the-same-population. |
| One counter walked files absent from the ledger and windowed them by file modification time | Reported 74 where the answer was 8, and counted transcripts the reporting session had just created. Nothing here reads a file's mtime, and nothing treats absence from the ledger as evidence. |

## What the corpus supports

Corpus at time of measurement (2026-08-20): 1,943 transcript files; 1,498 under a
`subagents/` path, of which 22 hold no conversation, leaving **1,476 subagent
ledger rows**. Main-session figures are given for contrast only.

**The counting rule, for every category below.** A failure is counted when all
three hold: the result block's `is_error` is exactly `True`; the refusal text
**starts** the result body, after stripping the harness's `<tool_use_error>`
wrapper; and the tool that produced it — resolved from the result's tool-use id —
is one that emits that refusal.

**How each marker was checked for specificity.** Four measurements, run over the
whole corpus, not one:

1. **The same text on successful results.** Agents read and grep transcripts, so
   the marker texts appear inside results that did not fail: the schema text 10
   times, the unread-file text 9, the missing-file text 10, on subagent
   transcripts alone. Requiring `is_error is True` excludes all of them.
2. **The same text quoted mid-body by a command's own output.** Exactly one
   failed result in the subagent corpus is a command that printed an isolation
   refusal it had grepped out of a file. Anchoring to the start of the body
   excludes it; a substring test counts it as a real refusal.
3. **The same text from a different tool.** Measured per tool, never assumed:
   the unread-file refusal comes from `Write` 20 times and `Edit` 8 times; the
   missing-path refusal from `Read` 26 times and `Grep` 8; the workspace
   refusals from four different tools. A text-only counter would attribute all
   of these to one tool.
4. **The anchored rule against successful results.** With `is_error is True`,
   the start-of-body anchor and tool attribution all applied, the number of
   *successful* results matching any category, corpus-wide, is **0**.

### Proposed categories

Occurrences count failed result blocks. Transcripts count distinct subagent
ledger rows carrying at least one. Projects counts distinct project directories,
as a concentration check — a signal confined to one project is one workflow's
quirk, not a general phenomenon.

| Column | Counted when | Occurrences | Transcripts | Share of 1,476 (WRONG - correction 1) | Projects | Main-session occurrences |
|---|---|---|---|---|---|---|
| `workspace_shape_unverifiable` | body starts with the workspace-isolation sentence, does **not** mention the shared checkout, and says the effect could not be verified | 175 | 97 | 6.6% | 5 | 26 |
| `schema_rejected` | `StructuredOutput`, body starts `Output does not match required schema` | 53 | 24 | 1.6% | 3 | 7 |
| `workspace_target_outside` | body starts with the workspace-isolation sentence **and** names the shared checkout | 37 | 11 | 0.7% | 4 | 14 |
| `missing_path_target` | `Read` body starts `File does not exist.` (26) or `Grep` body starts `Path does not exist` (8) | 34 | 21 | 1.4% | 7 | 87 |
| `unread_before_write` | `Write` or `Edit`, body starts `File has not been read yet.` | 28 | 26 | 1.8% | 6 | 35 |
| `invalid_tool_input` | any tool, body starts `InputValidationError` | 28 | 25 | 1.7% | 4 | 10 |
| `search_pattern_rejected` | `Grep`, body starts `Search failed` | 11 | 10 | 0.7% | 4 | 1 |

Notes that must survive into the code comments:

- **`workspace_shape_unverifiable` is not evidence that anything left the
  workspace.** It counts commands whose shape the guard could not check —
  compound commands, a command that sets HOME, a wrapper whose effect on what it
  wraps is opaque, a path computed at runtime. It is actionable exactly because
  of that: a standing rule asking for plain single commands should move it, and
  this counter is how that gets checked. It is the largest signal in the corpus
  and the one most likely to be misread, so the report prints its meaning beside
  the number.
- **`workspace_target_outside` is concentrated**: 37 occurrences in 11
  transcripts across 4 projects. Real, but a handful of runs.
- **`unread_before_write` covers `Edit` as well as `Write`.** The first attempt
  scoped it to `Write` and then noted that another tool emits the same refusal 8
  times. Those 8 are the same mistake and are counted.
- **`invalid_tool_input`** is the tool call being rejected before it ran: 23 are
  a tool input that would not parse as JSON, 3 an unknown parameter, 2 the rest.
  All 28 measured bodies begin with the same validation prefix.
- **`search_pattern_rejected`** is thin (11) and is proposed only because it is
  cleanly detectable and directly actionable. See the operator questions.

### How a run ended — one string column, not a failure counter

Replaces "returned nothing". Exactly one value per row, precedence in this
order. Measured over the 1,476 subagent rows:

| `ending` | Meaning | Rows |
|---|---|---|
| `structured` | final assistant record handed a result back through a structured-result tool | 730 |
| `text` | final assistant record carried prose | 722 |
| `interrupted` | the caller stopped the run | 19 |
| `unanswered` | a tool call was issued and its result never arrived | 3 |
| `silent` | stopped after a completed tool call without answering | 2 |

**The count of "the agent failed to answer" given here as 5 is itself wrong —
see correction 2.** It was — the
`unanswered` and `silent` rows. `interrupted` is the caller's action, not the
agent's mistake, and is reported separately rather than folded in. The two
result-bearing tools are named in one constant so a third can be added when the
harness grows one; today they are the only tools through which a result is
handed back rather than written as prose.

**A transcript still being written has a provisional `ending`, and the corpus
contains the reporting session's own.** Observed directly while measuring: three
runs of the same oracle over the same corpus returned `silent=2 text=722`, then
`silent=1 text=723`, then `silent=2 text=722` again — a transcript that was
mid-write when the middle run read it. The seven category counts were identical
across all three runs; only the endings and the turn quantiles moved. So:

- **This claim is false — see correction 3.** The category columns are not
  stable against an in-flight transcript either; a recount of the same
  population moved the largest signal. What is true is the second half: the
  `ending` column and the turn distribution are re-derived on the next
  `extract` because the file's size and mtime move, which is what the extract
  fingerprint is for — that is cache invalidation, not dating a session.
- Never read a one-run difference in `ending` as a change in behaviour. Compare
  windows that are closed, or accept a drift of one or two rows.
- This is the honest, bounded version of the self-counting problem the first
  attempt hit. It cannot be removed by excluding recent files without
  reintroducing modification time as a filter, which the constraints forbid.
  Operator question 6 asks whether to exclude the reporting session by id.

### Run length — a distribution, not a counter

Over the 1,476 subagent rows: median 11 turns, p90 105, p95 142, max 707. 162
rows at 100 turns or more, 66 at 150 or more, 34 at 200 or more. This uses the
existing `turns` column and adds no new one. No threshold is written into the
code, because no measurement in the corpus makes one turn count the boundary of
a mistake — see the operator questions.

### Measured, and deliberately not proposed

| Phenomenon | Measured | Why it is not a category |
|---|---|---|
| A command that ran and returned a non-zero exit | 205 of 441 failed `Bash` results, 71 of 75 failed `PowerShell` | The command ran. The exit code is the answer. |
| The operator declining a tool use | 10 on subagent transcripts | An operator decision, not an agent mistake. |
| A permission rule declining a tool | 10 on subagent transcripts | A policy decision about the environment. |
| A guard telling a subagent to return findings as text instead of writing a report file | 5 occurrences, 5 transcripts, **1 project** | Genuinely detectable and exactly what an instruction edit fixes, but confined to one project's own guard. Operator question 3. |
| `Edit` whose target string was not found | 5 on subagent transcripts (31 on main) | Real, but almost entirely a main-session phenomenon; below any useful floor here. |
| `Edit` on a file changed since it was read | 2 on subagent transcripts | Same. |
| A read whose content exceeded the token ceiling | 8 on subagent transcripts | Arguably a planning mistake, but the same refusal follows from a file simply being large; the transcript cannot separate the two. |

### Phenomena that cannot be measured from a transcript — stated plainly

1. **Whether an answer was good.** Out of scope by instruction, and there is no
   marker for it.
2. **Whether a subagent followed its dispatch instruction.** A transcript records
   what the agent did, not what it was told to do relative to what it should
   have done. The single exception is where a guard refuses a specific violation
   and says so — which is why the report-file refusal is measurable and the rest
   of instruction compliance is not. Do not build a proxy for this.
3. **Whether a command the guard could not verify would actually have left the
   workspace.** The command did not run. The transcript holds the refusal and
   the command text, and nothing that says what it would have done.
4. **Whether repeated work was wasted.** The existing `tool_retries` column
   counts a repeated call signature, which covers a legitimate re-run after an
   edit just as well as a pointless retry. It is not promoted into this lens.
5. **When a session happened, for a transcript that has no timestamps.** Rows
   fall back to an empty `date` and drop out of any window. That is correct;
   inferring the date from the file's modification time is the defect this
   rebuild exists to remove.

---

## File Structure

One code file changes: `plugins/retro/bin/retro.py`. One doc file changes:
`docs/plans/2026-08-18-retro-measurement-fixes.md`.

| Region of `retro.py` (line numbers at branch tip) | What it becomes |
|---|---|
| module docstring, lines 2-20 | lists four subcommands instead of two |
| schema block, lines 71-74 | `COUNTERS` unchanged; `SUBAGENT_COUNTERS` and `ENDINGS` added beside it |
| `tool_calls_of`, lines 142-151 | yields the block id as well as name and signature |
| new section after `signature`, line 172 | markers, `failure_body`, `classify_failure`, `failed_results_of`, `prose_of`, `result_call_in` |
| `measure`, lines 280-400 | builds the id map, counts the categories, writes the new columns |
| after `cmd_skills`, line 690 | `cmd_subagents` and `quantile` |
| `main`, lines 693-716 | `subagents` subparser |

## Task dependencies and what runs concurrently

- **Task 1** (oracle) depends on nothing. Start it first; Tasks 3 and 4 verify
  against its output.
- **Tasks 2 → 3 → 4** are strictly serial: all three edit the same function
  region of the same file, and Task 2 deliberately leaves a call site broken for
  Task 3 to fix. They are a single lane. This is a real file-and-line conflict,
  not an unexamined default.
- **Task 5** touches only a documentation file and shares no line with Tasks
  2-4. Run it in its own worktree lane, forked from the same branch tip,
  concurrently with the 2→3→4 lane, merging back when its diff is reviewed.

So: dispatch Task 1 and Task 5 at the same time as Task 2, and run 3 and 4
behind 2.

---

## Task 1: Freeze an independent oracle

The repo has no test suite, so the failing test is an oracle that measures the
corpus by a second, independently written path. It lives in scratch, is never
committed, and writes nothing inside the repo or under the user's Claude
configuration directory.

**Files:**
- Create: `$SCRATCH/oracle_subagents.py`, where `$SCRATCH` is a directory
  outside any git repository, chosen by the implementer. Not committed.
- Read only: the transcript corpus.

**Interfaces:**
- Consumes: nothing.
- Produces: a printed block of numbers that Tasks 3 and 4 must reproduce exactly
  — seven category counts with their transcript counts, the five `ending`
  counts, the subagent row count, and the turn quantiles.

- [ ] **Step 1: Choose a scratch directory and export it**

```bash
export SCRATCH="<a directory outside any git repository>"
export RETRO_HOME="$SCRATCH/retro-home"
mkdir -p "$RETRO_HOME"
echo "RETRO_HOME=$RETRO_HOME"
```

Every later step assumes both are set. If `RETRO_HOME` is unset, `extract`
writes to the default work directory. Do not run it without it.

- [ ] **Step 2: Write the oracle**

```python
# $SCRATCH/oracle_subagents.py - read-only measurement, never committed.
import json, re, statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path.home() / ".claude" / "projects"
PREFIX = "<tool_use_error>"
RESULT_TOOLS = {"StructuredOutput", "ReportFindings"}
INTERRUPT = re.compile(r"\[request interrupted", re.I)
ISO_HEADS = ("This session is isolated in the worktree",
             "This agent is isolated in the worktree")
MARKERS = [
    ("schema_rejected", {"StructuredOutput"},
     "Output does not match required schema"),
    ("unread_before_write", {"Write", "Edit"}, "File has not been read yet."),
    ("missing_path_target", {"Read"}, "File does not exist."),
    ("missing_path_target", {"Grep"}, "Path does not exist"),
    ("invalid_tool_input", None, "InputValidationError"),
    ("search_pattern_rejected", {"Grep"}, "Search failed"),
]


def head(body):
    b = body.lstrip()
    return b[len(PREFIX):].lstrip() if b.startswith(PREFIX) else b


def classify(tool, body):
    for column, tools, marker in MARKERS:
        if body.startswith(marker) and (tools is None or tool in tools):
            return column
    if body.startswith(ISO_HEADS):
        if "shared checkout" in body or "shared-checkout" in body:
            return "workspace_target_outside"
        if "verif" in body:
            return "workspace_shape_unverifiable"
    return None


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


def prose(message):
    content = (message or {}).get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(b.get("text") or "" for b in content
                     if isinstance(b, dict) and b.get("type") == "text")


files = [p for p in sorted(ROOT.rglob("*.jsonl"))
         if "subagents/" in p.relative_to(ROOT).as_posix()]
occ = Counter()
rows_with = defaultdict(set)
endings = Counter()
on_ok = 0
turns = []
rows = 0
for path in files:
    by_id, open_ids = {}, set()
    assistants, last, interrupted = 0, None, False
    hits = Counter()
    for rec in records(path):
        if not isinstance(rec, dict):
            continue
        kind = rec.get("type")
        message = rec.get("message") or {}
        content = message.get("content")
        if kind == "assistant":
            assistants += 1
            last = message
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        by_id[b.get("id")] = b.get("name") or ""
                        open_ids.add(b.get("id"))
        elif kind == "user":
            if INTERRUPT.search(prose(message)):
                interrupted = True
            if not isinstance(content, list):
                continue
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                open_ids.discard(b.get("tool_use_id"))
                body = b.get("content")
                if not isinstance(body, str):
                    continue
                column = classify(by_id.get(b.get("tool_use_id"), ""), head(body))
                if not column:
                    continue
                if b.get("is_error") is True:
                    hits[column] += 1
                else:
                    on_ok += 1
    if assistants == 0:
        continue
    rows += 1
    turns.append(assistants)
    for column, n in hits.items():
        occ[column] += n
        rows_with[column].add(str(path))
    last_content = (last or {}).get("content")
    if prose(last).strip():
        endings["text"] += 1
    elif isinstance(last_content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_use"
            and b.get("name") in RESULT_TOOLS for b in last_content):
        endings["structured"] += 1
    elif interrupted:
        endings["interrupted"] += 1
    elif open_ids:
        endings["unanswered"] += 1
    else:
        endings["silent"] += 1

turns.sort()
q = lambda f: turns[int(round((len(turns) - 1) * f))]
print(f"subagent rows {rows}")
for column in sorted(occ, key=lambda k: -occ[k]):
    print(f"{column} {occ[column]} across {len(rows_with[column])} transcripts")
print("endings " + " ".join(f"{k}={v}" for k, v in sorted(endings.items())))
print(f"turns median {statistics.median(turns)} p90 {q(0.90)} "
      f"p95 {q(0.95)} max {max(turns)} ge100 {sum(1 for t in turns if t >= 100)}")
print(f"anchored rule matched on a SUCCESSFUL result {on_ok} times")
```

- [ ] **Step 3: Run the oracle and record its output**

Run: `python "$SCRATCH/oracle_subagents.py"`

Expected, as measured 2026-08-20 (the corpus grows, so these drift; what Tasks 3
and 4 must reproduce is *this* run's numbers, not these):

```
subagent rows 1476
workspace_shape_unverifiable 175 across 97 transcripts
schema_rejected 53 across 24 transcripts
workspace_target_outside 37 across 11 transcripts
missing_path_target 34 across 21 transcripts
unread_before_write 28 across 26 transcripts
invalid_tool_input 28 across 25 transcripts
search_pattern_rejected 11 across 10 transcripts
endings interrupted=19 silent=2 structured=730 text=722 unanswered=3
turns median 11.0 p90 105 p95 142 max 707 ge100 162
anchored rule matched on a SUCCESSFUL result 0 times
```

The last line is the specificity check and **must be 0**. If it is not, a marker
has stopped being specific and the category it belongs to has to be re-earned
before any code is written.

Run this twice, a minute apart, and expect the seven category lines to be
identical both times while the `endings` line and the turn quantiles may move by
one or two. That is a transcript being written while the oracle reads it, and it
is why Tasks 3 and 4 compare against a *recorded* oracle run rather than a fresh
one: re-run the oracle immediately before each comparison and use that output.

- [ ] **Step 4: Capture the pre-change baseline**

```bash
cd <worktree root>
time python plugins/retro/bin/retro.py extract --rebuild; echo "exit=$?"
```

Expected: `transcripts: 1943  measured: 1921  unchanged: 0  not-transcripts: 22
unreadable: 0`, `exit=0`, in roughly 5.5 seconds. Record the wall clock — Task 3
compares against it.

- [ ] **Step 5: No commit**

Nothing in the repository changed. Do not commit scratch files.

---

## Task 2: Detection primitives

**Files:**
- Modify: `plugins/retro/bin/retro.py` — the schema block (lines 71-74),
  `tool_calls_of` (lines 142-151), and a new section after `signature`.

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, for Task 3:
  - `tool_calls_of(message) -> Iterator[tuple[str, str, str]]` yielding
    `(block_id, tool_name, input_signature)`. **The arity changed from 2 to 3
    and the new element is first.** Its only call site is inside `measure`.
  - `failure_body(block) -> str | None` — the refusal text of a failed
    `tool_result` block, wrapper stripped, or `None`.
  - `classify_failure(tool_name, body) -> str` — a column name, or `""`.
  - `failed_results_of(message, tool_by_id) -> Iterator[tuple[str, str]]`
    yielding `(tool_name, refusal_text)`.
  - `prose_of(message) -> str` — a message's text blocks only.
  - `result_call_in(message) -> bool`.
  - `SUBAGENT_COUNTERS: list[str]` — the seven column names, in order.
  - `ENDINGS: tuple[str, ...]` — the five `ending` values, in precedence order.

- [ ] **Step 1: Add the second schema list beside `COUNTERS`**

Insert immediately after the `COUNTERS` list (after line 74):

```python
# The subagent lens. Same ledger contract as COUNTERS - each name is a column,
# and adding one means an extract --rebuild - but a separate list, so the pack's
# trend table and per-session line, which iterate COUNTERS, are untouched.
#
# Every one of these was re-earned against the corpus rather than carried over
# from an earlier attempt whose categories a recount disproved. The measurement,
# the specificity checks, and the categories that failed those checks are in
# docs/plans/2026-08-20-plan-subagent-lens-rebuild.md.
SUBAGENT_COUNTERS = ["schema_rejected", "unread_before_write",
                     "missing_path_target", "invalid_tool_input",
                     "search_pattern_rejected", "workspace_target_outside",
                     "workspace_shape_unverifiable"]

# How a run ended, in precedence order. One value per row, in the `ending`
# column. The first two are a result delivered; only the last two are the agent
# failing to answer, and `interrupted` is the caller's doing, not the agent's.
ENDINGS = ("text", "structured", "interrupted", "unanswered", "silent")
```

- [ ] **Step 2: Give `tool_calls_of` the block id**

Replace lines 142-151 with:

```python
def tool_calls_of(message):
    """Yield (block_id, tool_name, input_signature) for each tool use.

    The id is the only link between a call and the result it produced: a
    tool_result block carries the id and never the name. Attribution by name is
    what the mechanical-failure columns need, because the same refusal text is
    emitted by more than one tool.
    """
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

- [ ] **Step 3: Add the markers and the classifier**

Insert a new section immediately after `signature` (after line 172):

```python
# --- Mechanical failures ---------------------------------------------------
#
# One rule for every column: a failure is counted only when the refusal text
# STARTS the result body and the tool that produced it is one that emits that
# refusal. Neither half is optional, and both were measured:
#
#   - Starts-with, because a command's own output can quote a refusal. Exactly
#     one failed result in the subagent corpus is a command that printed an
#     isolation refusal it had grepped out of a file. A substring test counts it
#     as a real refusal; this one does not.
#   - Tool-attributed, because the same text means different mistakes depending
#     on who emitted it: the unread-file refusal comes from Write 20 times and
#     Edit 8 times, the missing-path refusal from Read 26 and Grep 8.
#
# Checked the other way too. The same marker texts appear inside SUCCESSFUL tool
# results - the schema text 10 times, the unread text 9, the missing-file text
# 10, on subagent transcripts alone - because agents read and grep transcripts.
# Requiring is_error is True excludes every one, and with all three conditions
# applied the rule matches zero successful results corpus-wide.

TOOL_ERROR_PREFIX = "<tool_use_error>"

# The workspace guard's two refusal families share an opening sentence and are
# told apart by what follows. They are DIFFERENT phenomena and never share a
# counter: one names a target outside the workspace, the other is the guard
# declining because it could not statically verify the command's shape. The
# second is not evidence that anything left the workspace.
ISOLATION_HEADS = ("This session is isolated in the worktree",
                   "This agent is isolated in the worktree")
SHARED_CHECKOUT = ("shared checkout", "shared-checkout")

# (column, tools that emit it or None for any, text that must start the body)
FAILURE_MARKERS = (
    ("schema_rejected", ("StructuredOutput",),
     "Output does not match required schema"),
    ("unread_before_write", ("Write", "Edit"), "File has not been read yet."),
    ("missing_path_target", ("Read",), "File does not exist."),
    ("missing_path_target", ("Grep",), "Path does not exist"),
    ("invalid_tool_input", None, "InputValidationError"),
    ("search_pattern_rejected", ("Grep",), "Search failed"),
)

# The tools through which a result is handed back instead of written as prose.
# An agent that used one of these did answer; it just did not answer in text.
RESULT_TOOLS = ("StructuredOutput", "ReportFindings")


def failure_body(block):
    """The refusal text of a failed tool_result block, or None.

    is_error is compared to True exactly: measured corpus-wide it is only ever
    True, False or absent, so an identity test loses nothing and cannot be
    surprised by a truthy string later. A non-string body is skipped rather
    than serialised - JSON-dumping it would invent text for a marker to match.
    """
    if not isinstance(block, dict) or block.get("type") != "tool_result":
        return None
    if block.get("is_error") is not True:
        return None
    body = block.get("content")
    if not isinstance(body, str):
        return None
    body = body.lstrip()
    if body.startswith(TOOL_ERROR_PREFIX):
        body = body[len(TOOL_ERROR_PREFIX):].lstrip()
    return body


def classify_failure(tool, body):
    """Which mechanical-failure column a failed result belongs in, or "".

    `tool` is the name resolved from the result's tool-use id; an id with no
    matching call yields "" and therefore matches no tool-scoped category.
    """
    for column, tools, marker in FAILURE_MARKERS:
        if body.startswith(marker) and (tools is None or tool in tools):
            return column
    if body.startswith(ISOLATION_HEADS):
        if any(phrase in body for phrase in SHARED_CHECKOUT):
            return "workspace_target_outside"
        if "verif" in body:
            return "workspace_shape_unverifiable"
    return ""


def failed_results_of(message, tool_by_id):
    """Yield (tool_name, refusal_text) for each failed tool result."""
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        body = failure_body(block)
        if body is None:
            continue
        yield tool_by_id.get(block.get("tool_use_id"), ""), body


def prose_of(message):
    """A message's text blocks only.

    Deliberately not text_of(), which also flattens tool_result bodies into the
    string. That is right for quoting a turn and wrong for asking whether the
    agent itself said anything: the interrupt marker appears in the text_of()
    rendering of 41 subagent transcripts and in the text blocks of 19, the
    difference being transcripts that read a file which mentioned it.
    """
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(block.get("text") or "" for block in content
                     if isinstance(block, dict) and block.get("type") == "text")


def result_call_in(message):
    """Did this message hand a result back through a structured-result tool?"""
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") == "tool_use"
               and block.get("name") in RESULT_TOOLS for block in content)
```

- [ ] **Step 4: Prove the module parses and the arity change is contained**

```bash
python -c "import ast; ast.parse(open('plugins/retro/bin/retro.py', encoding='utf-8').read())"
grep -n "tool_calls_of" plugins/retro/bin/retro.py
```

Expected: the parse is silent, and `grep` prints exactly two lines — the
definition and the one call site inside `measure`. If it prints three, another
change added a caller; update it to the three-element form before going on.

- [ ] **Step 5: Confirm the call site is where this plan says it is**

Run: `python plugins/retro/bin/retro.py extract --rebuild`

Expected: it **fails** with a `ValueError` about unpacking, because `measure`
still destructures two values. That failure is the point — it proves the only
call site is the one Task 3 rewrites. Do not fix it here.

- [ ] **Step 6: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
feat(retro): detection primitives for the rebuilt subagent lens

tool_calls_of now yields the tool-use block id, so a failed tool result can be
attributed to the tool that produced it. Adds the anchored refusal markers, the
classifier that requires marker-at-start AND tool attribution together, the
SUBAGENT_COUNTERS column list, and the ENDINGS values.

Every category was re-measured against the corpus rather than carried over.
measure() is updated in the next commit; the call site is deliberately left
broken here so the arity change is visible in one place.
MSG
```

---

## Task 3: Count the categories in `measure()`

**Files:**
- Modify: `plugins/retro/bin/retro.py` — `measure` (lines 280-400).

**Interfaces:**
- Consumes: `tool_calls_of`, `failed_results_of`, `classify_failure`,
  `prose_of`, `result_call_in`, `SUBAGENT_COUNTERS`, `ENDINGS` from Task 2.
- Produces, for Task 4: every ledger row carries the seven names in
  `SUBAGENT_COUNTERS` as integers, and an `ending` string.

**Why the columns are computed for every transcript, not only subagent ones:**
`is_subagent` is derived at the end of `measure()`, from the path, after the
record loop. Gating the loop on it would mean hoisting that derivation above the
loop for no benefit. Counting unconditionally costs one dictionary per
transcript; `cmd_subagents` filters. Main-session rows carry values too, and
this change reports them nowhere.

- [ ] **Step 1: Add the new locals**

In `measure`, alongside the existing local initialisers (after
`prev_skill = None`), add:

```python
    tool_by_id = {}
    open_tool_ids = set()
    last_assistant = None
    caller_interrupted = False
```

- [ ] **Step 2: Record the id map and the last assistant message**

In the `elif rtype == "assistant":` branch, replace the message and tool-call
lines with:

```python
            msg = rec.get("message") or {}
            last_assistant = msg
            usage = msg.get("usage") or {}
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
            cache_read += int(usage.get("cache_read_input_tokens") or 0)
            body = text_of(msg)
            prior_assistant_chars = len(body)
            for block_id, name, sig in tool_calls_of(msg):
                tool_by_id[block_id] = name
                open_tool_ids.add(block_id)
                m["tool_calls"] += 1
                key = (name, sig)
                if sig and key in seen_sigs:
                    m["tool_retries"] += 1
                seen_sigs.add(key)
```

- [ ] **Step 3: Classify failed results and track how the run ended**

Immediately after the existing block:

```python
        # Only user records carry tool results; checking the rest re-walks
        # message content for nothing.
        if rtype == "user" and is_error_record(rec):
            m["tool_errors"] += 1
```

add:

```python
        if rtype == "user":
            user_msg = rec.get("message") or {}
            # An interrupt is the caller stopping the run. Read from the text
            # blocks only - a tool result that happens to quote the marker is
            # not the caller interrupting anything.
            if _INTERRUPT.search(prose_of(user_msg)):
                caller_interrupted = True
            user_content = user_msg.get("content")
            if isinstance(user_content, list):
                for block in user_content:
                    if isinstance(block, dict) \
                            and block.get("type") == "tool_result":
                        open_tool_ids.discard(block.get("tool_use_id"))
            # Mechanical failures the harness refused on its own terms. A
            # non-zero command exit is NOT one of these: the command ran, and
            # the exit code is information, not an agent mistake. Neither is a
            # tool use the operator declined, nor one a permission rule
            # declined - both are decisions about the environment.
            for name, refusal in failed_results_of(user_msg, tool_by_id):
                column = classify_failure(name, refusal)
                if column:
                    m[column] += 1
```

- [ ] **Step 4: Write the new columns onto the row**

After the existing `for key in COUNTERS:` loop and before `return row`, add:

```python
    # How the run ended, in ENDINGS precedence order. Answering through a
    # structured-result call is answering: 730 of 1,476 subagent transcripts
    # end that way and carry no assistant prose at all. Treating them as
    # silence is what the first attempt did, and it was wrong 152 times out of
    # 155. Only `unanswered` and `silent` are the agent failing to answer, and
    # together they were 5.
    if prose_of(last_assistant).strip():
        ending = "text"
    elif result_call_in(last_assistant):
        ending = "structured"
    elif caller_interrupted:
        ending = "interrupted"
    elif open_tool_ids:
        ending = "unanswered"
    else:
        ending = "silent"
    row["ending"] = ending
    for key in SUBAGENT_COUNTERS:
        row[key] = m[key]
```

- [ ] **Step 5: Rebuild into the isolated work directory**

```bash
echo "RETRO_HOME=$RETRO_HOME"   # must be set, and must not be under ~/.claude
time python plugins/retro/bin/retro.py extract --rebuild; echo "exit=$?"
```

Expected: the same `transcripts: / measured: / unchanged: / not-transcripts: /
unreadable:` line as the Task 1 baseline, `exit=0`, and no measurable slowdown
against the recorded wall clock. A consistently slower run means the id map is
being rebuilt somewhere it should not be.

- [ ] **Step 6: Check the ledger against the oracle**

```bash
python - <<'PY'
import json, os, statistics
from collections import Counter
from pathlib import Path
COLUMNS = ["workspace_shape_unverifiable", "schema_rejected",
           "workspace_target_outside", "missing_path_target",
           "unread_before_write", "invalid_tool_input",
           "search_pattern_rejected"]
rows = [json.loads(l) for l in
        (Path(os.environ["RETRO_HOME"]) / "metrics.jsonl")
        .read_text(encoding="utf-8").splitlines() if l.strip()]
sub = [r for r in rows if r.get("is_subagent")]
print("subagent rows", len(sub))
for key in COLUMNS:
    total = sum(int(r.get(key) or 0) for r in sub)
    carrying = sum(1 for r in sub if int(r.get(key) or 0))
    print(f"{key} {total} across {carrying} transcripts")
endings = Counter(r.get("ending") for r in sub)
print("endings " + " ".join(f"{k}={v}" for k, v in sorted(endings.items())))
turns = sorted(int(r.get("turns") or 0) for r in sub)
q = lambda f: turns[int(round((len(turns) - 1) * f))]
print(f"turns median {statistics.median(turns)} p90 {q(0.90)} "
      f"p95 {q(0.95)} max {max(turns)} ge100 {sum(1 for t in turns if t >= 100)}")
PY
```

Expected: every number equals the Task 1 oracle run, exactly. As measured
2026-08-20 that is 1,476 subagent rows; 175/97, 53/24, 37/11, 34/21, 28/26,
28/25, 11/10; endings `interrupted=19 silent=2 structured=730 text=722
unanswered=3`; turns median 11, p90 105, p95 142, max 707, 162 at 100 or more.

If any number differs, the oracle and the implementation disagree about a
definition. Settle which is right before continuing. Do not adjust the oracle to
match the tool.

- [ ] **Step 7: Confirm `pack` is unchanged**

```bash
python plugins/retro/bin/retro.py pack --days 30; echo "exit=$?"
```

Expected: exits 0 or 1, and its trend table lists exactly the `COUNTERS` names —
none of the seven new ones, and no `ending`. That is the point of the separate
list.

- [ ] **Step 8: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
feat(retro): count re-earned mechanical failures in measure()

Seven ledger columns, each attributed to a specific tool through the tool-use
block id and anchored to the start of the refusal body, plus an `ending` column
recording how the run finished.

The two workspace-guard refusals are counted separately: one names a target
outside the workspace, the other is the guard declining because it could not
verify a command's shape. The second is not evidence that anything left the
workspace, and they never share a counter.

Answering through a structured-result call is answering. Only a run that ended
with an unanswered tool call, or stopped without answering at all, counts as a
failure to answer.

Non-zero command exits, operator refusals and permission refusals are
deliberately not counted.

Verified against an independent measurement of the corpus: 1,476 subagent rows,
175 / 53 / 37 / 34 / 28 / 28 / 11.

Requires extract --rebuild.
MSG
```

---

## Task 4: The `subagents` subcommand

**Files:**
- Modify: `plugins/retro/bin/retro.py` — a new block after `cmd_skills`, `main`,
  and the module docstring (lines 2-20).

**Interfaces:**
- Consumes: `SUBAGENT_COUNTERS`, `ENDINGS`, `load_rows`, the `EXIT_*` constants.
- Produces: `cmd_subagents(args)` returning an exit code;
  `quantile(sorted_values, fraction)`.

- [ ] **Step 1: Write the command**

Insert after `cmd_skills`, before `main`:

```python
# --- subagents -------------------------------------------------------------

# What each column means when it fires, printed beside the number so the report
# reads without this file open. The wording matters for the two workspace
# signals: one is a target outside the workspace, the other is a command the
# guard could not check, and reading the second as the first is the mistake this
# report exists to stop making.
SUBAGENT_SIGNALS = [
    ("workspace_shape_unverifiable",
     "command shape the workspace guard could not verify (NOT proof it left)"),
    ("workspace_target_outside", "named a target outside its workspace"),
    ("schema_rejected", "structured output rejected against its own schema"),
    ("missing_path_target", "read or searched a path that does not exist"),
    ("unread_before_write", "wrote or edited a file it had not read"),
    ("invalid_tool_input", "tool call rejected before it ran, on its input"),
    ("search_pattern_rejected", "search pattern, glob or file type rejected"),
]

ENDING_MEANING = {
    "text": "answered in text",
    "structured": "answered through a structured-result call",
    "interrupted": "the caller stopped the run",
    "unanswered": "a tool call never got its result",
    "silent": "stopped without answering",
}


def quantile(sorted_values, fraction):
    """Nearest-rank quantile over a pre-sorted list. Empty input has no
    quantile; callers guard."""
    return sorted_values[int(round((len(sorted_values) - 1) * fraction))]


def cmd_subagents(args):
    """Mechanical failures in subagent transcripts, over a window.

    Reads the ledger and nothing else. It never stats a file, never walks the
    corpus, and never treats a transcript's absence from the ledger as a
    signal - counting files the ledger had not caught up with, dated by their
    modification time, is what produced a 74 where the answer was 8. Windows
    are on the row's `date`, which comes from the first timestamp inside the
    transcript.
    """
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
    # which is indistinguishable from a clean window. Say so rather than lie,
    # and refuse outright when no row can answer.
    keys = set(SUBAGENT_COUNTERS) | {"ending"}
    stale = sum(1 for r in rows if not keys.issubset(r))
    if stale == len(rows):
        print("every row in this window predates these columns - run "
              "`extract --rebuild` first", file=sys.stderr)
        return EXIT_CANNOT_RUN

    print(f"# Subagent lens - {window}, {len(rows)} transcripts\n")
    if stale:
        print(f"WARNING: {stale} of {len(rows)} rows predate these columns and "
              f"count as zero below - run `extract --rebuild`.\n")

    print("## Mechanical failures\n")
    print("| signal | transcripts | share | occurrences |")
    print("|---|---|---|---|")
    flagged = 0
    tallies = []
    for key, meaning in SUBAGENT_SIGNALS:
        occurrences = sum(int(r.get(key) or 0) for r in rows)
        carrying = sum(1 for r in rows if int(r.get(key) or 0))
        flagged += occurrences
        tallies.append((carrying, occurrences, key, meaning))
    # Both columns count the SAME population - transcripts in this window - so
    # the share divides one population by itself.
    for carrying, occurrences, key, meaning in sorted(tallies, reverse=True):
        print(f"| {key} - {meaning} | {carrying} | "
              f"{carrying / len(rows) * 100:.1f}% | {occurrences} |")

    print("\nNot counted: a command that ran and returned a non-zero exit, a "
          "tool use the operator declined, and one a permission rule declined. "
          "None of the three is an agent mistake.")

    print("\n## How runs ended\n")
    print("| ending | transcripts | share |")
    print("|---|---|---|")
    endings = Counter(r.get("ending") or "?" for r in rows)
    for name in ENDINGS:
        count = endings.get(name, 0)
        print(f"| {name} - {ENDING_MEANING[name]} | {count} | "
              f"{count / len(rows) * 100:.1f}% |")
    no_answer = endings.get("unanswered", 0) + endings.get("silent", 0)
    print(f"\nFailed to answer: {no_answer}. Answering through a "
          f"structured-result call is answering, and an interrupted run is the "
          f"caller's doing.")

    turns = sorted(int(r.get("turns") or 0) for r in rows)
    print(f"\n## Length\n\nturns: median {quantile(turns, 0.5)}, "
          f"p90 {quantile(turns, 0.90)}, p95 {quantile(turns, 0.95)}, "
          f"max {turns[-1]}.")
    for threshold in (100, 150, 200):
        print(f"  {sum(1 for t in turns if t >= threshold)} transcripts at "
              f"{threshold} turns or more")
    print("\nA distribution, not a failure count: no turn number in this "
          "corpus marks a boundary between a long job and a runaway one.")

    return EXIT_FLAGGED if (flagged or no_answer) else EXIT_CLEAN
```

The report prints numbers only. It deliberately does **not** rank or name
individual transcripts: the `project` field carries directory paths belonging to
other work, and a ranked list is the output most likely to be copied somewhere
it must not go.

- [ ] **Step 2: Register the subparser**

In `main`, after the `skills` parser block:

```python
    p_sub = sub.add_parser("subagents",
                           help="mechanical failures in subagent transcripts")
    p_sub.add_argument("--days", type=int, default=30,
                       help="restrict to a window; 0 means all history")
    p_sub.set_defaults(func=cmd_subagents)
```

`--days 0` meaning all history matches the `skills` subcommand's convention.

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

```bash
python plugins/retro/bin/retro.py subagents --days 0; echo "exit=$?"
```

Expected: `1476 transcripts` (or the current count), no staleness warning, a
failures table whose seven occurrence figures equal the Task 1 oracle exactly
(175, 53, 37, 34, 28, 28, 11) with transcript counts 97, 24, 11, 21, 26, 25, 10;
an endings table reading text 722, structured 730, interrupted 19, unanswered 3,
silent 2, and "Failed to answer: 5"; a length section reading median 11, p90
105, p95 142, max 707 with 162 / 66 / 34 at the three thresholds; and `exit=1`.

One definitional difference to know about before chasing it: the oracle's median
comes from `statistics.median`, which averages the two middle values on an
even-sized population, and the report's comes from `quantile(turns, 0.5)`, which
picks one of them. They agree at 11 on this corpus because both middle values are
11. If they ever disagree by one, that is the definition, not a defect — do not
"fix" either side without saying which one the report should use.

- [ ] **Step 5: Check the stale-ledger behaviour**

```bash
python - <<'PY'
import json, os
from pathlib import Path
led = Path(os.environ["RETRO_HOME"]) / "metrics.jsonl"
out = []
for line in led.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    row = json.loads(line)
    for key in ["schema_rejected", "unread_before_write", "missing_path_target",
                "invalid_tool_input", "search_pattern_rejected",
                "workspace_target_outside", "workspace_shape_unverifiable",
                "ending"]:
        row.pop(key, None)
    out.append(json.dumps(row))
led.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
python plugins/retro/bin/retro.py subagents --days 0; echo "exit=$?"
python plugins/retro/bin/retro.py extract --rebuild
python plugins/retro/bin/retro.py subagents --days 0 > /dev/null; echo "exit=$?"
```

Expected, in order: the stripped ledger prints the "every row predates these
columns" message on stderr and `exit=2` — it refuses rather than reporting a
clean window; the rebuild restores the columns; the final run is `exit=1` again.
This is the only step in the plan that writes a ledger, and it writes under the
isolated `RETRO_HOME`.

- [ ] **Step 6: Check a narrow window**

```bash
python plugins/retro/bin/retro.py subagents --days 1; echo "exit=$?"
python plugins/retro/bin/retro.py subagents --days 0 | grep -c "%"
```

Expected: the one-day window prints either a report or the no-transcripts line,
with no traceback, and exits 0, 1 or 2; the grep confirms the share column
renders. If the one-day window is empty, that is a fact about the corpus, not a
failure.

- [ ] **Step 7: Confirm the other subcommands still work**

```bash
python plugins/retro/bin/retro.py pack --days 30; echo "pack exit=$?"
python plugins/retro/bin/retro.py skills --days 30; echo "skills exit=$?"
```

Expected: both run to completion with no traceback and the same output shape as
before this change.

- [ ] **Step 8: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
feat(retro): subagents subcommand

Reports the seven mechanical-failure columns over a window, how runs ended, and
the turn-length distribution. Every share divides one population by itself.

Reads the ledger and nothing else - it never stats a file and never treats a
transcript's absence from the ledger as a signal. Windows are on the row's date,
taken from timestamps inside the transcript, never from file modification time.

Refuses with exit 2 when every row in the window predates the columns, rather
than reporting zeros as a clean window.

Prints numbers only; no transcript is named and no message text leaves the tool.
MSG
```

---

## Task 5: Mark the disproved section of the older spec

Independent of Tasks 2-4 — different file, no shared line. Run it in its own
worktree lane, concurrently.

**Files:**
- Modify: `docs/plans/2026-08-18-retro-measurement-fixes.md` — the "Fix 4"
  section heading.

**Interfaces:**
- Consumes: the "What the first attempt got wrong" table in this plan.
- Produces: nothing code depends on.

- [ ] **Step 1: Mark the section superseded**

Immediately under the "Fix 4 — a subagent lens" heading, insert:

```markdown
> **Superseded 2026-08-20.** A recount against the corpus disproved this
> section's two largest categories: the transcripts counted as returning
> nothing had almost all answered through a structured-output call, and the
> single workspace counter conflated a command naming a target outside the
> workspace with the guard declining a command whose shape it could not verify.
> The rebuilt categories, the counting rule, and the specificity checks are in
> `docs/plans/2026-08-20-plan-subagent-lens-rebuild.md`. Read that instead; the
> numbers below are kept only as a record of what was disproved.
```

- [ ] **Step 2: Verify nothing shipped points at the old numbers**

```bash
grep -rn "returned_nothing\|reached outside" docs/ plugins/ README.md
```

Expected: hits only inside the two plan documents. Anything under `plugins/`
would be a counter that was never built and must not exist.

- [ ] **Step 3: Commit**

```bash
git add docs/plans/2026-08-18-retro-measurement-fixes.md
git commit -F - <<'MSG'
docs: mark the first subagent-lens design superseded

A recount against the corpus disproved its two largest categories. The rebuilt
categories and the checks each one had to pass are in the 2026-08-20 plan.
MSG
```

---

## Questions for the operator

1. `search_pattern_rejected` is 11 occurrences across 10 transcripts. It is
   cleanly detectable and directly actionable, but it is the thinnest signal
   proposed. Keep it, or drop it and leave six categories?
2. The turn-length section reports a distribution with no threshold, because
   nothing in the corpus marks where a long job becomes a runaway one. 162
   transcripts are at 100 turns or more, 66 at 150, 34 at 200. Do you want a
   fixed line drawn, and at which number?
3. A guard refuses a subagent that tries to write a report file instead of
   returning findings as text: 5 occurrences, 5 transcripts, all within a single
   project. It is exactly the kind of mistake a dispatch-instruction edit fixes,
   and it is the only measurable instance of instruction compliance in the whole
   corpus — but it is one project's local guard, so counting it corpus-wide
   would mostly measure whether that guard is installed. Include it as an eighth
   category, or leave it out?
4. Found while measuring, outside this plan's scope: the existing `interrupts`
   column reads its text through `text_of()`, which folds tool-result bodies
   into the string, so a transcript that merely read a file mentioning the
   interrupt marker is counted as interrupted. On subagent transcripts that is
   41 counted against 19 real. Fix it on this branch, or leave it for its own
   change?
5. The report prints a project *count* per signal but names no project, because
   the `project` field carries directory paths belonging to other work. Acting
   on a signal will eventually mean knowing where it comes from. Leave it as
   counts, or add an opt-in flag that prints the redacted project field?
6. The corpus contains the transcripts of whichever session is running the
   report, and a transcript still being written has a provisional `ending` — a
   run measured mid-write reads as `silent` and then as `text` once it finishes.
   The category counts are unaffected. Excluding by recency would mean filtering
   on modification time, which the constraints forbid; excluding by session id
   would mean the tool knowing which session invoked it, which it currently does
   not. Leave the drift and document it, or add a way to pass the current
   session id in and drop its rows?
