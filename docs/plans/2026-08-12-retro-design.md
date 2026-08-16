# retro — workflow retrospectives from measured session history

**Status:** built, unreviewed
**Home:** `plugins/retro/` in polstools

## What it does

Turns Claude Code session history into three retrospective lenses: what friction
is recurring, which standing rules and skills are actually doing anything, and
what tooling would close a named seam. Each lens is a skill, invoked when wanted.

**Nothing is scheduled.** The skills run when asked.

## The constraint

A previous attempt at this grew into a compiled binary with its own database,
a migration chain, a findings lifecycle, and skill generation. Maintaining the
miner became the project, and it stopped running: its hooks were never installed,
its archived sessions went stale, and the findings it extracted sat unread
in the state they were created in.

So: no database, no daemon, no compiled binary, no findings state machine. One
Python script over data that already exists, and three skills. Changing what a
retrospective looks for means editing a markdown file.

The failure to design against is not "the miner was too slow." It is "nothing
closed the loop."

## Components

### `plugins/retro/bin/retro.py` — Python 3, stdlib only

Three subcommands:

- `extract` — walk session transcripts, write one metrics row per transcript.
  Counts only, no message text. Incremental via a state file keyed on size and
  mtime, so a live session that has grown is re-measured and everything else is
  skipped.
- `pack --days N` — build the evidence pack a model reads: trends for the window
  against the prior window, then the highest-friction sessions with their
  moments quoted. Every quote passes through the redactor first.
- `skills --days N` — split installed skills into fired and never-fired.

Every field access is guarded. Transcript shape varies by CLI version, and a
`KeyError` partway through a 900 MB corpus loses the whole run.

### Three skills

| Skill | Lens |
|---|---|
| `finding-friction-in-recent-sessions` | what fought us, and the specific edit that would stop it |
| `auditing-workflow-rules-against-behavior` | which rules are dead letters, which skills never fire |
| `scouting-tools-for-open-frictions` | tooling searched by seam, never by novelty |

All three propose. None of them edit configuration.

## Metrics row

One JSON object per transcript:

```
transcript, is_subagent, session_id, project, git_branch, cc_version, date,
turns, duration_s, tokens_in, tokens_out, cache_read, skills_used[], abandoned,
user_prompts, tool_calls, tool_errors, tool_retries, correction_turns,
interrupts, permission_mode_changes, queue_operations, sidechain_turns,
skill_invocations
```

Rows are keyed by transcript path, not session id. Subagent transcripts live
under `<session>/subagents/` and carry the **parent** session's id — keying by
session id let them overwrite the parent's row and collapsed 1,715 transcripts
into 352. They are tagged `is_subagent` and excluded from session counts, so
per-session rates are not deflated by fan-out.

## Friction signals

Every signal was confirmed present in real transcripts.

| Signal | Source | Reads as |
|---|---|---|
| `permission_mode_changes` | `permission-mode` records | the permission config does not match the work |
| `queue_operations` | `queue-operation` records | waiting, or overriding mid-turn |
| `tool_errors` | `toolUseResult` error shape | a loop that had to be sat through |
| `tool_retries` | same tool + normalized input signature ≥2× | something rediscovered every session |
| `correction_turns` | short user prompt after a long assistant turn | a standing instruction is missing or ignored |
| `interrupts` | interrupt marker in user records | the turn went wrong early |
| `skill_invocations`, `skills_used` | `attributionSkill` | which skills actually fire |
| `sidechain_turns` | `isSidechain` | subagent spend |
| `tokens_*` | `message.usage` | what the friction cost |
| `abandoned` | ends on an assistant turn, quiet ≥15 min | ended without resolution |

The retry signature normalizes whitespace and numeric literals before hashing,
so a retried command with a tweaked number still matches its predecessor.

## Redaction

Anything quoted into a pack passes through `redact()` first: home directory
prefix, account username, email addresses, IPv4 addresses, and long
credential-shaped tokens. Redaction happens at write time — a pack on disk must
already be safe, because redacting at read time is too late.

The work directory (`~/.retro/`, overridable via `RETRO_HOME`) sits outside every
git repository. Harvested session history never becomes a tracked file.

## Non-goals

No scheduler, no database, no dashboard, no skill auto-generation, no training
corpus, no auto-apply.

## Verification

Measured on the live corpus, 2026-08-16:

| Check | Result |
|---|---|
| Full rebuild over 1,737 transcripts | 29.6 s, 1,715 rows, 22 unreadable |
| Immediate incremental re-run | 0.17 s, 3 changed, 1,735 unchanged |
| 7-day pack | built; 81 sessions vs 78 prior |
| Redactor over the produced pack | username, home path, email, IPv4 all absent |
| `skills --days 30` | 25 fired, 61 of 86 installed never fired |
| Manifests | `marketplace.json` and all three `plugin.json` parse |
| Privacy scan over the worktree | 0 hits |

Open: the 22 unreadable transcripts are counted but not characterized.
