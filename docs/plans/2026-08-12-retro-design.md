# retro — scheduled workflow retrospectives

**Status:** spec, awaiting review
**Date:** 2026-08-12
**Home:** `plugins/retro/` in polstools

## What it does

Reads Claude Code session history on a schedule and produces a report naming the
frictions in how we work together, each with evidence and one concrete proposed
fix. Daily is mechanical and silent. Weekly and monthly cost one model call each
and end in a numbered list of proposals.

## The constraint

A previous attempt at this grew into a compiled binary with its own database,
a migration chain, a findings lifecycle, and skill generation. Maintaining the
miner became the project, and it stopped running: its hooks were never installed,
its archived sessions went stale, and the findings it extracted sat unread
in the state they were created in.

So: **no database, no daemon, no compiled binary, no findings state machine.** Two
Python scripts over data that already exists, three prompt files, and a scheduled
task. Changing what the retro looks for must mean editing a markdown file.

The failure to design against is not "the miner was too slow." It is "nothing
closed the loop." Every part of this spec that looks like overhead — the proposal
ledger, the staleness alarm, the metric attached to each accepted proposal —
exists because that loop stayed open.

## Decisions locked

| Question | Answer |
|---|---|
| Home | new `retro` plugin in polstools |
| Delivery | file in a review inbox |
| Autonomy | proposes only; never edits config without being asked |
| Backfill | all available history on first run |

## Components

### 1. `plugins/retro/bin/retro-extract` (Python 3, stdlib only)

Walks the session transcript directory and the prompt history file, emits one
metrics row per session to the metrics ledger. Counts only — no message text.

Incremental: a state file records each processed transcript by path, size, and
mtime. Re-running is cheap; the first run is the backfill. A transcript that has
grown since last run is re-processed and its row replaced (sessions are appended
to while live).

Must tolerate: truncated final lines, mixed encodings, records whose `message.content`
is a string rather than a list, and files locked by a running session.

### 2. `plugins/retro/bin/retro-pack` (Python 3, stdlib only)

Builds the evidence pack a model reads for one window (`--days N`). Two parts:

- **Trends** — per-metric totals for the window, the prior window, and the delta.
- **Moments** — up to 20 quoted excerpts around the highest-scoring friction
  events, each with session id, timestamp, and project. Every excerpt passes
  through the redactor before it is written.

Output is one markdown file under the work directory. Nothing else reads
transcripts; the model only ever sees the pack.

### 3. Commands — `plugins/retro/commands/{daily,weekly,monthly}.md`

The lenses. Each is a prompt; each runs both interactively (`/retro:weekly`) and
headlessly from the scheduler via the same command.

### 4. `plugins/retro/bin/retro-install` (POSIX sh, matching the other plugin bins)

Registers and removes the three Windows scheduled tasks. Discovers the Python
interpreter and the Claude CLI at install time and writes their paths into the
task definitions rather than assuming a PATH the scheduler will not have.

## Metrics row

One JSON object per session, appended to the metrics ledger:

```
session_id, project, git_branch, date, cc_version,
turns, duration_s, tokens_in, tokens_out, cache_read,
permission_mode_changes, queue_operations, sidechain_turns,
tool_calls, tool_errors, tool_retries,
user_prompts, correction_turns, interrupts,
skill_invocations, skills_used[], abandoned
```

No message content. No file paths. This ledger is the trend substrate — it is what
lets a monthly report say whether last month's fix worked, rather than re-litigating
the same friction forever.

## Friction signals and where they come from

Every signal below was confirmed present in a real transcript sampled 2026-08-12.

| Signal | Source | Reads as |
|---|---|---|
| `permission_mode_changes` | `permission-mode` records | the harness fought you — 104 in one sampled session |
| `queue_operations` | `queue-operation` records | you were waiting, or overriding mid-turn |
| `tool_errors` | `toolUseResult` error shape | a loop you sat through |
| `tool_retries` | same tool + near-identical input ≥2× in a session | rediscovery, or a rule that should exist |
| `correction_turns` | short user prompt directly after a long assistant turn | you had to steer |
| `interrupts` | interrupt marker in user records | the turn was going wrong |
| `skill_invocations` / `skills_used` | `attributionSkill` | which skills actually fire |
| `sidechain_turns` | `isSidechain` | subagent spend |
| `tokens_*` | `message.usage` | what the friction cost |
| `abandoned` | last record is an assistant turn | session died without resolution |

The prompt history file is the cheap half: every prompt typed, with timestamp and
project, at roughly a five-hundredth the size of the transcript corpus. Correction
and interrupt detection run off it.

`tool_retries` needs an input signature that is stable across trivial edits —
normalize whitespace and numeric literals before hashing, or the metric only
catches literal duplicates and misses the case that matters.

## Redaction

Anything quoted into a pack or a report passes through a single redactor first.
It replaces: the user's home directory prefix, the account username wherever it
appears, email addresses, LAN addresses and hostnames, and any value matching a
credential shape. Redaction happens at write time in `retro-pack`, not at read
time in the model — a pack file on disk must already be safe.

The work directory (`~/.retro/`) and the report inbox live outside every git
repository and are never added to one. Harvested session history does not become
a tracked file.

## The three lenses

**Daily** — no model call. Runs `retro-extract`, appends the day's totals, and
exits silently. Writes a report only if a threshold trips or if the newest
transcript processed is older than 48 hours, which is the alarm for this system
having quietly died the way the last one did.

**Weekly** — `retro-pack --days 7`, then one model call. Question: *what fought us
this week?* Output: at most three proposals, each with an evidence pointer
(session, timestamp) and one concrete edit — a CLAUDE.md clause, a permission rule, a
skill fix, a hook. A proposal without a specific edit is not a proposal.

**Monthly** — `retro-pack --days 30`, then one model call with web access. Two
questions:

1. *Which of our own rules are dead letters?* Cross-reference CLAUDE.md clauses
   and installed skills against observed behavior. Name the rules that have never
   changed a session and the skills that never fire.
2. *What exists now that addresses our top open frictions?* Search seeded by the
   open friction list, not by novelty. Each candidate carries the seam it closes,
   its install cost, and a kill criterion. Not adopted within 30 days → dropped.

## Proposal ledger and the measurement loop

Proposals are appended to a ledger with status `open`. When one is accepted, it
gets a metric name and the current value of that metric recorded as its baseline.
The next monthly report reads the ledger and reports the delta for every accepted
proposal.

This is the part that makes it a loop rather than a suggestion box. Without it,
rules accumulate and nobody ever learns which ones worked.

Nothing in this system edits configuration. Reports propose; a normal session
applies, when asked.

## Delivery

Reports land in the review inbox as numbered files (`01-`, `02-`, …) defining the
set's order; the numbers in the file names are the numbers used when talking about
them. Before a new set is delivered, the previous set moves to `_archive/<date>/`.
Archive, never delete.

## Scheduling

Windows Task Scheduler. Session-scoped timers do not survive a closed terminal,
and a cloud runner cannot read a local transcript corpus — neither is a candidate.

| Task | When | Cost |
|---|---|---|
| daily | 06:00 | seconds, no model call |
| weekly | Sunday 07:00 | one model call |
| monthly | 1st, 08:00 | one model call with web access |

Each task invokes the Claude CLI in headless print mode with the corresponding
command. A missed run (machine asleep) is caught by the next run — the extractor
is incremental over all unprocessed history, so no window is lost.

## Non-goals

No database, no dashboard, no web UI, no skill auto-generation, no training corpus,
no per-project report splits, no auto-apply.

## Verification

- `retro-extract` over the full corpus completes and produces one row per
  transcript, with a stated wall-clock number.
- A second immediate run processes zero files.
- A pack built from a window containing a known-noisy session shows that session's
  permission-mode count.
- The redactor, run over a fixture containing a home path, a username, an email,
  and a credential-shaped value, leaks none of them.
- A dry-run of each scheduled task produces its report file.
