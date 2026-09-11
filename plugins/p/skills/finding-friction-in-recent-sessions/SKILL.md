---
name: finding-friction-in-recent-sessions
description: Use when asked what has been going wrong in how we work, for a weekly or periodic retrospective, or when the same annoyance keeps recurring across sessions and should become a rule instead. Reads measured session history, not memory.
---

# Finding friction in recent sessions

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check finding-friction-in-recent-sessions`.
If it exits 1 or 2, stop and report its output.

## Overview

This skill turns measured workflow evidence into at most three concrete
proposals. Transcripts observe only part of the work; missing observations do
not establish that a problem never occurred or that a rule was followed.

## Coverage before interpretation

The `retro.py` commands below read Claude history only. They do not switch to
the active harness. For work spanning harnesses, use the existing extraction
and coverage report described in `<plugin-root>/EVALUATION.md`; its registered
adapters currently cover Claude and Codex. Antigravity is not measured by these
commands. A source location alone does not establish parser support.

Report observed sources, main and child populations, exclusions, snapshot or
window bounds, and unavailable signals before drawing conclusions. Preserve
separate populations when inclusion rules differ. Use an isolated external
`RETRO_HOME` for an audit; keep real evidence outside every repository.

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

**3. Read trends as rates, not totals.** Compare each signal over its eligible
population and check task mix and source coverage. The pack reports per-session
rates; a change in these rates alone does not establish a behavioral cause.

**4. Rank by consequence, not by count.** Current ranking uses tool-error
markers and permission-mode changes. Legacy correction and interrupt labels
are sampling aids and cannot support decisions until their rubric is validated.
Repeated calls are reported but do not affect ranking. Diagnose consequential
cases with actual context; an expected error or a legitimate repeat is not
necessarily a failure of the workflow.

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

`correction_candidates` is deliberately over-inclusive. Read the displayed
moments and report how many you retained, but do not call a selected reading
sample a population rate or classifier precision. Current legacy labels remain
restricted to sampling and scorer validation by the rubric catalogue. A manual
reading does not silently promote the rubric into decision support.

## Reading the signals

| Signal rising | Question to investigate |
|---|---|
| `repeat_calls` | the same call made twice with identical input — duplicated work, not necessarily a retry |
| `correction_candidates` | is the moment an actual correction, with an observable applicable instruction? |
| `interrupts` | was the interruption corrective, logistical, or unrelated to agent behavior? |
| `queued_prompts` | was queuing normal task staging or a response to delay? |
| `tool_errors` | was the failure expected, caused by the environment, or an avoidable call error? |
| `subagent_transcripts` with flat output | did parent use and task quality justify the delegated work? |
| `permission_mode_changes` | rare by nature — expect long stretches of zero. Any nonzero week is worth a look; do not expect a trend line |

Two of these carry a known measurement caveat. `skill_runs` counts contiguous
stretches of the same skill being active, which is not the same as the number of
times it was deliberately invoked, and the field it derives from is absent from
transcripts written by older CLI versions. `tool_errors` counts records carrying
a failure marker, which includes failures that were expected and handled.

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

All of these mean: return to the evidence and check what it can support.
