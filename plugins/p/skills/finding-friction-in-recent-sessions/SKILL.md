---
name: finding-friction-in-recent-sessions
description: Use when asked what has been going wrong in how we work, for a weekly or periodic retrospective, or when the same annoyance keeps recurring across sessions and should become a rule instead. Reads measured session history, not memory.
---

# Finding friction in recent sessions

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check finding-friction-in-recent-sessions`.
If it exits 1 or 2, stop and report its output.

## Overview

Session transcripts record every time a human had to intervene — correct a turn,
interrupt it, flip permission mode, sit through a retried command. Those events
are countable. This skill turns a window of them into at most three proposals,
each naming a specific edit to a specific file.

The failure mode this replaces is retrospection from memory: recalling the
annoyances that happened to be recent or loud, and missing the one that cost the
most turns. What you remember and what the numbers say are routinely different.

## The procedure

**1. Measure.** Incremental: only transcripts whose size or mtime moved are
re-read, so a routine run costs a fraction of a first build.

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/retro.py" extract
"${CLAUDE_PLUGIN_ROOT}/bin/retro.py" pack --days 7
```

`extract` exits 1 when a transcript would not read. It still writes the ledger,
and retries that file on the next run.

`pack` prints the path of one markdown file: trends for the window against the
previous window, then the highest-friction sessions with the actual moments
quoted.

**2. Read the pack. Only the pack.** Do not open transcripts. The corpus is most
of a gigabyte, and the pack is already redacted — transcripts are not.

**3. Read trends as rates, not totals.** Every raw total tracks how much work
happened. A signal that rose while sessions stayed flat is real; a signal that
fell 20% in a week when turns also fell 20% is nothing. The pack prints
per-session rates under the table for this reason.

**4. Rank by consequence, not by count.** The pack orders sessions by a friction
score that weights the signals meaning a human had to intervene — corrections and
interrupts heaviest, then permission-mode changes and retries, then errors. Read
in that order, and within it prefer the friction that cost the most turns over
the one that occurred most often.

**5. Write at most three proposals.** Each one has four parts:

- **What fought us** — one line, stated as consequence.
- **Evidence** — date, project, and the quoted moment from the pack.
- **The edit** — a named file and the text to add or change. Concrete enough to
  apply without a follow-up question.
- **The metric** — which counter should move if this works, and its value now.

**A proposal without a named file and proposed text is not a proposal.** Cut it.
"Be more careful about X" survives no contact with a future session.

**6. Deliver as a file and stop.** Never edit CLAUDE.md, a skill, a hook, or a
permission rule from inside this skill. Propose; wait to be asked.

## Corrections are candidates, and judging them is your job

`correction_candidates` is deliberately over-inclusive. Measured against 300
turns marked by hand: it catches 93% of real corrections and about 60% of what
it flags is a correction. That trade is on purpose. Whether a reply is a
correction is a judgment about intent, and no wording rule got past 0.63
precision in testing — the ones it missed were corrections phrased as questions,
which is exactly the shape a rule cannot see and you can.

So do not report a candidate count as a correction count, and do not put it in a
proposal as though it were measured fact. Read the quoted moments, decide which
ones are really someone being told they got it wrong, and say how many you kept
out of how many you were given. A count you have not read is not evidence.

## Reading the signals

| Signal rising | Usually means |
|---|---|
| `repeat_calls` | the same call made twice with identical input — duplicated work, not necessarily a retry |
| `correction_candidates` | a standing instruction is missing, or an existing one is not being followed |
| `interrupts` | turns are going wrong early — usually scope or approach, not detail |
| `queued_prompts` | you were typing ahead because a turn was taking too long |
| `tool_errors` | a tool is being called wrong, repeatedly — usually a missing note about its interface |
| `subagent_transcripts` with flat output | fan-out that is not paying for itself |
| `permission_mode_changes` | rare by nature — expect long stretches of zero. Any nonzero week is worth a look; do not expect a trend line |

Two of these carry a known measurement caveat. `skill_runs` counts contiguous
stretches of the same skill being active, which is not the same as the number of
times it was deliberately invoked, and the field it derives from is absent from
transcripts written by older CLI versions. `tool_errors` counts records carrying
a failure marker, which includes failures that were expected and handled. On a
mixed corpus, `tool_errors`, `queued_prompts`, and `permission_mode_changes` are
not observable for Codex rows; the pack marks those lines with the observable
share, and per-session rates divide by it.

Every metric's precise definition, and the measurement that settled it, lives in
`docs/plans/2026-08-12-retro-design.md`. Read it before arguing with a number.

## Common mistakes

**Proposing a rule for something that happened once.** One occurrence is an
anecdote. The pack shows counts; use them.

**Writing the proposal as a description of the problem.** The deliverable is the
edit. If the proposal does not contain text that could be pasted into a file, it
is not finished.

**Quoting the moment without the turn before it.** The pack includes the
assistant text immediately preceding each correction because that is what was
actually wrong. The correction alone reads as a complaint.

**Letting the list grow past three.** A retrospective that returns nine findings
gets read once and actioned never. Three that get applied beat nine that do not.

**Treating a fall in a counter as improvement without checking session count.**
See step 3.

## Red flags

- "I remember being annoyed by..." — check the pack, the counts disagree more often than not
- "This one is hard to make concrete" — then it is not ready to propose
- "I'll just fix this one while I'm here" — this skill proposes; it does not apply
- Opening a transcript directly

All of these mean: go back to the pack and let the counts pick the finding.
