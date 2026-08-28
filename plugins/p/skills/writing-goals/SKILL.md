---
name: writing-goals
description: Use when constructing or reviewing a Claude Code /goal condition — before leaving a session to run unattended, at plan-execution start, when the operator asks for a goal to be set, or when an active goal churns without stopping or stops early.
---

# Writing Goals

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check writing-goals`. If it exits
1 or 2, stop and report its output.

## Overview

A /goal condition is judged after every turn by a small evaluator model that reads only the conversation transcript — it cannot run commands or read files. A well-formed condition is a compact contract about what the transcript must show. Build it from the five slots below, in order; keep the whole condition under ~120 words.

## The five slots

| Slot | States | Example |
|---|---|---|
| EVIDENCE | Named command(s) run after the last change, with the literal summary line that must appear | "`cargo test --release` run after the final edit shows `0 failed`" |
| ARTIFACT | What exists where when done | "HANDOFF.md updated with the measured replay delta, committed" |
| CONSTRAINTS | What must still hold; block the cheap outs | "test not skipped, weakened, or retry-wrapped; no files modified outside the worktree" |
| PARKED | The legitimate other exit | "or parked: the exact failing command and its error shown, two distinct fix attempts, one line naming the blocker" |
| BOUNDS (required) | Turn cap, plus a stall cap when progress is countable | "or stop after 25 turns, or after 3 consecutive turns that add no newly-passing test" |

BOUNDS is never omitted. In baseline testing, agents reliably produced evidence-rich conditions with no bounds at all — and an unbounded goal is the documented way to burn a night unattended. Turn/time/token counters are totals since the goal was set and reset on `--resume`.

## Where the condition comes from

1. A written plan exists → promote its Verification section into EVIDENCE + ARTIFACT verbatim; don't invent new gates.
2. The operator gave a vague target ("sort out the flaky test") → convert it to something countable from context ("passes 25 consecutive runs").
3. A ratchet needs a threshold the context doesn't contain ("reduce prompt noise") → ask the operator for the number; never invent it.

## Sizing

One goal = one finish line. If two conditions could complete at different times, they are two goals — set the one for this session. Drop slots with nothing to say; don't pad. Name the summary line that must appear, not "paste the full output" — long pasted-output demands bloat every evaluation.

## Required-gate recovery

A required gate's evidence means one successful PASS over the final required
inputs, not one attempted execution. If an already-authorized required gate
fails, automatically diagnose, make an in-scope fix, and rerun it. Ordinary
fix-and-rerun recovery already required by the finish line needs no new
operator authorization. A failed attempt does not consume wording such as
"exactly once"; write that constraint as "exactly one final-input PASS" when
the distinction matters.

Do not mechanically rerun identical failing inputs. Rerun after a relevant
fix, or when the failure is demonstrably transient and the workflow already
authorizes retrying it. Keep the goal's turn and stall bounds in force, and
stop at any genuine permission, scope, privacy, or external-mutation boundary.
Never repeat a matching successful PASS merely to recover output; use retained
receipts or logs.

## Common mistakes

1. No bounds — always end with "or stop after N turns".
2. Vibe words — clean/better/robust have no yes/no; name the command whose output flips.
3. Conditions the transcript cannot show — "CI is green" fails when CI runs elsewhere; use what the session itself runs.
4. Multi-gate walls — the evaluator judges the whole condition every turn; compress.
5. Missing CONSTRAINTS — a goal without them rewards deleting the test that blocks it.
6. Treating the first failed attempt as "exactly once" — recover automatically until one final-input PASS or a stated bound decides the outcome.

## Mechanics

Evaluator is transcript-only, no tools. `/goal clear` removes an active goal (aliases: stop, off, reset, cancel); `/clear` also removes it. An active goal survives `--resume` with counters reset. Never leave a goal unbounded when nobody is watching.
