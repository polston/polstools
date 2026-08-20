# Retry metric and four cleanups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the retry counter mean something a recount can defend, make the
turn classifier read the whole assistant turn rather than its last record, stop
`pack` writing quoted user turns inside a git repository, and make the documents
this work falsifies true again.

**Architecture:** The retry signature stops normalising digits and whitespace
away and stops truncating, becoming an exact digest of the call's input; the
single `tool_retries` column splits into two disjoint columns — `repeat_calls`
(an identical call already made in this transcript) and `retries_after_error`
(the same, where the earlier identical call returned an error) — and
`friction_score` weights them 1 and 2. `measure` accumulates assistant text
across the records of one turn instead of overwriting it per record, and
`_moments` accumulates the same way so the two keep reading one rule. `cmd_pack`
gains the repository refusal its sibling already has. The design document and the
three skills are swept with `finding-what-a-change-made-false`.

**Tech Stack:** Python 3 standard library only. No test framework, no
dependency, no build step.

**Spec:** No separate spec exists for this change set; the four defects and the
measured baseline below stand in for one. Background the tasks argue from:
`docs/plans/2026-08-12-retro-design.md` (the metric definitions table and the
work-directory guarantee) and `docs/plans/2026-08-18-retro-measurement-fixes.md`
(the friction-score contribution finding). Read both before Task 1.

## Global Constraints

- Stdlib only. No dependency, no build step, no daemon.
- Nothing personally identifying, secret, or belonging to another project enters
  any file, example, comment, or commit message — see `CLAUDE.md` at the repo
  root. No absolute path from this machine appears in a tracked file. Scratch
  paths in this plan are written as `$RETRO_SCRATCH`; set it to a throwaway
  directory outside every repository.
- Exit codes: `0` ran clean and flagged nothing, `1` ran clean and flagged
  something, `2` could not run.
- `plugins/*/bin/*` and `*.md` are `text eol=lf` in `.gitattributes`. If you
  write a file from a script rather than an editor, pass `newline="\n"`.
- Commit messages go through `git commit -F -` fed by a single-quoted heredoc,
  never a double-quoted `-m` string, and contain no backticks.
- Verification is by measurement, read-only against the live corpus. Never write
  anywhere under the harness configuration directory, and never run `extract`
  against the default work directory — always point `RETRO_HOME` at
  `$RETRO_SCRATCH`.
- **Tasks 1 and 2 both redefine stored counters. No trend over `repeat_calls`,
  `retries_after_error` or `correction_turns` means anything until
  `extract --rebuild` has re-measured the whole corpus.** Task 4 is that rebuild;
  the skills that run `extract` must say so, and the `COUNTERS` comment already
  states the contract.

---

## Measured baseline (2026-08-20, live corpus, read-only)

Every number below was re-derived today by walking the corpus, not carried over
from the review that raised the defects. The recount script is scratch and is
never committed.

| Fact | Value |
|---|---|
| Files walked under the transcript root | 1,942 |
| Rows in the ledger | 1,920 (445 main-session, 1,475 subagent) |
| Tool calls in main-session transcripts | 19,943 |
| Repeats the current rule flags, main-session | 1,387 |
| — of which the two inputs were byte-identical | 230 |
| — of which the two inputs differed | 1,157 |
| Full `extract --rebuild` wall clock | 5.4 s |
| `extract` exit code | 0 |

What the 1,157 differing repeats actually are (top classes, main-session):

| Class | Count | Why it matched |
|---|---|---|
| One file read at successive offsets or limits | 637 | the offsets are digits, and digits normalise to `#` |
| Task-tracking updates to different task ids | 314 | the ids are digits |
| Reads of **different files** | 73 | the paths differ only in digits |
| Shell commands that differ | 69 | the commands differ only in numbers, or the description alone differs |
| Everything else | ~64 | assorted numeric-only differences |

The review's figures reproduce: 1,387 flagged, 1,157 with different inputs
(review: 1,158), 637+73 Read repeats (review: 712 at successive offsets), 314
task-tracking (review: 315), 230 byte-identical (review: 229). Differences of one
are a live session that grew between the two runs.

**Share of the friction score.** Measured over main-session rows only:
`tool_retries` contributes **42.2%** all-history and **50.0%** over the last 30
days — the largest single input either way. (The 54% in the earlier document was
taken over a population that included subagent rows.)

**The signature collision the review suspected is real.** `json.dumps(...,
sort_keys=True)` puts `content` before `file_path`, and the result is truncated
at 2,048 characters, so `file_path` is not in the hashed prefix once the content
exceeds roughly 2,030 characters. Verified by construction: two writes of the
same 4,000-character body to different paths produce the same signature; with a
five-character body they do not. In the corpus this fires once in 1,942
transcripts — the mechanism is real, its current incidence is negligible, and
removing truncation closes it for free.

**The classifier defect reproduces, but not by the mechanism the review named.**
Main-session transcripts hold 1,453 short replies (120 characters or fewer,
non-interrupt, on a human user record). The current rule classifies 989.

| Rule for the prior assistant length | Classified | Rescued vs. current |
|---|---|---|
| Last assistant record (current) | 989 | — |
| Last assistant record that carried text | 990 | 1 |
| Largest single record of the turn | 1,017 | 28 |
| **Sum over the turn's records (proposed)** | **1,068** | **79** (56 corrections, 23 questions) |

So "the immediately preceding record carried no text" accounts for **1** turn in
main-session transcripts, not 113. The real cause is a turn whose text is split
across several assistant records, none of which reaches the 400-character floor
on its own. The rescued 79 is the number to check against, and the fix that gets
it is accumulation, not walking back to the last record with text.

**`pack` does not refuse inside a repository.** Verified by pointing
`RETRO_HOME` at a throwaway `git init` directory holding a one-row synthetic
ledger whose transcript path does not exist (so no harvested text was written):
`pack --days 7` wrote its file and exited 1. No guard anywhere in `cmd_pack`.

**What the redefinition does to the score.** Both changes applied together:

| Window (main sessions) | `tool_retries` now | `repeat_calls` / `retries_after_error` | corrections | score total | retry share | top-8 kept | top-20 kept |
|---|---|---|---|---|---|---|---|
| all history (445) | 1,388 | 208 / 32 | 565 → 620 | 6,571 → 4,287 | 42.2% → 6.3% | 7 of 8 | 16 of 20 |
| last 30 days (357) | 927 | 84 / 19 | 251 → 262 | 3,711 → 2,023 | 50.0% → 6.0% | 3 of 8 | 16 of 20 |
| last 7 days (105) | 151 | 1 / 1 | 49 → 49 | 748 → 449 | 40.4% → 0.7% | 7 of 8 | 11 of 20 |

These are not predictions. Every code block in Tasks 1 to 3 was applied to a
scratch copy of `retro.py` and rebuilt against the live corpus: 445 main-session
rows, `repeat_calls` 208, `retries_after_error` 32, `correction_turns` 620, score
total 4,287, rebuild 5.2 s. The fourteen checks in the harness all pass on that
copy. If your run disagrees by more than a few, something in the transcription
differs.

Two consequences worth knowing before you start:

1. **This reorders the pack.** Over 30 days only 3 of the top 8 sessions survive
   into the new top 8. That is the point of the change, and it is also why Task 4
   records the ranking rather than asserting it.
2. **Correctly defined, the retry signal is nearly silent in a weekly window** —
   1 repeat and 1 retry-after-error across 105 sessions. A weekly pack will be
   ranked almost entirely by corrections, interrupts and permission-mode changes.
   That is an honest result, not a defect in the fix, and it is the subject of a
   question at the end of this plan.

---

## Files

- **Modify:** `plugins/retro/bin/retro.py` — every code change in this plan.
- **Modify:** `docs/plans/2026-08-12-retro-design.md` — the metric-definitions
  table, the signature paragraph, the counter list, the work-directory
  guarantee, the verification table, the open questions.
- **Modify:** `docs/plans/2026-08-18-retro-measurement-fixes.md` — the 54%
  finding, and a results subsection under Verification.
- **Modify:** `plugins/retro/skills/finding-friction-in-recent-sessions/SKILL.md`
  — the score description and the counter table.
- **Modify:** the other two `plugins/retro/skills/*/SKILL.md` — the rebuild note.
- **Create (scratch, never committed):** `$RETRO_SCRATCH/check_metrics.py` — the
  harness every task checks against.

### Functions changed in `retro.py`

Line numbers are as of the current tip; a sibling lane landing first shifts them.

| Function | Line | What changes |
|---|---|---|
| module docstring | 2-20 | subcommand list (says two, three exist), the counters sentence |
| `_DIGITS` / `_SPACES` / `SIGNATURE_MAX_CHARS` | 154-161 | `_DIGITS` and `SIGNATURE_MAX_CHARS` deleted; `_SPACES` deleted if unused elsewhere |
| `signature` | 164-172 | replaced by `call_digest` — exact, untruncated |
| `tool_calls_of` | 142-152 | yields `(name, digest, call_id)` |
| `errored_call_ids` (new) | after 231 | tool-use ids a failed record refers to |
| `COUNTERS` | 72-75 | `tool_retries` out, `repeat_calls` and `retries_after_error` in |
| `measure` | 280-401 | call bookkeeping in the assistant branch; accumulation in the user branch |
| `classify_user_turn` | 186-208 | docstring only — the parameter now means the turn |
| `friction_score` | 534-541 | two terms replace one |
| `_moments` | 557-583 | accumulates prior text the same way `measure` does |
| `refuse_inside_repo` (new) | before 586 | the repository guard |
| `cmd_pack` | 586-654 | guard before the write; the per-session counter line |

---

## Sibling lane map — read this before Task 1

Two branches are unmerged and both rewrite this file heavily. Neither is
file-disjoint from this plan, so **no task here can run concurrently with any of
them, or with another task in this plan.** One file, one implementer at a time.

| Lane | Carries | Collides with |
|---|---|---|
| the extra-roots lane | compressed transcripts, extra transcript roots, row identity derived from the root | `read_records`, `cmd_extract`, the `rel` derivation in `measure`, path resolution in `_moments` — **not** the retry or classifier code |
| the population/classification lane | a `label` subcommand, "do not count tool-written user records as human turns", a `subagents` subcommand | **Task 1** (its `label` sampler reuses `signature()` and sweeps the retry rule), **Task 2** (it edits the same user branch), **Task 3** (it already defines `refuse_inside_repo`), **Task 5** (it edits the same documents) |

Three specifics for whoever merges:

1. Task 3 deliberately writes `refuse_inside_repo` **byte-identical** to the
   version on the population/classification lane, so the merge keeps one copy and
   `cmd_pack` and the labelling command share it.
2. That lane's `label` sampler calls `signature()`. After Task 1 there is no
   `signature()` — it must call `call_digest`, and its "candidate retry" sampling
   must sample the new definition or it will be validating a rule that no longer
   exists.
3. That lane's module docstring says five subcommands. Task 5's sweep is stamped
   to this branch's head; re-run it over the merge commit.

---

## Task 1: Make the retry counter mean an identical call

**Files:**
- Modify: `plugins/retro/bin/retro.py`
- Create (scratch): `$RETRO_SCRATCH/check_metrics.py`

**Interfaces:**
- Produces: `call_digest(tool_input) -> str`; `tool_calls_of(message)` yielding
  `(name: str, digest: str, call_id: str | None)`; `errored_call_ids(rec) ->
  set[str]`; ledger columns `repeat_calls: int` and `retries_after_error: int`
  replacing `tool_retries`.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing checks**

Create `$RETRO_SCRATCH/check_metrics.py`:

```python
#!/usr/bin/env python3
"""Scratch harness for the retry and classifier fixes. Never committed."""
import importlib.util, json, sys
from pathlib import Path

RETRO = Path(sys.argv[1])  # path to plugins/retro/bin/retro.py
spec = importlib.util.spec_from_file_location("retro", RETRO)
retro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retro)

failures = []
def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + ("" if ok else f"   [{detail}]"))
    if not ok:
        failures.append(label)

body = "x" * 4000
check("long writes to different paths get different digests",
      retro.call_digest({"file_path": "/a/one.txt", "content": body})
      != retro.call_digest({"file_path": "/b/two.txt", "content": body}))
check("paths differing only in digits get different digests",
      retro.call_digest({"file_path": "/p/report-2024.md"})
      != retro.call_digest({"file_path": "/p/report-2025.md"}))
check("commands differing only in a number get different digests",
      retro.call_digest({"command": "head -5 f"})
      != retro.call_digest({"command": "head -500 f"}))
check("an identical input gets the same digest",
      retro.call_digest({"command": "echo x > file.txt"})
      == retro.call_digest({"command": "echo x > file.txt"}))
check("a None input digests to the empty string",
      retro.call_digest(None) == "")
check("tool_retries is gone from the row schema",
      "tool_retries" not in retro.COUNTERS)
check("both new columns are in the row schema",
      "repeat_calls" in retro.COUNTERS and "retries_after_error" in retro.COUNTERS)

print("\nFAILED: " + ", ".join(failures) if failures else "\nall checks passed")
sys.exit(1 if failures else 0)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python "$RETRO_SCRATCH/check_metrics.py" plugins/retro/bin/retro.py`
Expected: `AttributeError: module 'retro' has no attribute 'call_digest'`.

- [ ] **Step 3: Replace the signature with an exact digest**

Delete `_DIGITS`, `_SPACES`, `SIGNATURE_MAX_CHARS`, the comment block above
them, and `signature`. All four have exactly one caller between them, which is
`signature` itself; `_INTERRUPT` on the same lines stays. In their place:

```python
def call_digest(tool_input):
    """Identify one tool call by its input, exactly.

    Deliberately no normalisation and no truncation. The rule this replaces
    folded digits to `#` and hashed only the first 2,048 characters of the
    canonical JSON: of 1,387 repeats it flagged in main-session transcripts,
    1,157 had different inputs - 637 were one file read at successive offsets,
    314 were task-tracking updates to different ids, and 73 were reads of
    different files whose paths differed only in digits. Truncation had its own
    hole: sort_keys puts a write's content ahead of its path, so two long
    identical writes to different paths hashed the same.
    """
    if tool_input is None:
        return ""
    raw = json.dumps(tool_input, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]
```

- [ ] **Step 4: Carry the tool-use id out of the message**

`tool_calls_of` currently yields two values. Yield three:

```python
def tool_calls_of(message):
    """Yield (tool_name, input_digest, call_id) for each tool use in a message.

    The id is what links a call to the result that came back, so a repeat can
    be told apart by whether the first attempt failed.
    """
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield (block.get("name") or "?", call_digest(block.get("input")),
                   block.get("id"))
```

- [ ] **Step 5: Name the calls a failed record refers to**

Add directly below `is_error_record`:

```python
def errored_call_ids(rec):
    """The tool-use ids this record reports a failure for.

    is_error_record answers "did something fail here" for the error counter.
    This answers "which call", which is what a retry needs. The record-level
    markers (a toolUseResult error key, an error-prefixed string result) carry
    no id of their own, so they mark every tool result on the same record.
    """
    message = rec.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), list):
        return set()
    record_failed = is_error_record(rec)
    out = set()
    for block in message["content"]:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            if block.get("is_error") or record_failed:
                call_id = block.get("tool_use_id")
                if call_id:
                    out.add(call_id)
    return out
```

- [ ] **Step 6: Split the counter in the row schema**

```python
COUNTERS = ["turns", "user_prompts", "tool_calls", "tool_errors",
            "repeat_calls", "retries_after_error",
            "correction_turns", "approval_turns", "interrupts",
            "permission_mode_changes", "queued_prompts", "skill_runs"]
```

- [ ] **Step 7: Count the two kinds in `measure`**

Replace `seen_sigs = set()` near the top of `measure` with:

```python
    seen_calls = set()      # (name, digest) already made in this transcript
    failed_calls = set()    # ...and whose earlier attempt came back an error
    pending_calls = {}      # tool-use id -> (name, digest), until its result lands
```

Replace the tool-call loop in the `assistant` branch with:

```python
            for name, digest, call_id in tool_calls_of(msg):
                m["tool_calls"] += 1
                key = (name, digest)
                if digest and key in seen_calls:
                    if key in failed_calls:
                        m["retries_after_error"] += 1
                    else:
                        m["repeat_calls"] += 1
                if digest:
                    seen_calls.add(key)
                    if call_id:
                        pending_calls[call_id] = key
```

And replace the existing tail block of the record loop — the one reading
`if rtype == "user" and is_error_record(rec): m["tool_errors"] += 1` — with a
single block that does both jobs on one pass over the record:

```python
        # Only user records carry tool results; checking the rest re-walks
        # message content for nothing.
        if rtype == "user":
            if is_error_record(rec):
                m["tool_errors"] += 1
            for call_id in errored_call_ids(rec):
                key = pending_calls.get(call_id)
                if key:
                    failed_calls.add(key)
```

The two counters are disjoint by construction: a repeat is counted as one or the
other, never both, so they sum to the number of identical repeats.

- [ ] **Step 8: Reweight the score and the pack's per-session line**

```python
def friction_score(row):
    """Rank sessions for which ones are worth quoting. Weighted toward signals
    that mean a human had to intervene, over ones that just mean a long session.

    A repeat that followed a failure means something went wrong and was tried
    again; a repeat that followed a success only means the same call was made
    twice. They are weighted 2 and 1 accordingly.
    """
    return (int(row.get("correction_turns") or 0) * 4
            + int(row.get("interrupts") or 0) * 4
            + int(row.get("permission_mode_changes") or 0) * 3
            + int(row.get("retries_after_error") or 0) * 2
            + int(row.get("repeat_calls") or 0)
            + int(row.get("tool_errors") or 0))
```

In `cmd_pack`, replace the `tool retries {row.get('tool_retries')}, ` fragment of
the per-session line with:

```python
                     f"repeat calls {row.get('repeat_calls')}, "
                     f"retries after error {row.get('retries_after_error')}, "
```

- [ ] **Step 9: Run the checks and watch them pass**

Run: `python "$RETRO_SCRATCH/check_metrics.py" plugins/retro/bin/retro.py`
Expected: all seven PASS, exit 0.

- [ ] **Step 10: Prove it against the corpus**

```bash
RETRO_HOME="$RETRO_SCRATCH/home" python plugins/retro/bin/retro.py extract --rebuild
python - <<'PY'
import json, os
from pathlib import Path
rows = [json.loads(l) for l in
        (Path(os.environ["RETRO_SCRATCH"]) / "home" / "metrics.jsonl").open(encoding="utf-8")]
main = [r for r in rows if not r.get("is_subagent")]
print("main rows", len(main),
      "repeat_calls", sum(r["repeat_calls"] for r in main),
      "retries_after_error", sum(r["retries_after_error"] for r in main))
PY
```

Expected: main rows about 445, `repeat_calls` about 208, `retries_after_error`
about 32. The corpus grows while you work, so treat these as within a few, not
exact; a result in the thousands means the normalisation is still in.

- [ ] **Step 11: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: a retry is an identical call, not one that normalises to the same shape

The signature folded digits and whitespace away and hashed only the first
2,048 characters. Of 1,387 repeats it flagged in main-session transcripts,
1,157 had different inputs: 637 one file read at successive offsets, 314
task-tracking updates to different ids, 73 reads of different files whose
paths differ only in digits. Truncation had its own hole, since sort_keys
puts a write's content ahead of its path.

The digest is now exact and untruncated, and the single counter splits into
two disjoint ones: repeat_calls for an identical call already made, and
retries_after_error for one whose earlier attempt failed. Score weights are
1 and 2. Both are new columns, so a rebuild is required before any trend
over them means anything.
MSG
```

---

## Task 2: Classify against the whole assistant turn

**Files:**
- Modify: `plugins/retro/bin/retro.py`
- Modify (scratch): `$RETRO_SCRATCH/check_metrics.py`

**Interfaces:**
- Consumes: nothing from Task 1 — but it edits the same function, so it runs
  after Task 1 is committed.
- Produces: no new names. `classify_user_turn`'s second parameter now means the
  whole turn's character count.

- [ ] **Step 1: Add the failing check**

Append to `$RETRO_SCRATCH/check_metrics.py`, above the summary lines:

```python
import tempfile
def rows_for(records):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.jsonl"
        with p.open("w", encoding="utf-8", newline="\n") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        return retro.measure(p)

def assistant(text, ts="2026-08-20T00:00:00Z"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]}}

def user(text, ts="2026-08-20T00:00:01Z"):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": text}}

split_turn = rows_for([assistant("a" * 250), assistant("b" * 250),
                       user("no, use the other one")])
check("a turn split across two records still passes the floor",
      split_turn["correction_turns"] == 1, str(split_turn["correction_turns"]))

one_record = rows_for([assistant("a" * 500), user("no, use the other one")])
check("a turn in one record is unaffected",
      one_record["correction_turns"] == 1, str(one_record["correction_turns"]))

short_turn = rows_for([assistant("a" * 50), user("no, use the other one")])
check("a genuinely short turn still classifies nothing",
      short_turn["correction_turns"] == 0, str(short_turn["correction_turns"]))

# 176 characters, so this turn is a new request and is not itself classified.
# A short first turn would score as a correction in its own right and the next
# check would read 1 for the wrong reason.
NEW_REQUEST = "please move on to the next file in the list " * 4

two_turns = rows_for([assistant("a" * 500), user(NEW_REQUEST),
                      assistant("b" * 50), user("no, use the other one")])
check("the accumulator resets at each human turn",
      two_turns["correction_turns"] == 0, str(two_turns["correction_turns"]))

restart = rows_for([assistant("a" * 500), user(NEW_REQUEST),
                    assistant("b" * 200), assistant("c" * 250),
                    user("no, use the other one")])
check("accumulation restarts and can pass the floor again",
      restart["correction_turns"] == 1, str(restart["correction_turns"]))
```

- [ ] **Step 2: Run it and watch the first check fail**

Run: `python "$RETRO_SCRATCH/check_metrics.py" plugins/retro/bin/retro.py`
Expected: `FAIL  a turn split across two records still passes the floor   [0]`,
the other four new checks PASS, and Task 1's seven still PASS.

- [ ] **Step 3: Accumulate in `measure`**

In the `assistant` branch, replace `prior_assistant_chars = len(body)` with:

```python
            prior_assistant_chars += len(body)
```

In the `user` branch, add the reset to the interrupt arm so both arms of a human
turn close the accumulation:

```python
            if kind == "interrupt":
                m["interrupts"] += 1
                prior_assistant_chars = 0
            elif rec.get("toolUseResult") is None and body.strip():
```

Leave the existing `prior_assistant_chars = 0` at the end of the second arm in
place. Tool-result user records still do not reset it — they are not turns.

- [ ] **Step 4: Say what the parameter means**

Amend `classify_user_turn`'s docstring, replacing its last paragraph with:

```python
    Only a short reply to a substantial assistant turn is classified at all.
    Anything longer is a new request, and returns "" as it always has.

    prior_assistant_chars is the whole turn's text - every assistant record
    since the last human turn, added up - not the last record's. One turn is
    routinely split across several records, and reading only the last one
    discarded 79 short replies in main-session transcripts (56 corrections, 23
    questions) whose turn cleared the floor comfortably.
```

- [ ] **Step 5: Make `_moments` read the same rule**

`_moments` keeps its own copy of the prior text and must accumulate the same
way, or the pack will quote a different set of turns from the one it counted.
Replace the loop's prior-text handling:

```python
    out = []
    prior = ""        # tail of the turn's text, for quoting
    prior_chars = 0   # the whole turn's length, for classifying
    for rec in read_records(path):
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "assistant":
            text = text_of(rec.get("message") or {})
            if text:
                prior_chars += len(text)
                prior = (prior + "\n" + text)[-400:] if prior else text[-400:]
        elif rec.get("type") == "user" and rec.get("toolUseResult") is None:
            body = text_of(rec.get("message") or {})
            kind = classify_user_turn(body, prior_chars)
            if kind in ("interrupt", "correction"):
                out.append({
                    "at": rec.get("timestamp") or "",
                    "kind": kind,
                    "said": redact(body.strip())[:400],
                    "after": redact(prior.strip()[-300:]),
                })
            prior = ""
            prior_chars = 0
```

The 400-character tail is kept so the 300-character quote is never short of
text; the classification reads `prior_chars`, which is uncapped.

- [ ] **Step 6: Run the checks and watch them pass**

Run: `python "$RETRO_SCRATCH/check_metrics.py" plugins/retro/bin/retro.py`
Expected: all twelve PASS, exit 0.

- [ ] **Step 7: Prove it against the corpus**

```bash
RETRO_HOME="$RETRO_SCRATCH/home" python plugins/retro/bin/retro.py extract --rebuild
python - <<'PY'
import json, os
from pathlib import Path
rows = [json.loads(l) for l in
        (Path(os.environ["RETRO_SCRATCH"]) / "home" / "metrics.jsonl").open(encoding="utf-8")]
main = [r for r in rows if not r.get("is_subagent")]
print("corrections", sum(r["correction_turns"] for r in main),
      "approvals", sum(r["approval_turns"] for r in main),
      "user_prompts", sum(r["user_prompts"] for r in main))
PY
```

Expected: `corrections` about 620, up from 565. A number still near 565 means the
accumulation did not take; a number far above 700 means the reset was lost.

- [ ] **Step 8: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: classify a short reply against the whole assistant turn

measure kept only the last assistant record's length, and one turn is
routinely split across several records. 79 short replies in main-session
transcripts were discarded whose turn cleared the 400-character floor - 56
corrections and 23 questions. Reading back to the last record that carried
text rescues 1 of them, so accumulation is the fix, not that.

_moments accumulates the same way, so the pack quotes the turns it counted.
correction_turns is redefined: a rebuild is required before any trend over
it means anything.
MSG
```

---

## Task 3: `pack` refuses to write inside a repository

**Files:**
- Modify: `plugins/retro/bin/retro.py`
- Modify (scratch): `$RETRO_SCRATCH/check_metrics.py`

**Interfaces:**
- Produces: `refuse_inside_repo(path) -> None` (exits 2 when `path` is inside a
  repository). Written byte-identical to the version on the sibling lane so the
  merge keeps one copy.

- [ ] **Step 1: Add the failing check**

Append to `$RETRO_SCRATCH/check_metrics.py`:

```python
import subprocess
with tempfile.TemporaryDirectory() as d:
    repo = Path(d) / "repo"
    (repo / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    raised = None
    try:
        retro.refuse_inside_repo(repo / "sub" / "pack.md")
    except SystemExit as exc:
        raised = exc.code
    check("refuses a path nested inside a repository",
          raised == retro.EXIT_CANNOT_RUN, repr(raised))
    outside = Path(d) / "plain"
    outside.mkdir()
    ok = True
    try:
        retro.refuse_inside_repo(outside / "pack.md")
    except SystemExit:
        ok = False
    check("allows a path outside every repository", ok)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python "$RETRO_SCRATCH/check_metrics.py" plugins/retro/bin/retro.py`
Expected: `AttributeError: module 'retro' has no attribute 'refuse_inside_repo'`.

- [ ] **Step 3: Add the guard**

Insert immediately above `cmd_pack`:

```python
def refuse_inside_repo(path):
    """The labelling file carries message text. redact() mirrors the mechanical
    categories of the privacy audit and cannot recognise what a project is
    called, so the file carries identifiers redaction will not catch. It lives
    in the work directory and never inside a repository."""
    for parent in [path] + list(path.parents):
        if (parent / ".git").exists():
            print(f"refusing to write {path.name}: {parent} is a repository - "
                  "point RETRO_HOME outside every repo", file=sys.stderr)
            sys.exit(EXIT_CANNOT_RUN)
```

`.exists()` rather than `.is_dir()` on purpose: in a worktree or a submodule
`.git` is a file.

- [ ] **Step 4: Call it before anything is created**

At the end of `cmd_pack`, the guard runs before the directory is made, not
after — creating directories inside a repository is itself a write:

```python
    out_path = WORK_DIR / f"pack-{now.isoformat()}-{args.days}d.md"
    refuse_inside_repo(out_path)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(out_path)
```

- [ ] **Step 5: Run the checks and watch them pass**

Run: `python "$RETRO_SCRATCH/check_metrics.py" plugins/retro/bin/retro.py`
Expected: all fourteen PASS, exit 0.

- [ ] **Step 6: Prove it end to end, with no harvested text**

```bash
mkdir -p "$RETRO_SCRATCH/inrepo" && git -C "$RETRO_SCRATCH/inrepo" init -q
python - <<'PY'
import json, os
from pathlib import Path
row = {"transcript": "nonexistent/none.jsonl", "is_subagent": False,
       "session_id": "s", "project": "p", "git_branch": "b", "cc_version": "v",
       "date": "2026-08-20", "duration_s": 1, "tokens_in": 0, "tokens_out": 0,
       "cache_read": 0, "skills_used": [], "turns": 1, "user_prompts": 1,
       "tool_calls": 0, "tool_errors": 0, "repeat_calls": 0,
       "retries_after_error": 0, "correction_turns": 1, "approval_turns": 0,
       "interrupts": 0, "permission_mode_changes": 0, "queued_prompts": 0,
       "skill_runs": 0}
p = Path(os.environ["RETRO_SCRATCH"]) / "inrepo" / "metrics.jsonl"
p.write_text(json.dumps(row) + "\n", encoding="utf-8", newline="\n")
PY
RETRO_HOME="$RETRO_SCRATCH/inrepo" python plugins/retro/bin/retro.py pack --days 7
echo "exit=$?"
ls "$RETRO_SCRATCH/inrepo"
```

Expected: the refusal message on stderr, `exit=2`, and no `pack-*.md` in the
listing. The synthetic row points at a transcript that does not exist, so
`moments` returns nothing and no harvested text is written even if the guard
fails. Delete `$RETRO_SCRATCH/inrepo` afterwards.

- [ ] **Step 7: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: pack refuses to write inside a repository

A pack carries quoted user turns. redact() mirrors the mechanical categories
of the privacy audit and cannot recognise what a project is called, so a pack
carries identifiers redaction will not catch. The design document already
stated the work directory sits outside every repository; nothing enforced it
for pack, which wrote wherever RETRO_HOME pointed.

The guard runs before the work directory is created, and is written to match
the one on the sibling branch so a merge keeps a single copy.
MSG
```

---

## Task 4: Rebuild, and record what the redefinition did

**Files:**
- Modify: `docs/plans/2026-08-18-retro-measurement-fixes.md` — a results
  subsection under Verification.

**Interfaces:**
- Consumes: the columns from Tasks 1 and 2.
- Produces: the recorded before/after. Task 5's sweep cites it.

- [ ] **Step 1: Capture the ranking before the rebuild**

The ledger in `$RETRO_SCRATCH/home` is already on the new columns from Tasks 1
and 2. Rebuild the old ranking from the branch point instead — `HEAD~3` if Tasks
1, 2 and 3 each landed as one commit; otherwise name the branch point explicitly:

```bash
git show HEAD~3:plugins/retro/bin/retro.py > "$RETRO_SCRATCH/retro-before.py"
RETRO_HOME="$RETRO_SCRATCH/before" python "$RETRO_SCRATCH/retro-before.py" extract --rebuild
```

- [ ] **Step 2: Rebuild on the new definitions**

```bash
RETRO_HOME="$RETRO_SCRATCH/after" python plugins/retro/bin/retro.py extract --rebuild
```

Expected: the same transcript and row counts as the before run, and a wall clock
near the 5.4 s baseline — a trial run of these changes measured 5.2 s, so the
untruncated digest costs nothing measurable. Record the number either way.

- [ ] **Step 3: Diff the two rankings**

```bash
python - <<'PY'
import json, os
from pathlib import Path
S = Path(os.environ["RETRO_SCRATCH"])
def load(name):
    return [json.loads(l) for l in (S / name / "metrics.jsonl").open(encoding="utf-8")
            if l.strip()]
def old(r):
    return (r["correction_turns"] * 4 + r["interrupts"] * 4
            + r["permission_mode_changes"] * 3 + r["tool_retries"] * 2
            + r["tool_errors"])
def new(r):
    return (r["correction_turns"] * 4 + r["interrupts"] * 4
            + r["permission_mode_changes"] * 3 + r["retries_after_error"] * 2
            + r["repeat_calls"] + r["tool_errors"])
b = [r for r in load("before") if not r.get("is_subagent")]
a = [r for r in load("after") if not r.get("is_subagent")]
bt = sorted(((old(r), r["transcript"]) for r in b), reverse=True)
at = sorted(((new(r), r["transcript"]) for r in a), reverse=True)
print("rows", len(b), len(a))
print("score total", sum(s for s, _ in bt), "->", sum(s for s, _ in at))
print("retries", sum(r["tool_retries"] for r in b), "-> repeat_calls",
      sum(r["repeat_calls"] for r in a), "retries_after_error",
      sum(r["retries_after_error"] for r in a))
print("corrections", sum(r["correction_turns"] for r in b), "->",
      sum(r["correction_turns"] for r in a))
for n in (8, 20):
    print(f"top-{n} kept",
          len({t for _, t in bt[:n]} & {t for _, t in at[:n]}), "of", n)
PY
```

Expected, within a few for corpus growth: about 445 rows either way; score total
about 6,571 down to about 4,287; retries 1,388 down to 208 plus 32; corrections
565 up to 620; 7 of the top 8 and 16 of the top 20 kept.

- [ ] **Step 4: Record it**

Under Verification in `docs/plans/2026-08-18-retro-measurement-fixes.md`, append
a subsection headed `### Retry redefinition, measured 2026-08-20` holding the
before/after table from the baseline section of this plan with the numbers the
rebuild actually produced substituted in, plus one sentence naming the fact that
matters: **correctly defined, the retry signal is nearly silent over a 7-day
window (1 repeat call, 1 retry after error across about 105 sessions), so a
weekly pack is ranked almost entirely by corrections, interrupts and
permission-mode changes.**

- [ ] **Step 5: Commit**

```bash
git add docs/plans/2026-08-18-retro-measurement-fixes.md
git commit -F - <<'MSG'
docs: what the retry redefinition and the turn fix measured

Rebuilt the ledger on both definitions and diffed the ranking. The retry
family drops from 42% of the friction score to 6%, corrections rise, and 3
of the top 8 sessions over a 30-day window are different sessions. Over 7
days the redefined counters are nearly silent, so a weekly pack now ranks
on corrections, interrupts and permission-mode changes.
MSG
```

---

## Task 5: Sweep for what this made false

**Files:**
- Modify: `plugins/retro/bin/retro.py` — the module docstring.
- Modify: `docs/plans/2026-08-12-retro-design.md`
- Modify: `docs/plans/2026-08-18-retro-measurement-fixes.md`
- Modify: `plugins/retro/skills/finding-friction-in-recent-sessions/SKILL.md`
- Modify: `plugins/retro/skills/auditing-workflow-rules-against-behavior/SKILL.md`
- Modify: `plugins/retro/skills/scouting-tools-for-open-frictions/SKILL.md`

**Interfaces:**
- Consumes: the recorded numbers from Task 4.
- Produces: nothing code-facing.

- [ ] **Step 1: Invoke the skill**

Use `core:finding-what-a-change-made-false`, scoped to this branch:
`git diff --stat <branch point>..HEAD`. Follow its method — list what changed,
grep every tracked document for each changed name, classify each hit. The
findings below are what a first pass already turned up; the skill's method is
what finds the rest, so do not stop at this list.

- [ ] **Step 2: Grep for each deleted or changed name**

```bash
git grep -n -e tool_retries -e signature -e SIGNATURE_MAX_CHARS -e '54%' \
  -e 'normaliz' -e 'normalis' -e 'subcommand' -e 'sits outside every' -- '*.md' '*.py'
git grep -nE 'today|current|still|not yet|does not exist' -- '*.md'
```

- [ ] **Step 3: Fix the present-tense claims that are now false**

Known before you start:

| Where | What is false | What it becomes |
|---|---|---|
| `retro.py` module docstring | says **two** subcommands and lists two; three exist (`extract`, `pack`, `skills`) — already false before this branch | three, listed |
| `retro.py` module docstring | "Message text leaves this script in exactly one place" is still true, but nothing enforced where that place may be | note that the pack write refuses inside a repository |
| design doc, metric-definitions table | the `tool_retries` row: "same tool + normalized input signature" | two rows, for `repeat_calls` and `retries_after_error` |
| design doc, the paragraph under that table | "The retry signature normalizes whitespace and numeric literals before hashing, so a retried command with a tweaked number still matches its predecessor" — the behaviour is gone, and it was the defect | the exact-digest rule and one line of why, citing the recount |
| design doc, the row-schema list | still names `tool_retries` | the two new columns |
| design doc, the work-directory paragraph | "sits outside every git repository" was a guarantee nothing enforced | say the pack write refuses, and name the function |
| design doc, Verification table | row counts and the row split were measured on a smaller corpus | a measurement whose subject changed: re-take it from Task 4's rebuild, or stamp each number with the commit it was true at |
| design doc, Open questions | "`tool_retries` and `correction_turns` are heuristics ... not validated against a hand-labelled sample" | `tool_retries` no longer exists; the recount that replaced it is evidence, hand labelling is still open for `correction_turns` |
| measurement-fixes doc | "`tool_retries` **54%**" in the finding, and again in the hand-label section | the re-derived share, and that the counter it named is gone |
| `finding-friction-in-recent-sessions/SKILL.md` | the score description ("then permission-mode changes and retries") and the `tool_retries` row of the counter table | the two counters and what each reads as |
| all three retro `SKILL.md` files | each runs `extract` with no rebuild | one sentence: the counters changed, so run `extract --rebuild` once before reading a trend |

- [ ] **Step 4: Check what is not in the queue**

There is no `ROADMAP.md` in this repository, so `docs/plans/` is the queue.
Confirm this plan's filename is reachable — if a later index file exists, add it.
Any finding parked only in scratch during this work moves into the
measurement-fixes document now; the scratch directory does not survive.

- [ ] **Step 5: Verify the sweep mechanically**

```bash
git grep -n tool_retries -- '*.md' '*.py'
git grep -n 'Two subcommands' -- '*.py'
git grep -n '54%' -- '*.md'
```

Expected: no hits outside historical text (a sentence that says "it used to be"
is correct and stays). Then run the privacy audit over the worktree:

```bash
sh plugins/core/bin/repo-privacy-audit
```

Expected: exit 1, with every category reading zero except `email`, and the
`email` row confined to metadata. Measured on this branch before the work began:
2 commit messages (the co-author trailers), 28 patch lines, 2 identities, and 0
distinct files. The patch-line count is not file content — `git log --all -p`
prints an `Author:` header before every commit, and the audit greps that output
whole, so the row can never read zero in a repository whose commits carry an
address. Confirm content is clean separately, and expect `0`:

```bash
git log --all -p | grep -EI "^[+-][^+-]" \
  | grep -cEI '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
```

- [ ] **Step 6: Answer the skill's closing question**

`finding-what-a-change-made-false` ends by asking whether the run changed the
skill itself. Answer it with evidence from this run: if a miss survived to the
grep in Step 5 that the method should have caught, add a row quoting it;
otherwise say so and change nothing.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -F - <<'MSG'
docs: make the retro documents true after the metric redefinition

The design document described a retry signature that normalised digits away
and a counter that no longer exists; the module docstring listed two
subcommands where there are three; both documents carried a share of the
friction score taken from the counter this branch replaced. The work
directory guarantee is now enforced rather than asserted, and the three
skills say to rebuild once, because two stored counters changed meaning.
MSG
```

---

## Verification

Run at the end, on the branch, before anything is proposed for merge.

| Check | Command | Expected |
|---|---|---|
| Harness | `python "$RETRO_SCRATCH/check_metrics.py" plugins/retro/bin/retro.py` | all PASS, exit 0 |
| Rebuild | `RETRO_HOME="$RETRO_SCRATCH/after" python plugins/retro/bin/retro.py extract --rebuild` | about 1,942 walked, about 1,920 rows, 0 unreadable, exit 0 |
| Ranking | Task 4 Step 3 | matches the recorded before/after within a few |
| Pack, outside a repo | `RETRO_HOME="$RETRO_SCRATCH/after" python plugins/retro/bin/retro.py pack --days 7` | writes, prints the path, exit 0 or 1 |
| Pack, inside a repo | Task 3 Step 6 | refuses, exit 2, no file written |
| Skills | `RETRO_HOME="$RETRO_SCRATCH/after" python plugins/retro/bin/retro.py skills --days 30` | runs, exit 0 or 1 |
| Documents | Task 5 Step 5 greps | no live hits |
| Privacy | `sh plugins/core/bin/repo-privacy-audit`, then Task 5 Step 5's content grep | every category zero but `email`; the content grep prints 0 |
| Manifests | `python -c "import json,glob; [json.load(open(p)) for p in glob.glob('.claude-plugin/marketplace.json')+glob.glob('plugins/*/.claude-plugin/plugin.json')]"` | no output |

---

## Questions for the operator

1. **Two counters or one?** This plan splits `tool_retries` into `repeat_calls`
   (an identical call already made, weight 1) and `retries_after_error` (the
   earlier identical attempt failed, weight 2). The alternative is a single
   counter meaning only one of those. Measured over main-session rows,
   all-history: 208 repeats and 32 after a failure. Keep the split, or collapse
   it — and if collapsed, to which meaning?

2. **Should a repeat that followed a success score at all?** Over the last 7
   days there is 1 of each across about 105 sessions, so under either weighting a
   weekly pack is ranked almost entirely by corrections, interrupts and
   permission-mode changes. Dropping `repeat_calls` from the score (recording it
   but not scoring it) is defensible. Keep it at weight 1, or drop it to 0?

3. **Should the guard cover the whole work directory, or only writes that carry
   message text?** This plan guards the pack write, which is the only text-bearing
   write on this branch. The design document's sentence is broader — it says the
   work directory sits outside every repository, which would also cover the
   counts-only ledger. Guard the directory once at creation instead?

4. **Base and ordering.** Two branches are unmerged and both rewrite this file.
   One of them already carries `refuse_inside_repo` and a `label` subcommand
   whose sampler calls the `signature()` that Task 1 deletes. Two of the four
   defects as originally stated describe that branch's state, not this one: at
   this tip the module docstring says two subcommands where three exist, and the
   design document's "three subcommands" is currently correct, not wrong. Land
   this on the current tip and hand-merge afterwards, or merge those branches
   first and re-plan against the result?
