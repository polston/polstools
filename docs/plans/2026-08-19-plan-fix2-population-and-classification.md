# Fix 2 — one population per ratio: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every printed rate divide one population by itself, split the
correction signal into correction / approval / question, stop counting
tool-generated user records as human turns, and add a `label` subcommand that
measures the classifier and the retry rule against hand labels.

**Architecture:** Four edits to one stdlib-only script. `totals()` stops
deciding what a population is and aggregates whatever rows it is handed;
`cmd_pack` picks the populations and reports subagent spend on its own line.
`classify_user_turn()` gains two more return values under a fixed precedence,
and `moments()` quotes only the two that mean friction. A one-line predicate
excludes tool-written user records from the three human-turn counters. A new
`label` section samples turns and retry candidates into a redacted file under
the work directory and reads the operator's marks back as precision, recall and
a threshold sweep.

**Tech Stack:** Python 3 standard library only. No test suite, no build step, no
dependency. Verification is measurement against the live session corpus,
read-only, with `RETRO_HOME` pointed at a throwaway directory.

**Spec:** `docs/plans/2026-08-18-retro-measurement-fixes.md`, section "Fix 2 —
one population per ratio" (2a, 2b, 2c, 2d) plus its Invariants section.

**Blocked before Task 2 and Task 4 can start:** Questions 1 and 2 at the foot of
this document. Task 1 and Task 3 are not blocked.

## Global Constraints

- Stdlib only. No dependency, no build step, no daemon.
- No other project of the author's is named — not in code, comments, examples,
  commit messages, or this document. The same rule covers personal paths,
  account names, emails, machine names and session ids.
- Message text leaves the script in exactly two places once this lands: the
  `moments` section of a pack, and the labelling file. Both go through
  `redact()`. The module docstring currently says "exactly one place" and is
  updated in the same change, not after it.
- `redact()` does not catch another project's absolute paths. Packs and the
  labelling file therefore carry them. Both stay in the work directory, are
  never committed, and are not safe to paste anywhere public unread.
- The labelling file lives under the work directory and is never written inside
  a repository. The code enforces this, not the operator.
- Only aggregates from a labelling run go into this document, the spec, a commit
  message, or any other tracked file — never sample turns.
- The ledger contract: redefining or adding a counter requires
  `extract --rebuild` before any trend over it means anything.
- Exit codes: `0` ran clean and flagged nothing, `1` ran clean and flagged
  something, `2` could not run.
- Verification runs never write under the user's Claude configuration directory
  and never run `extract` against the default work directory. Every run in this
  plan sets `RETRO_HOME` to a throwaway directory first.
- Commit messages go through `git commit -F -` fed by a single-quoted heredoc,
  never through `git commit -m "…"`.

---

## Measured starting state

Everything below was measured on 2026-08-19 against the live corpus with an
isolated `RETRO_HOME`. These are the "before" values each task's verification
compares against. The corpus grows daily, so re-measure the row you are about to
change rather than trusting the number a week from now.

| Quantity | Now |
|---|---|
| transcripts walked / rows written | 1,920 / 1,898 |
| rows by population | 431 main-session, 1,467 subagent |
| full `extract --rebuild` | 5.2 s |
| `user_prompts` all / main / subagent | 11,509 / 3,877 / 7,632 |
| `correction_turns` all / main | 989 / 988 |
| `interrupts` all / main | 201 / 163 |
| printed prompts per session (all counters ÷ main sessions) | 26.7 |
| true main prompts per session | 9.0 |
| main corrections whose reply ends in `?` | 380 of 988 (38.5%) |
| user records carrying `sourceToolAssistantUUID` | 49,516, all on user records, never empty |
| …of those, ones not already excluded by `toolUseResult`, main / subagent | 0 / 6,787 |
| main-session interrupt records carrying the marker | 9 |
| main rows whose friction score moves under 2b | 6 of 431 |
| top-20 friction order under 2b | identical |

Two consequences worth having in front of you before you start:

1. **2b buys almost nothing in the ranking and costs a full rebuild.** It moves
   6 rows of 431, leaves the top-20 order untouched, and changes main-session
   corrections not at all. What it fixes is the subagent rows' honesty
   (7,632 counted prompts there, 6,787 of them machine-written) and 9
   main-session interrupts that were tool-written. It is Task 3, it lands last
   among the counter changes, and it is one revert to drop.
2. **2c is the change that moves the number.** The question rule alone
   reclassifies 38.5% of main-session corrections. Corrections are 29% of the
   friction score, so the ranking will genuinely reorder — that is the point,
   not a regression.

## File Structure

One file changes: `plugins/retro/bin/retro.py` (594 lines today). Three
documentation files change in Task 5. Nothing is created except the plan's own
throwaway verification scripts, which live in a temp directory and are never
added to the repository — the repo has no test suite and adding one is not in
this spec's scope.

| Region of `retro.py` | Responsibility after this plan | Task |
|---|---|---|
| module docstring | says text leaves in two places, both in the work directory | 4 |
| tuning constants | correction thresholds, approval phrases, label sampling constants | 2, 4 |
| `COUNTERS` | ledger columns; gains `approval_turns` | 2 |
| `classify_user_turn` | the one definition of what a user turn means | 2 |
| `is_approval` | whole-reply approval test, shared by the classifier and the sweep | 2 |
| `is_tool_generated` | was this user record written by a tool | 3 |
| `measure` user branch | counts turns per the two rules above | 2, 3 |
| `totals` | aggregates the rows it is handed, nothing more | 1 |
| `split_population` | main-session rows and subagent rows | 1 |
| `moments` | quotes friction evidence only | 2, 3 |
| `cmd_pack` | picks populations; per-session rate over main only | 1 |
| label section | sampler, repo guard, report, sweep | 4 |
| `main` | adds the `label` subparser | 4 |

## Sibling fixes touching the same file

Three other fixes are being planned against `plugins/retro/bin/retro.py` at the
same time. The table below was checked against those three plans as they stood
on 2026-08-19 — `docs/plans/2026-08-19-plan-fix1-transcript-outcomes.md`,
`-fix3-extra-roots.md` and `-fix4-subagent-lens.md` — not inferred from their
titles. "Same lines" means a textual conflict is likely, not merely a semantic
one. Line numbers are against today's HEAD.

| Site in `retro.py` | This plan | Fix 1 (read failure) | Fix 3 (extra roots, gzip) | Fix 4 (subagent lens) |
|---|---|---|---|---|
| module docstring, 2-19 | Task 4 rewrites the "exactly one place" sentence and adds `label` to the subcommand list | edits the exit-code list four lines below | rewrites the subcommand and configuration description | lists four subcommands instead of two |
| tuning constants block, 42-57 | Task 2 adds approval phrases; Task 4 adds label constants | — | adds a constants entry after line 40 and a whole "Transcript roots" section before line 64 | adds its own thresholds and refusal markers |
| `COUNTERS` literal, 55-57 | Task 2 inserts `approval_turns` inside the list | — | — | inserts a separate `SUBAGENT_COUNTERS` list immediately after it — **adjacent insertion, same hunk**, but not the same list |
| `classify_user_turn`, 158-170 | Task 2 rewrites the body | — | — | — |
| new predicate near `is_error_record`, ~193 | Task 3 adds `is_tool_generated` | — | — | adds its own mistake predicates nearby |
| `read_records`, 205-221 | not modified; Task 4's sampler calls it | **raises `TranscriptUnreadable`** instead of yielding nothing | **rewrites the opener** for gzip and widens the exception clause | — |
| `measure` signature, 224 | — | changes the return contract | **adds a required `root` parameter** | — |
| `measure` user branch, 288-297 | **Tasks 2 and 3** | — | — | **same loop** — adds counters in the assistant and user branches |
| `measure` terminal condition and row dict, 304-331 | — | adds a conversation counter and changes the `None` return | rewrites `rel`, `is_subagent` and the session-id fallback | appends the subagent columns |
| `totals`, 417-429 | Task 1 rewrites it | — | — | — |
| `moments`, 445-471 | Task 2 (quote filter) and Task 3 (marker check), both in the loop body | splits it: `moments` becomes a wrapper and the body moves into `_moments` | rewrites the **first two lines** to resolve through the row's root | — |
| `cmd_extract`, 343-394 | not modified | rewrites the counting and the exit code | rewrites the walk, the stale loop and the summary | — |
| `cmd_pack`, 474-528 | Task 1 rewrites the header, table and rates | — | — | states its trend table and per-session line are unchanged |
| after `cmd_skills` / `main` subparsers, 566-590 | Task 4 adds the label section and the `label` subparser | — | — | **same insertion point** — adds a `subagents` section and subparser |

**Two consequences for this plan's own code, if a sibling lands first:**

- After Fix 1, the loop body this plan edits lives in `_moments`, not `moments`,
  and `read_records` raises. Task 4's `label_candidates` then wraps its
  per-transcript loop in `try: ... except TranscriptUnreadable: continue` — one
  candidate pool must not be lost to one bad file.
- After Fix 3, `measure` takes a root. Task 3's effect script calls
  `retro.measure(path)`; it becomes `retro.measure(path, root)` with the default
  root passed in.

**Recommended merge order, and why:**

1. **Task 1 (2a)** first — it touches `totals` and `cmd_pack` only, which no
   sibling rewrites. It needs no rebuild and no schema change, so it can land
   the moment it is reviewed.
2. **Fix 1, then Fix 3** — both restructure `read_records` and `measure`'s
   plumbing. Landing them before the counter changes means Tasks 2, 3 and 4 are
   written once, against the final shape.
3. **Task 2 (2c), then Task 3 (2b)** — both edit `measure`'s user branch; Task 3
   is a two-line addition on top of Task 2 and must not be rebased under it.
4. **Fix 4** — it inserts a second counter list immediately after the one Task 2
   edits and adds counters to the same `measure` loop. Landing it after both
   counter changes resolves that hunk once instead of twice.
5. **Task 4 (2d)** last — its sampler calls `read_records` and
   `classify_user_turn`, so it wants both in their settled form, and it inserts
   a section and a subparser at exactly the point Fix 4 also inserts one.

If a sibling lands first, the anchors named in each task shift but the edits do
not. Re-read the function before applying, never the line number.

## Parallelism

Task 1 is file-disjoint from Tasks 2-4 at function level and can run in its own
worktree concurrently with them. Tasks 2 → 3 → 4 are strictly serial: 3 edits
the lines 2 writes, and 4 depends on 2's class names. With four fixes already
converging on one 594-line file, one implementer taking Tasks 1-4 in order is
the lower-risk choice; if you do split, Lane A = Task 1 and Lane B = Tasks 2-4
is the only clean cut. Task 5's documentation edits touch no code and can be
drafted while Task 4's labelling run is out with the operator.

---

## Task 1: 2a — totals aggregates one population, cmd_pack picks it

**Files:**
- Modify: `plugins/retro/bin/retro.py` — `totals()` (currently 417-429),
  `cmd_pack()` (currently 474-528)
- Verify: throwaway scripts under a temp directory; nothing added to the repo

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `totals(rows) -> Counter` with keys `transcripts`, `tokens_out` and
  every name in `COUNTERS` — it no longer emits `sessions` or
  `subagent_transcripts`; `split_population(rows) -> (main_rows, sub_rows)`.
  Fix 4's `subagents` subcommand can call both.

- [ ] **Step 1: Write the failing check**

Write this to a temp directory (`export TMP_CHECK=$(mktemp -d)`), not into the
repo:

```python
# $TMP_CHECK/check_totals.py
import importlib.util, sys
spec = importlib.util.spec_from_file_location("retro", "plugins/retro/bin/retro.py")
retro = importlib.util.module_from_spec(spec); spec.loader.exec_module(retro)

rows = [
    {"is_subagent": False, "user_prompts": 10, "tokens_out": 100},
    {"is_subagent": True,  "user_prompts": 90, "tokens_out": 900},
]
main, sub = retro.split_population(rows)
assert [r["user_prompts"] for r in main] == [10], main
assert [r["user_prompts"] for r in sub] == [90], sub

t_main, t_sub = retro.totals(main), retro.totals(sub)
assert t_main["user_prompts"] == 10, t_main["user_prompts"]
assert t_main["transcripts"] == 1, t_main["transcripts"]
assert t_sub["user_prompts"] == 90, t_sub["user_prompts"]
assert t_sub["tokens_out"] == 900, t_sub["tokens_out"]
# the defect this task exists to remove: no caller can get a mixed aggregate
# by accident, because totals() no longer counts populations for you.
assert "sessions" not in t_main, "totals() must not classify rows"
print("ok")
```

- [ ] **Step 2: Run it and watch it fail**

Run from the worktree root: `python "$TMP_CHECK/check_totals.py"`
Expected: `AttributeError: module 'retro' has no attribute 'split_population'`

- [ ] **Step 3: Capture the baseline pack, before any edit**

```bash
export RETRO_HOME=$(mktemp -d)
python plugins/retro/bin/retro.py extract --rebuild
python plugins/retro/bin/retro.py pack --days 3650
cp "$RETRO_HOME"/pack-*-3650d.md "$RETRO_HOME/baseline-pack.md"
sed -n '1,25p' "$RETRO_HOME/baseline-pack.md"
```

Expected, on today's corpus: the trends table shows `user_prompts 11509` and
the per-session line shows `user_prompts 26.7`. Both are the defect. Keep the
ledger — 2a changes no counter, so the after-pack is an exact A/B against this
same ledger file.

- [ ] **Step 4: Replace `totals` and add `split_population`**

```python
def totals(rows):
    """Aggregate the rows handed in, and nothing else.

    The caller chooses the population. Deciding it here is what made the
    printed rates wrong: every counter was summed over all transcripts and then
    divided by a session count that excluded subagent transcripts.
    """
    agg = Counter()
    for row in rows:
        for key in COUNTERS:
            agg[key] += int(row.get(key) or 0)
        agg["tokens_out"] += int(row.get("tokens_out") or 0)
        agg["transcripts"] += 1
    return agg


def split_population(rows):
    """Main-session rows, then subagent rows. One row per transcript either
    way — subagent transcripts are spend, not sessions, and counting them as
    sessions deflates every per-session rate."""
    main = [row for row in rows if not row.get("is_subagent")]
    sub = [row for row in rows if row.get("is_subagent")]
    return main, sub
```

- [ ] **Step 5: Run the check and watch it pass**

Run: `python "$TMP_CHECK/check_totals.py"`
Expected: `ok`

- [ ] **Step 6: Rewrite the head of `cmd_pack`**

Replace everything from `now_t, prev_t = totals(window), totals(prior)` down to
and including the `ranked = sorted(...)` line with:

```python
    main, sub = split_population(window)
    prior_main, prior_sub = split_population(prior)
    now_t, prev_t = totals(main), totals(prior_main)
    now_s = totals(sub)

    lines = [f"# Evidence pack — last {args.days} days",
             f"Window: {start} to {now}. Sessions: {len(main)} "
             f"(prior window: {len(prior_main)}).", "",
             "## Trends", "",
             "Main sessions only. Subagent transcripts are spend and are "
             "reported under the table — every rate here divides one "
             "population by itself.", "",
             "| signal | this window | prior | delta |", "|---|---|---|---|"]
    table = [("sessions", len(main), len(prior_main))]
    table += [(key, now_t[key], prev_t[key]) for key in ["tokens_out"] + COUNTERS]
    for key, a, b in table:
        delta = "n/a" if not b else f"{(a - b) / b * 100:+.0f}%"
        lines.append(f"| {key} | {a} | {b} | {delta} |")

    lines.append("")
    if main:
        lines.append("Per session: " + ", ".join(
            f"{key} {now_t[key] / len(main):.1f}" for key in COUNTERS))
    lines.append("")
    lines.append(f"Subagent spend — {len(sub)} transcripts "
                 f"(prior window: {len(prior_sub)}), no per-session rate: "
                 + ", ".join(f"{key} {now_s[key]}"
                             for key in ["tokens_out"] + COUNTERS))
    lines += ["", "## Moments", ""]

    ranked = sorted(main, key=friction_score, reverse=True)[:args.sessions]
```

Two things this also removes: the `subagent_transcripts` row from the trends
table, which is now the count at the head of the spend line, and the second
`is_subagent` filter that `ranked` used to do for itself.

- [ ] **Step 7: Re-run the pack against the same ledger and diff**

```bash
python plugins/retro/bin/retro.py pack --days 3650
diff "$RETRO_HOME/baseline-pack.md" "$RETRO_HOME"/pack-*-3650d.md | head -40
```

Expected on today's corpus: trends `user_prompts` 11509 → 3877, `turns` 96146 →
42635, `tool_calls` 49469 → 19747, `correction_turns` 989 → 988, `interrupts`
201 → 163; per-session `user_prompts` 26.7 → 9.0, `turns` 223.1 → 98.9; a new
spend line reading 1467 transcripts with `user_prompts 7632`. The `## Moments`
section must be byte-identical — 2a changes what is printed above it and
nothing about which sessions are quoted.

- [ ] **Step 8: Confirm the moments section did not move**

```bash
python - <<'PY'
import os, glob
base = open(os.path.join(os.environ["RETRO_HOME"], "baseline-pack.md"), encoding="utf-8").read()
new = open(sorted(glob.glob(os.path.join(os.environ["RETRO_HOME"], "pack-*-3650d.md")))[-1], encoding="utf-8").read()
cut = lambda s: s[s.index("## Moments"):]
print("moments identical:", cut(base) == cut(new))
PY
```

Expected: `moments identical: True`

- [ ] **Step 9: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: one population per ratio in the pack

totals() summed every counter over all transcripts and cmd_pack divided by a
session count that excluded subagent transcripts, so every printed per-session
rate mixed two populations - prompts per session read 26.7 against a true 9.0.

totals() now aggregates whatever rows it is handed and classifies nothing;
split_population() names the two populations; the trends table and the
per-session line are main sessions only, and subagent spend gets its own line
with no rate attached to it.

No counter changed and no rebuild is needed: the moments section of a pack
built from the same ledger is byte-identical.
MSG
```

---

## Task 2: 2c — split the correction signal

**Blocked on Question 1** (the approval phrase list). Everything else in this
task is fully specified; the blocked datum is one tuple.

**Files:**
- Modify: `plugins/retro/bin/retro.py` — tuning constants (currently 42-57),
  `classify_user_turn()` (158-170), `measure()` user branch (288-297),
  `moments()` (445-471)
- Verify: throwaway scripts under a temp directory

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `classify_user_turn(body, prior_assistant_chars) -> str` returning
  one of `"interrupt"`, `"question"`, `"approval"`, `"correction"`, `""`;
  `is_approval(reply) -> bool` taking an already-stripped reply; `COUNTERS`
  containing `"approval_turns"` between `"correction_turns"` and `"interrupts"`.
  Task 4's sweep calls `is_approval`.

**The reading this task implements, stated so a reviewer can reject it:** the
three-way split applies only inside the population the spec's table describes —
a short reply after a substantial assistant turn. A long reply ending in `?` is
a new request and stays unclassified, exactly as today. The alternative reading,
where any question-shaped turn is classified, would flood the moments section
with questions and change what `moments()` walks past. See Question 3.

- [ ] **Step 1: Get Question 1 answered and write the phrase list**

Fill the tuple in Step 4 with the operator's answer. Do not invent entries: the
list decides which turns stop counting as friction, and a wrong entry silently
deletes evidence from the ranking. If the operator wants the corpus to inform
the answer, this produces the raw material without putting any of it in the
repo — it writes to the work directory and prints a count only:

```bash
export RETRO_HOME=$(mktemp -d)
python - <<'PY'
import importlib.util, os, collections
from pathlib import Path
spec = importlib.util.spec_from_file_location("retro", "plugins/retro/bin/retro.py")
retro = importlib.util.module_from_spec(spec); spec.loader.exec_module(retro)
root = Path.home() / ".claude" / "projects"
freq = collections.Counter()
for path in sorted(root.rglob("*.jsonl")):
    if "subagents/" in path.relative_to(root).as_posix():
        continue
    prior = 0
    for rec in retro.read_records(path):
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "assistant":
            prior = len(retro.text_of(rec.get("message") or {}))
        elif rec.get("type") == "user" and rec.get("toolUseResult") is None:
            body = retro.text_of(rec.get("message") or {})
            if retro.classify_user_turn(body, prior) == "correction":
                freq[retro.redact(body.strip()).lower()] += 1
            prior = 0
out = Path(os.environ["RETRO_HOME"]) / "correction-replies.txt"
out.write_text("\n".join(f"{n}\t{t}" for t, n in freq.most_common(200)), encoding="utf-8")
print(f"{sum(freq.values())} correction replies, {len(freq)} distinct -> {out}")
PY
```

The file stays in the work directory. Its contents are message text and never
enter this document, a commit message, or any other tracked file.

- [ ] **Step 2: Write the failing check**

```python
# $TMP_CHECK/check_classify.py
import importlib.util
spec = importlib.util.spec_from_file_location("retro", "plugins/retro/bin/retro.py")
retro = importlib.util.module_from_spec(spec); spec.loader.exec_module(retro)

LONG = 1000  # a substantial prior assistant turn
cases = [
    ("[Request interrupted by user]", LONG, "interrupt"),
    ("why did that step run twice?", LONG, "question"),
    ("no, revert that", LONG, "correction"),
    ("no, revert that", 10, ""),          # prior turn too short to classify
    ("x" * 500, LONG, ""),                # too long to be a reply
    ("", LONG, ""),
]
for body, prior, expected in cases:
    got = retro.classify_user_turn(body, prior)
    assert got == expected, (body[:20], prior, got, expected)

# an approval phrase from the operator's list, whole-reply and punctuated
assert retro.is_approval("yes"), "fill APPROVAL_PHRASES"
assert retro.classify_user_turn("yes", LONG) == "approval"
assert retro.classify_user_turn("yes, but drop the cache", LONG) == "correction"
assert "approval_turns" in retro.COUNTERS
print("ok")
```

Replace `"yes"` in the last three assertions with a phrase the operator actually
supplied, if `"yes"` is not on their list.

- [ ] **Step 3: Run it and watch it fail**

Run: `python "$TMP_CHECK/check_classify.py"`
Expected: `AssertionError: ('why did that step run', 1000, 'correction', 'question')`

- [ ] **Step 4: Add the approval constants beside the correction thresholds**

Immediately after `CORRECTION_MIN_PRIOR_CHARS`:

```python
# A short reply that only agrees is the process working, not friction. The whole
# reply has to be one of these, ignoring case and trailing punctuation: "yes" is
# an approval, "yes, but drop the cache" is a correction.
APPROVAL_PHRASES = (
    # Supplied by the operator - see Question 1 of
    # docs/plans/2026-08-19-plan-fix2-population-and-classification.md
)
_APPROVAL_TAIL = re.compile(r"[\s.!,]+$")
_APPROVAL = re.compile(
    r"^(?:%s)$" % "|".join(re.escape(p) for p in APPROVAL_PHRASES), re.I)
```

- [ ] **Step 5: Add `is_approval` above `classify_user_turn`**

```python
def is_approval(reply):
    """Is this whole reply nothing but agreement? `reply` is already stripped.

    Shared with the label report's threshold sweep, so the sweep cannot drift
    from the rule the ledger was built with.
    """
    if not APPROVAL_PHRASES:
        return False
    return bool(_APPROVAL.match(_APPROVAL_TAIL.sub("", reply)))
```

- [ ] **Step 6: Replace `classify_user_turn`**

```python
def classify_user_turn(body, prior_assistant_chars):
    """The pack's central definition, in one place: what a user turn means.

    Returns "interrupt", "question", "approval", "correction" or "". Precedence
    is fixed in that order, so a turn that could read as two things is always
    the earlier one. `measure` counts the result and `moments` quotes it, so
    both read the same rule rather than two copies that drift.

    Only a short reply to a substantial assistant turn is classified at all.
    Anything longer is a new request, and returns "" as it always has.
    """
    if _INTERRUPT.search(body):
        return "interrupt"
    reply = body.strip()
    if not (0 < len(reply) <= CORRECTION_MAX_CHARS
            and prior_assistant_chars >= CORRECTION_MIN_PRIOR_CHARS):
        return ""
    if reply.endswith("?"):
        return "question"
    if is_approval(reply):
        return "approval"
    return "correction"
```

- [ ] **Step 7: Add the ledger column**

```python
COUNTERS = ["turns", "user_prompts", "tool_calls", "tool_errors", "tool_retries",
            "correction_turns", "approval_turns", "interrupts",
            "permission_mode_changes", "queued_prompts", "skill_runs"]
```

`friction_score` is not touched: approvals are recorded, not scored.

- [ ] **Step 8: Count approvals in `measure`**

In the `elif rtype == "user":` branch, replace the `if kind == "correction":`
line and its body with:

```python
                if kind == "correction":
                    m["correction_turns"] += 1
                elif kind == "approval":
                    m["approval_turns"] += 1
```

Questions add to `user_prompts` and to nothing else — they are prompts, just
not friction.

- [ ] **Step 9: Stop `moments` quoting approvals and questions**

In `moments()`, replace `if kind:` with:

```python
            if kind in ("interrupt", "correction"):
```

The moments section is friction evidence. A question or an approval is
classified so it stops inflating `correction_turns`, not so it can be quoted
back as something that went wrong.

- [ ] **Step 10: Run the check and watch it pass**

Run: `python "$TMP_CHECK/check_classify.py"`
Expected: `ok`

- [ ] **Step 11: Measure the corpus effect in one pass, old rule against new**

One pass, both rules, so the two numbers come from the same corpus snapshot —
comparing two rebuilds taken minutes apart would fold in whatever sessions ran
in between:

```python
# $TMP_CHECK/effect_2c.py
import importlib.util, collections
from pathlib import Path
spec = importlib.util.spec_from_file_location("retro", "plugins/retro/bin/retro.py")
retro = importlib.util.module_from_spec(spec); spec.loader.exec_module(retro)

root = Path.home() / ".claude" / "projects"
agg = {"main": collections.Counter(), "sub": collections.Counter()}
for path in sorted(root.rglob("*.jsonl")):
    rel = path.relative_to(root).as_posix()
    a = agg["sub" if "subagents/" in rel else "main"]
    prior = 0
    for rec in retro.read_records(path):
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "assistant":
            prior = len(retro.text_of(rec.get("message") or {}))
        elif rec.get("type") == "user":
            body = retro.text_of(rec.get("message") or {})
            kind = retro.classify_user_turn(body, prior)
            if kind == "interrupt":
                a["interrupts"] += 1
            elif rec.get("toolUseResult") is None and body.strip():
                a["user_prompts"] += 1
                if kind in ("correction", "question", "approval"):
                    a["old_correction_turns"] += 1
                a[f"{kind or 'plain'}_turns"] += 1
                prior = 0
for name in ("main", "sub"):
    print(name, dict(sorted(agg[name].items())))
```

Run: `python "$TMP_CHECK/effect_2c.py"`
Expected on today's corpus: main `old_correction_turns` 988, of which
`question_turns` 380; `correction_turns` = 608 minus however many the approval
list matches; `user_prompts` unchanged at 3877; `interrupts` unchanged at 163.
Record the approval count — it is one of the aggregates Task 5 writes into the
spec.

- [ ] **Step 12: Rebuild the ledger and confirm the pack**

```bash
export RETRO_HOME=$(mktemp -d)
python plugins/retro/bin/retro.py extract --rebuild
python plugins/retro/bin/retro.py pack --days 3650
grep -c '\*\*question\*\*\|\*\*approval\*\*' "$RETRO_HOME"/pack-*-3650d.md
grep '^| correction_turns\|^| approval_turns' "$RETRO_HOME"/pack-*-3650d.md
```

Expected: the grep count is `0` — no question or approval is ever quoted as a
moment; the trends table carries both counters and `correction_turns` matches
the number Step 11 predicted. Rebuild time stays near 5.2 s.

- [ ] **Step 13: Commit**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: split the correction signal into correction, approval and question

A short reply after a long assistant turn was counted as friction whatever it
said. Measured: 38.5% of them end in a question mark, and asking a question is
not a correction. Approvals were in there too - the process working, counted as
the process failing.

classify_user_turn now returns interrupt, question, approval, correction or
nothing, in that precedence. Questions and approvals still count as prompts,
because they are prompts. approval_turns is recorded and not scored;
friction_score is unchanged. moments quotes only interrupts and corrections -
an approval is not evidence that something went wrong.

New ledger column and a redefined counter, so this needs extract --rebuild
before any trend across it means anything.
MSG
```

---

## Task 3: 2b — exclude tool-generated turns (droppable)

**Read the cost before starting.** This task forces a second full
`extract --rebuild`, breaks trend comparability across the change, and moves 6
of 431 main rows with the top-20 order unchanged. What it does buy: the subagent
population's counters stop being 89% machine text (7,632 counted prompts, 6,787
of them tool-written), and 9 main-session interrupt records that a tool wrote
stop scoring as human interrupts. It is deliberately last and deliberately
small — one predicate and three call sites. Dropping it is `git revert` of one
commit, and nothing in Tasks 1, 2, 4 or 5 depends on it.

**Files:**
- Modify: `plugins/retro/bin/retro.py` — new predicate after `is_error_record()`
  (currently ends 193), `measure()` user branch, `moments()` record filter

**Interfaces:**
- Consumes: Task 2's `classify_user_turn` return values.
- Produces: `is_tool_generated(rec) -> bool`.

- [ ] **Step 1: Write the failing check**

```python
# $TMP_CHECK/check_marker.py
import importlib.util
spec = importlib.util.spec_from_file_location("retro", "plugins/retro/bin/retro.py")
retro = importlib.util.module_from_spec(spec); spec.loader.exec_module(retro)

assert retro.is_tool_generated({"type": "user", "sourceToolAssistantUUID": "u"}) is True
assert retro.is_tool_generated({"type": "user"}) is False
assert retro.is_tool_generated({"type": "user", "toolUseResult": "x"}) is False
print("ok")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python "$TMP_CHECK/check_marker.py"`
Expected: `AttributeError: module 'retro' has no attribute 'is_tool_generated'`

- [ ] **Step 3: Add the predicate after `is_error_record`**

```python
def is_tool_generated(rec):
    """Was this user-role record written by a tool rather than typed?

    `sourceToolAssistantUUID` names the assistant turn whose tool call produced
    the text. Measured over the corpus 2026-08-19: the key is on 49,516 records,
    every one of them a user record, never with an empty value. The older
    `toolUseResult` guard misses 6,787 of them, all in subagent transcripts.

    A precision instrument, not a complete one: unmarked records carrying
    machine wrapper tags exist and this does not find them. On older transcripts
    the marker coincides exactly with `toolUseResult`, so this is a no-op there
    and the ledger will show that as a step in the trend rather than a change in
    behaviour.
    """
    return "sourceToolAssistantUUID" in rec
```

- [ ] **Step 4: Apply it in `measure`**

In the `elif rtype == "user":` branch:

```python
            if kind == "interrupt":
                if not is_tool_generated(rec):
                    m["interrupts"] += 1
            elif (rec.get("toolUseResult") is None and not is_tool_generated(rec)
                    and body.strip()):
```

Interrupts get the marker rule but not the `toolUseResult` rule — an interrupt
marker legitimately arrives on a record that also carries a tool result, and
excluding those would drop real interrupts.

- [ ] **Step 5: Apply it in `moments`**

```python
        elif (rec.get("type") == "user" and rec.get("toolUseResult") is None
                and not is_tool_generated(rec)):
```

Measured today this is a no-op on main-session rows — no main record carries the
marker without also carrying a tool result — so it changes no pack output now.
It is the same rule the counters use, and it is what keeps a foreign or older
transcript root from quoting machine text as a human turn.

- [ ] **Step 6: Run the check and watch it pass**

Run: `python "$TMP_CHECK/check_marker.py"`
Expected: `ok`

- [ ] **Step 7: Measure the effect and the ranking, in one pass**

```python
# $TMP_CHECK/effect_2b.py
import importlib.util, collections
from pathlib import Path
spec = importlib.util.spec_from_file_location("retro", "plugins/retro/bin/retro.py")
retro = importlib.util.module_from_spec(spec); spec.loader.exec_module(retro)

root = Path.home() / ".claude" / "projects"
agg = {"main": collections.Counter(), "sub": collections.Counter()}
scored = []
for path in sorted(root.rglob("*.jsonl")):
    rel = path.relative_to(root).as_posix()
    is_sub = "subagents/" in rel
    a = agg["sub" if is_sub else "main"]
    row = retro.measure(path)
    if row is None:
        continue
    prior = 0
    c_kept = c_all = i_kept = i_all = 0
    for rec in retro.read_records(path):
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "assistant":
            prior = len(retro.text_of(rec.get("message") or {}))
        elif rec.get("type") == "user":
            body = retro.text_of(rec.get("message") or {})
            kind = retro.classify_user_turn(body, prior)
            marked = retro.is_tool_generated(rec)
            if kind == "interrupt":
                i_all += 1
                i_kept += 0 if marked else 1
            elif rec.get("toolUseResult") is None and body.strip():
                a["prompts_before"] += 1
                if not marked:
                    a["prompts_after"] += 1
                if kind == "correction":
                    c_all += 1
                    c_kept += 0 if marked else 1
                prior = 0
    a["corrections_before"] += c_all; a["corrections_after"] += c_kept
    a["interrupts_before"] += i_all;  a["interrupts_after"] += i_kept
    if not is_sub:
        base = (int(row["permission_mode_changes"]) * 3
                + int(row["tool_retries"]) * 2 + int(row["tool_errors"]))
        scored.append((rel, base + 4 * c_all + 4 * i_all,
                       base + 4 * c_kept + 4 * i_kept))
for name in ("main", "sub"):
    print(name, dict(sorted(agg[name].items())))
top_before = [r for r, b, _ in sorted(scored, key=lambda s: -s[1])[:20]]
top_after = [r for r, _, a in sorted(scored, key=lambda s: -s[2])[:20]]
print("main rows", len(scored),
      "score moved", sum(1 for _, b, a in scored if b != a),
      "top20 order identical", top_before == top_after)
```

Run: `python "$TMP_CHECK/effect_2b.py"`
Expected on today's corpus: main `prompts_before` 3877 = `prompts_after`;
main `interrupts_before` 163, `interrupts_after` 154; subagent
`prompts_before` 7632, `prompts_after` 1584; subagent `interrupts` 38 → 19;
`score moved` 6 of 431; `top20 order identical True`. If the top-20 order is not
identical, stop and report it — this task's cost/benefit assumes it.

- [ ] **Step 8: Rebuild and confirm the totals**

```bash
export RETRO_HOME=$(mktemp -d)
time python plugins/retro/bin/retro.py extract --rebuild
python plugins/retro/bin/retro.py pack --days 3650
grep '^| user_prompts\|^| interrupts' "$RETRO_HOME"/pack-*-3650d.md
```

Expected: main `user_prompts` unchanged at 3877, `interrupts` 163 → 154, rebuild
near 5.2 s.

- [ ] **Step 9: Commit, alone, so it can be reverted alone**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
fix: do not count tool-written user records as human turns

A user-role record carrying sourceToolAssistantUUID was generated by a tool.
The toolUseResult guard misses 6,787 of them, all in subagent transcripts,
where they were most of the counted prompts.

Stated plainly, because it is less than it sounds: in main sessions this changes
prompts not at all and interrupts by 9, it moves 6 session rows of 431, and it
leaves the top-20 friction order identical. What it fixes is the subagent
population's counters and the interrupt count. It costs a full rebuild and it
breaks trend comparability across the change; on older transcripts the marker
coincides with toolUseResult, so the ledger will show a step there rather than a
change in behaviour.

Kept to one predicate and three call sites so it can be reverted without
disturbing anything else.
MSG
```

---

## Task 4: 2d — a `label` subcommand that measures the heuristics

**Blocked on Question 2** (how the operator marks the file). The shape below —
one JSON object per line with an empty `label` field for the operator to fill —
is what the rest of the task is written against; if the answer differs, only
`write_labels` and `read_labels` change, and the sampler, the guard and the
report arithmetic do not.

**Files:**
- Modify: `plugins/retro/bin/retro.py` — module docstring (2-19), tuning
  constants, new `label` section before `main()`, `main()` subparsers
- Verify: throwaway `RETRO_HOME`; the labelling file never leaves the work
  directory

**Interfaces:**
- Consumes: `classify_user_turn`, `is_approval`, `redact`, `read_records`,
  `text_of`, `signature` — all existing. If Fix 1 lands first, `read_records`
  raises `TranscriptUnreadable`, and `label_candidates`' per-transcript loop
  gains `try: ... except TranscriptUnreadable: continue` so one bad file cannot
  cost the whole pool.
- Produces: `cmd_label(args)`; the labelling file at `WORK_DIR/labels.jsonl`.

**Sampling decisions, stated so a reviewer can reject them:**

1. **Main-session transcripts only.** The thresholds exist to rank sessions, and
   subagent transcripts are excluded from that ranking. See Question 4.
2. **Selection is by hash, not by a seeded shuffle.** A shuffle reseeded over a
   grown corpus draws different turns every run; ranking each candidate by a
   hash of its own identity keeps a rerun on the same turns.
3. **Recall for retries is not estimable from this sample.** Only flagged
   retries are drawn, so the report prints retry precision and says so, rather
   than printing a recall number that means nothing.
4. **The two turn strata are reweighted to corpus scale.** 150 firing turns and
   150 quiet ones are not proportional to the corpus, so raw precision and
   recall over the sample would be an artefact of the sampling. Each sample
   carries its stratum's population size and the report weights by it.

- [ ] **Step 1: Get Question 2 answered**

If the answer changes the file's shape, adjust `write_labels` and `read_labels`
in Step 6 and nothing else.

- [ ] **Step 2: Write the failing check**

```python
# $TMP_CHECK/check_label.py
import importlib.util, os, subprocess, sys
spec = importlib.util.spec_from_file_location("retro", "plugins/retro/bin/retro.py")
retro = importlib.util.module_from_spec(spec); spec.loader.exec_module(retro)

# the guard: a work directory inside a repository is refused
env = dict(os.environ, RETRO_HOME=os.path.join(os.getcwd(), ".retro-should-refuse"))
p = subprocess.run([sys.executable, "plugins/retro/bin/retro.py", "label"],
                   env=env, capture_output=True, text=True)
assert p.returncode == 2, (p.returncode, p.stderr)
assert "repository" in p.stderr, p.stderr
assert not os.path.exists(os.path.join(env["RETRO_HOME"], "labels.jsonl"))

# the sweep re-predicts from stored numbers, not stored text
s = {"predicted": "correction", "reply_chars": 30, "prior_chars": 1000,
     "said": "no, revert that"}
assert retro._predict_at(s, 120, 400) == "correction"
assert retro._predict_at(s, 20, 400) == "none"
assert retro._predict_at(s, 120, 2000) == "none"
assert retro._predict_at(dict(s, said="why?"), 120, 400) == "question"
print("ok")
```

- [ ] **Step 3: Run it and watch it fail**

Run: `python "$TMP_CHECK/check_label.py"`
Expected: a nonzero exit complaining that `label` is an invalid choice — the
subcommand does not exist yet.

- [ ] **Step 4: Update the module docstring**

Replace the "Counts only" paragraph with:

```
Counts only, with two exceptions. Message text leaves this script in exactly two
places - the `moments` section of a pack, and the labelling file written by
`label` - and only after passing through redact(). Both land in the work
directory, which must sit outside every git repository: redact() strips machine
and credential shapes, not the names and paths of whatever the sessions were
about.
```

And add `label` to the subcommand list at the top of the docstring:

```
    label     sample turns and retry candidates for hand labelling, and report
              precision, recall and a threshold sweep from the marked file
```

- [ ] **Step 5: Add the label constants beside the other tuning constants**

```python
# --- Labelling -------------------------------------------------------------
# The sample the thresholds above are argued from. 150 a side is enough to
# separate a precision of 0.9 from one of 0.7 and small enough to mark in a
# sitting.
LABEL_SAMPLE_SIZE = 150
LABEL_SEED = "retro-label-v1"
LABEL_AFTER_CHARS = 600     # of the assistant turn before, for context
LABEL_SAID_CHARS = 400      # of the reply itself
LABEL_INPUT_CHARS = 300     # of a tool input, for a retry candidate
TURN_LABELS = ("interrupt", "question", "approval", "correction", "none")
RETRY_LABELS = ("wasteful", "legitimate")
# Swept against the marks. The top of the reply sweep stays under
# LABEL_SAID_CHARS, or the stored reply would be truncated where the rule reads.
SWEEP_MAX_CHARS = (60, 90, 120, 160, 200, 300)
SWEEP_MIN_PRIOR = (0, 200, 400, 800, 1600)
```

- [ ] **Step 6: Add the label section immediately before `main()`**

```python
# --- label -----------------------------------------------------------------

def labels_file():
    """The labelling file, under the work directory. A function rather than a
    constant so a run with RETRO_HOME pointed elsewhere lands there."""
    return WORK_DIR / "labels.jsonl"


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


def _rank(sample_id):
    """Deterministic order over candidates. A seeded shuffle draws different
    turns once the corpus grows; hashing each candidate's own id keeps a rerun
    on the same turns."""
    return hashlib.sha1(f"{LABEL_SEED}|{sample_id}".encode("utf-8")).hexdigest()


def _sample_id(rel, index, extra=""):
    """Opaque and stable. The transcript path is hashed rather than stored: the
    file needs to identify a sample across reruns, not to name a session."""
    raw = f"{rel}#{index}#{extra}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def label_candidates():
    """Walk main-session transcripts once for three candidate pools: turns the
    classifier fires on, turns it does not, and flagged tool retries.

    Main sessions only - the thresholds exist to rank sessions, and subagent
    transcripts are excluded from that ranking.
    """
    fires, quiet, retries = [], [], []
    for path in sorted(PROJECTS_DIR.rglob("*.jsonl")):
        try:
            rel = path.relative_to(PROJECTS_DIR).as_posix()
        except ValueError:
            rel = path.name
        if "subagents/" in rel:
            continue
        prior = ""
        date = ""
        seen = {}
        first_input = {}
        for index, rec in enumerate(read_records(path)):
            if not isinstance(rec, dict):
                continue
            date = date or str(rec.get("timestamp") or "")[:10]
            rtype = rec.get("type")
            if rtype == "assistant":
                msg = rec.get("message") or {}
                prior = text_of(msg)
                content = msg.get("content")
                for block in content if isinstance(content, list) else []:
                    if not (isinstance(block, dict)
                            and block.get("type") == "tool_use"):
                        continue
                    name = block.get("name") or "?"
                    sig = signature(block.get("input"))
                    shown = redact(json.dumps(block.get("input"), sort_keys=True,
                                              default=str))[:LABEL_INPUT_CHARS]
                    key = (name, sig)
                    if sig and key in seen:
                        seen[key] += 1
                        retries.append({
                            "id": _sample_id(rel, index, name),
                            "kind": "retry", "predicted": "retry", "label": "",
                            "date": date, "tool": name, "repeat": seen[key],
                            "first_input": first_input.get(key, ""),
                            "repeat_input": shown,
                        })
                    else:
                        seen[key] = 1
                        first_input[key] = shown
            elif rtype == "user" and rec.get("toolUseResult") is None:
                body = text_of(rec.get("message") or {})
                if not body.strip():
                    continue
                kind = classify_user_turn(body, len(prior))
                sample = {
                    "id": _sample_id(rel, index),
                    "kind": "turn", "predicted": kind or "none", "label": "",
                    "date": date,
                    # unredacted lengths: redact() shortens the reply and the
                    # stored context is truncated, so a sweep over the stored
                    # text would be measuring the wrong lengths.
                    "reply_chars": len(body.strip()),
                    "prior_chars": len(prior),
                    "after": redact(prior.strip()[-LABEL_AFTER_CHARS:]),
                    "said": redact(body.strip())[:LABEL_SAID_CHARS],
                }
                (fires if kind else quiet).append(sample)
                prior = ""
    return fires, quiet, retries


def draw_sample(pools):
    """Take LABEL_SAMPLE_SIZE from each pool, tagging every sample with the pool
    it came from and how big that pool was. The report needs both: 150 a side is
    not proportional to the corpus, so an unweighted precision would be an
    artefact of the sampling."""
    out = []
    for stratum, pool in pools.items():
        chosen = sorted(pool, key=lambda s: _rank(s["id"]))[:LABEL_SAMPLE_SIZE]
        for sample in chosen:
            sample["stratum"] = stratum
            sample["stratum_population"] = len(pool)
            sample["stratum_sampled"] = len(chosen)
        out += chosen
    return out


def write_labels(samples, path):
    with open(path, "w", encoding="utf-8") as fh:
        for sample in samples:
            fh.write(json.dumps(sample) + "\n")


def read_labels(path):
    if not path.exists():
        print(f"no labelling file at {path} - run `label` first", file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _predict_at(sample, max_chars, min_prior):
    """Re-run the turn classifier at a candidate pair of thresholds, from the
    stored numbers rather than the stored text - the text is redacted and
    truncated, the numbers are the reply's real lengths."""
    if sample["predicted"] == "interrupt":
        return "interrupt"
    if not (0 < sample["reply_chars"] <= max_chars
            and sample["prior_chars"] >= min_prior):
        return "none"
    said = sample["said"].strip()
    if said.endswith("?"):
        return "question"
    if is_approval(said):
        return "approval"
    return "correction"


def _weighted(marked, predict):
    """Precision and recall per class, weighted back to corpus scale."""
    out = {}
    for cls in TURN_LABELS:
        tp = fp = fn = 0.0
        for sample in marked:
            weight = sample["stratum_population"] / sample["stratum_sampled"]
            got, want = predict(sample), sample["label"]
            if got == cls and want == cls:
                tp += weight
            elif got == cls:
                fp += weight
            elif want == cls:
                fn += weight
        out[cls] = (tp / (tp + fp) if tp + fp else float("nan"),
                    tp / (tp + fn) if tp + fn else float("nan"),
                    tp + fn)
    return out


def report_labels(samples):
    turns = [s for s in samples if s.get("kind") == "turn" and s.get("label")]
    retries = [s for s in samples if s.get("kind") == "retry" and s.get("label")]
    total_turns = sum(1 for s in samples if s.get("kind") == "turn")
    total_retries = sum(1 for s in samples if s.get("kind") == "retry")
    print(f"# Labelled {len(turns)} of {total_turns} turns, "
          f"{len(retries)} of {total_retries} retry candidates\n")
    if not turns:
        print("nothing marked yet", file=sys.stderr)
        return EXIT_CANNOT_RUN

    print(f"## Turn classifier at the settled thresholds "
          f"(reply <= {CORRECTION_MAX_CHARS}, "
          f"prior >= {CORRECTION_MIN_PRIOR_CHARS})\n")
    print("| class | precision | recall | corpus turns |")
    print("|---|---|---|---|")
    for cls, (p, r, n) in _weighted(turns, lambda s: s["predicted"]).items():
        print(f"| {cls} | {p:.2f} | {r:.2f} | {n:.0f} |")

    print("\n## Threshold sweep, correction class\n")
    print("| reply <= | prior >= | precision | recall | corpus corrections |")
    print("|---|---|---|---|---|")
    for max_chars in SWEEP_MAX_CHARS:
        for min_prior in SWEEP_MIN_PRIOR:
            p, r, n = _weighted(
                turns,
                lambda s, a=max_chars, b=min_prior: _predict_at(s, a, b),
            )["correction"]
            print(f"| {max_chars} | {min_prior} | {p:.2f} | {r:.2f} | {n:.0f} |")

    if retries:
        wasteful = sum(1 for s in retries if s["label"] == "wasteful")
        print(f"\n## tool_retries\n\nprecision {wasteful / len(retries):.2f} "
              f"over {len(retries)} marked candidates. Recall is not estimable "
              "from this sample: only flagged retries were drawn.")
        by_tool = Counter(s["tool"] for s in retries if s["label"] == "wasteful")
        print("\n| tool | marked wasteful |\n|---|---|")
        for name, count in by_tool.most_common():
            print(f"| {name} | {count} |")
    return EXIT_CLEAN


def cmd_label(args):
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    path = labels_file()
    refuse_inside_repo(path)
    if args.report:
        return report_labels(read_labels(path))
    if not PROJECTS_DIR.is_dir():
        print(f"no session directory at {PROJECTS_DIR}", file=sys.stderr)
        sys.exit(EXIT_CANNOT_RUN)
    if path.exists() and not args.resample:
        print(f"{path} exists - mark it, then run `label --report` "
              "(or `label --resample` to redraw)", file=sys.stderr)
        return EXIT_CANNOT_RUN

    kept = {s["id"]: s.get("label", "") for s in
            (read_labels(path) if path.exists() else [])}
    fires, quiet, retries = label_candidates()
    samples = draw_sample({"fires": fires, "quiet": quiet, "retries": retries})
    carried = 0
    for sample in samples:
        if kept.get(sample["id"]):
            sample["label"] = kept[sample["id"]]
            carried += 1
    write_labels(samples, path)
    print(f"pools: {len(fires)} firing, {len(quiet)} quiet, "
          f"{len(retries)} retry candidates")
    print(f"sampled {len(samples)} into {path}" +
          (f", {carried} marks carried over" if carried else ""))
    print('mark each line\'s "label": turns take one of '
          f"{'/'.join(TURN_LABELS)}, retries one of {'/'.join(RETRY_LABELS)}. "
          "Then: retro label --report")
    print("This file holds message text and stays in the work directory. "
          "Only aggregates from --report go anywhere tracked.")
    return EXIT_CLEAN
```

- [ ] **Step 7: Register the subcommand in `main()`**

Immediately after the `p_skills` block:

```python
    p_label = sub.add_parser("label",
                             help="sample turns and retries for hand labelling")
    p_label.add_argument("--report", action="store_true",
                         help="read the marked file back and report")
    p_label.add_argument("--resample", action="store_true",
                         help="redraw the sample, carrying existing marks over")
    p_label.set_defaults(func=cmd_label)
```

- [ ] **Step 8: Run the check and watch it pass**

Run: `python "$TMP_CHECK/check_label.py"`
Expected: `ok`

- [ ] **Step 9: Draw the sample and prove the file is safe**

```bash
export RETRO_HOME=$(mktemp -d)
python plugins/retro/bin/retro.py label
python - <<'PY'
import json, os, re
from pathlib import Path
path = Path(os.environ["RETRO_HOME"]) / "labels.jsonl"
rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
kinds = {}
for r in rows:
    kinds[r["stratum"]] = kinds.get(r["stratum"], 0) + 1
print("samples by stratum:", kinds)
print("all unmarked:", all(r["label"] == "" for r in rows))
print("ids unique:", len({r["id"] for r in rows}) == len(rows))
blob = path.read_text(encoding="utf-8")
home, user = str(Path.home()), Path.home().name
print("home path absent:", home not in blob and home.replace("\\", "/") not in blob)
print("account name absent:", not re.search(r"\b%s\b" % re.escape(user), blob, re.I))
print("email absent:", not re.search(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", blob))
print("turn samples carry both lengths:",
      all("reply_chars" in r and "prior_chars" in r
          for r in rows if r["kind"] == "turn"))
PY
```

Expected: 150 in each of `fires`, `quiet`, `retries`; all unmarked; ids unique;
home path, account name and email all absent; both length fields present.

- [ ] **Step 10: Prove a rerun draws the same sample**

```bash
cp "$RETRO_HOME/labels.jsonl" "$RETRO_HOME/labels-first.jsonl"
python plugins/retro/bin/retro.py label --resample
python - <<'PY'
import json, os
from pathlib import Path
home = Path(os.environ["RETRO_HOME"])
ids = lambda p: [json.loads(l)["id"]
                 for l in (home / p).read_text(encoding="utf-8").splitlines()]
print("identical sample:", ids("labels-first.jsonl") == ids("labels.jsonl"))
PY
```

Expected: `identical sample: True`

- [ ] **Step 11: Commit the mechanism**

```bash
git add plugins/retro/bin/retro.py
git commit -F - <<'MSG'
feat: a label subcommand, so the thresholds can be argued from marks

tool_retries is 54% of the friction score and its rule is the crudest in the
file - same tool name and argument shape twice in a session. The turn
classifier is next. Neither had ever been checked against a human judgement.

label draws 150 turns the classifier fires on, 150 it does not, and 150 flagged
retries, writes them redacted to the work directory with the unredacted reply
and prior-turn lengths beside them, and reads the marked file back as precision,
recall and a sweep over both correction thresholds. Selection is by hash of each
candidate's identity, so a rerun on a grown corpus draws the same turns.

The sample is stratified, so the report weights each stratum back to corpus
scale rather than printing a rate that is an artefact of the sampling. Retry
recall is not estimable from flagged-only candidates and the report says so.

The file carries message text: the code refuses to write it inside a git
repository, and only aggregates from --report may go anywhere tracked. The
module docstring now says two places, not one.
MSG
```

- [ ] **Step 12: Hand the file to the operator and wait**

Tell them the path and that marking is per line. Do not paste any sample into
chat, this document, or a commit message. When it comes back, run
`python plugins/retro/bin/retro.py label --report` and keep only the printed
aggregates.

---

## Task 5: settle the numbers into the documents

**Files:**
- Modify: `docs/plans/2026-08-18-retro-measurement-fixes.md` — the Fix 2 section
  and the Verification table
- Modify: `docs/plans/2026-08-12-retro-design.md` — the metrics row list (74),
  the friction signals table (90), the "Open" note about unvalidated thresholds
  (171)
- Modify: `plugins/retro/skills/finding-friction-in-recent-sessions/SKILL.md` —
  the "Reading the signals" table (63-72)

**Interfaces:** consumes the aggregates printed by Task 4's report and the
measured values from Tasks 1-3.

- [ ] **Step 1: Update the spec's verification table with measured values**

Fill in, from the runs in Tasks 1-3 on the same day: prompts per session before
and after, counted user prompts, corrections, approvals, interrupts, the top-20
ranking result, and the rebuild time. Replace each "expect" with what happened.

- [ ] **Step 2: Record the labelling aggregates in the spec**

Precision and recall per class at the settled thresholds, retry precision, and
the sweep's best pair. Aggregates only — no sample turn, no reply text, no path.

- [ ] **Step 3: Update the earlier design document**

The metrics row list gains `approval_turns`. The friction signals table's
`correction_turns` line says questions and approvals are excluded. The "Open"
note that the two heuristics have never been validated is replaced by the
measured precision and recall.

- [ ] **Step 4: Update the friction skill's signal table**

Add a row for `approval_turns` reading as the process working rather than
friction, note on the `correction_turns` row that questions and approvals no
longer land there, and change the `subagent_transcripts` row to name the pack's
subagent spend line, which is where that count now appears.

- [ ] **Step 5: Check nothing else went stale**

```bash
grep -rn "subagent_transcripts\|correction_turns\|per session\|exactly one place" \
  --include=*.md --include=*.py . | grep -v docs/plans/2026-08-19
```

Every hit must describe the code as it now is. Fix what does not.

- [ ] **Step 6: Privacy scan before anything is proposed for merge**

```bash
plugins/core/bin/repo-privacy-audit
git diff --stat main...HEAD
git log main..HEAD -p | grep -n "labels.jsonl" | head
```

Expected: the audit's only hits are the known accepted commit-metadata ones; no
sample text, no work-directory path, and no labelling file content anywhere in
the diff or the messages.

- [ ] **Step 7: Commit**

```bash
git add docs/plans plugins/retro/skills
git commit -F - <<'MSG'
docs: record what fix 2 changed and what the labels measured

Verification numbers replace the expectations in the spec, the design document
gains the approval counter and loses the note that the two heuristics were
never validated, and the friction skill's signal table now describes the split
signal and the pack's subagent spend line.

Aggregates only. No sampled turn appears here.
MSG
```

---

## Verification summary

| Check | Before | After |
|---|---|---|
| printed prompts per session | 26.7 | 9.0 |
| trends table population | all transcripts | main sessions, with subagent spend on its own line |
| moments section across 2a | — | byte-identical |
| main corrections | 988 | 608 minus approvals |
| approvals recorded | none | counted, not scored |
| questions or approvals quoted as moments | n/a | 0 |
| main user prompts across 2b | 3,877 | 3,877 |
| subagent user prompts across 2b | 7,632 | 1,584 |
| main interrupts across 2b | 163 | 154 |
| main rows whose score moves across 2b | — | 6 of 431 |
| top-20 friction order across 2b | — | identical |
| full rebuild | 5.2 s | no material regression |
| labelling file inside a repository | n/a | refused, exit 2 |
| labelling file rerun | n/a | same sample |
| turn classifier precision and recall | never measured | recorded in the spec |
| `tool_retries` precision | never measured | recorded in the spec |

## Questions for the operator

1. Which exact phrases count as an approval reply — the whole-reply list for `APPROVAL_PHRASES`, given that the spec offers "yes", "sure" and "go for it" as examples rather than as the list?
2. How should the labelling file be marked — filling a `label` field on each line of a JSONL file under the work directory, or a different shape you would rather mark by hand?
3. Confirm that the question / approval / correction split applies only to short replies after a substantial assistant turn, leaving long replies unclassified as they are today?
4. Confirm that `label` should sample main-session transcripts only, excluding the subagent transcripts that no ranking reads?
