---
name: finding-what-a-change-made-false
description: Use when a change set is landing or has just landed and the repo has docs that describe how the code works — roadmaps, handoffs, plans, config examples, header comments. Also use when a doc turns out to describe machinery that no longer exists, or when picking up work and finding the notes do not match the code.
---

# Finding what a change made false

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check finding-what-a-change-made-false`.
If it exits 1 or 2, stop and report its output.

## Overview

The question is **"what did this change make false"**, not "review the docs".

They produce different results. "Review the docs" is an open-ended reading task
with no failure condition, so it comes back clean. "What did this change make
false" is answerable, and the answer is usually a short list you can check.

## When to use

- A change set is about to land, or just landed
- You are writing the final "update the docs" task of a plan — **stop, this is
  that task, and doing it last is why it fails**
- A doc told you to call something that does not exist
- The notes you picked up describe a state the machine is not in

Do not use it as a general end-of-session sweep. That is the failure mode, not
the fix.

## The method

1. **List what changed.** `git diff --stat <base>..HEAD`. Symbols deleted or
   renamed, files moved, defaults changed, behaviour that flipped.
2. **For each one, grep the docs for it by name.** Deleted `foo::bar()`? Grep
   every `.md` for `bar`. Moved a path? Grep for the old path. This is the step
   that finds things; the reading comes after.
3. **Read what the grep found, and classify it.** Three outcomes, and only one
   is a defect:
   - historical record — "it used to be X" — correct, leave it
   - present-tense claim that is now false — fix it
   - **an instruction for future work** — fix it first, it is the worst kind
4. **Check the docs that describe machine state**, not just code. A handoff
   saying "the live binary is still the old one" stops being true the moment
   someone builds.
5. **Check what is NOT in the queue.** A plan, finding, or decision that exists
   only in scratch, a ledger, or prose is invisible. Grep the roadmap for each
   plan filename.
6. **Grep every doc — not just the files the diff touched — for words that
   pin text to a moment:** `today`, `current` (which catches `currently`),
   `still`, `not yet`, `does not exist`. Classify each hit with step 3: a
   `today` inside a dated entry is history and stays. A run once cleared a
   spec saying "popd is not even in the current cd-name list" — popd had
   been in that list for six commits; only a later run caught it. The word
   list grows by evidence, like every row here — never speculatively.
7. **A measurement whose subject changed after it was taken is re-TAKEN, not
   re-read.** Reading cannot tell you whether any of 20,194 recorded
   decisions changed; re-running the measurement can. Record the re-run's
   result beside the original — even "0 rows changed" is a fact, because it
   upgrades the number from "true at commit X" to "true at head". If the
   re-take is not cheap, stamp the number with the commit it was true at
   instead of leaving it reading as current.

## What to look for

Every row cost a real miss.

| Look for | Because |
|---|---|
| A deleted symbol named in a doc | Someone follows it into a function that is gone |
| The same, in an unexecuted plan | Worse — it is an instruction, and nobody has run it yet to find out |
| Counts, percentages, measurements | Two review rounds and they are stale |
| A doc describing machine state | True when written, false after the next build |
| A plan not named in the roadmap | It does not exist; nobody reading the queue will see it |
| A finding kept only in scratch | The scratch gets deleted and it goes with it |
| A stated guarantee | "There is deliberately no setting for this" was false — the setting was a list in the operator's own config |
| A semantics list the change EXTENDED | True sentence by sentence, false as an enumeration — someone implementing from it re-introduces the case the change just added |
| A line-number reference (`engine.rs:608`) | The file grew 364 lines and the number pointed at the wrong function. The fix is a NAME (`decide_file`'s protected loop), never a fresh number — a number goes stale again the same way |

## Common mistakes

**Doing it last.** The change set that made the docs false is the one that
should have fixed them. A task called "update the docs" at the end of a plan
runs after six other tasks have already made them wrong, and it will miss
things — that is exactly how the list above was collected.

**Grepping only the files you expect.** Check every tracked doc, plus config
examples, header comments, and scripts. The worst finds are in the ones nobody
thinks of as documentation.

**Treating historical text as stale.** "It used to be compiled into the binary"
is correct and must stay. Only present-tense claims and future instructions are
defects. Fixing history erases the record of why something changed.

**Stopping at the code.** Half of what goes stale is about the machine — which
binary is live, which files exist, where config lives.

## A run's result is stamped to a commit and a machine

A clean bill from this skill covers `<base>..<head as of the run>` and
nothing after. Commits that land later — a fix wave from a final review, a
"small" doc correction — re-open the question for exactly those commits.
Re-running scoped to just the new commits is cheap; assuming the old clean
bill stretches over them is how a spec shipped describing semantics its own
fix wave had already extended.

Machine state re-opens a run the same way. A doc saying "the `[tools]`
section now carries the blanket allows" went stale within hours with no
commit at all — the config moved. The stamp covers the machine, too.

## After every run: did this run change THIS file?

Answer with evidence from the run, not in the abstract:

1. Did a miss survive to a later run, a reviewer, or the operator? Add a
   row, quoting it.
2. Was a check done by reading that a grep or a re-run could have made
   mechanical? Move it into the method in that form.
3. Did any wording mislead the run? Fix it.

If all three are no, say so and change nothing. Every row here cost a real
miss; a row without one is padding, and padding is how checklists stop
being read.

## Red flags

- "The docs are probably fine"
- "I'll do a docs pass at the end"
- "That file is just notes"
- "It still mostly describes it"

All of these mean: list what changed, and grep for each thing by name.
