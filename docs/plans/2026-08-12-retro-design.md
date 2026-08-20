# retro — workflow retrospectives from measured session history

**Status:** built; reviewed and re-measured 2026-08-20
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

Seven subcommands:

- `extract` — walk session transcripts, write one metrics row per transcript.
  Counts only, no message text. Incremental via a state file keyed on size and
  mtime, so a live session that has grown is re-measured and everything else is
  skipped.
- `pack --days N` — build the evidence pack a model reads: trends for the window
  against the prior window, then the highest-friction sessions with their
  moments quoted. Every quote passes through the redactor first.
- `skills --days N` — which installed skills fire, and which never do.
- `subagents --days N` — mechanical failures in subagent transcripts, each share
  divided by the population the signal could have occurred in.
- `label` — sample turns for hand marking, then report precision, recall and a
  threshold sweep from the marked file. This is how the classifier's constants
  stopped being guesses.
- `rules` — whether the standing instructions are under version control at all,
  and whether their edits are being committed as they are made. Built because
  `effect` needs a date and a rule change with no commit has none.
- `effect --since DATE` — the same metrics before and after a date, so a rule or
  skill edit can be checked against what followed it. Reports both per session
  and per hundred turns, because the first moves whenever sessions change length
  and on its own it will tell you an edit worked when nothing did.

Every field access is guarded. Transcript shape varies by CLI version, and a
`KeyError` partway through a 900 MB corpus loses the whole run.

`extract` fans the per-transcript work across a thread pool. Measuring a
transcript shares no state and is dominated by reading it off disk, so the pool
turns most of the wall clock into concurrent I/O — a full rebuild went from
29.6 s to 5.9 s.

Exit codes match the sibling scripts in `plugins/core/bin`: `0` ran clean and
flagged nothing, `1` ran clean and flagged something (friction in the window,
dormant skills), `2` could not run. Without this the subcommands cannot gate
anything without parsing their stdout.

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
duration_s, tokens_in, tokens_out, cache_read, skills_used[],
turns, user_prompts, tool_calls, tool_errors, repeat_calls, correction_candidates,
interrupts, permission_mode_changes, queued_prompts, skill_runs
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
| `correction_candidates` | a reply carrying a corrective signal, or a short reply after a long turn | a standing instruction is missing or ignored. Over-inclusive on purpose: 0.93 recall, 0.60 precision against 144 hand-marked turns |
| `interrupts` | interrupt marker in user records | the turn went wrong early |
| `repeat_calls` | same tool, byte-identical input, twice | duplicated work. Unscored: the normalising version of this was 42% of the friction score and 83% of what it flagged had different inputs |
| `tool_errors` | `is_error` on a tool_result block, or an error-prefixed string result | a tool called wrong, repeatedly |
| `queued_prompts` | `queue-operation` records of subtype `enqueue` | typing ahead because a turn ran long |
| `permission_mode_changes` | transitions between consecutive `permission-mode` records | the permission config did not match the work |
| `skill_runs`, `skills_used` | contiguous runs of `attributionSkill` | which skills actually fire |
| `tokens_*` | `message.usage` | what the friction cost |

The call signature is an exact digest of the tool input. It used to normalize
whitespace and numeric literals first, on the theory that a retry is the same
command with a tweaked number — recounted 2026-08-20, that theory was wrong in
1,157 of the 1,387 repeats it flagged, most of them one file read at successive
offsets. Truncating the hashed prefix also let two long writes to different
paths collide.

### Metric definitions that a first pass gets wrong

Each of these was measured against the corpus, not reasoned about. They are
recorded because the wrong version of each is the obvious one.

- **`permission-mode` records are a repeated snapshot of the current mode, not a
  change event.** 5,645 records across the corpus contain 68 actual transitions —
  an 83× overcount if records are counted directly. One session holds 313 records
  and zero changes.
- **`isSidechain` is never true inside a session transcript.** Subagent turns
  live in separate files under `subagents/`, where it is true 68,470 times.
  Reading the field inside a session file measures nothing, so no
  `sidechain_turns` metric is emitted; the `is_subagent` tag carries fan-out.
- **Tool failures are marked on the `tool_result` content block, not on
  `toolUseResult`.** `is_error` on the block appears 519 times in a 373-session
  sample; a `toolUseResult.error` key appears once in the entire corpus.
- **`attributionSkill` is stamped on every assistant record while a skill is
  active**, so counting records counts turns. 4,736 records collapse to 2,476
  contiguous runs. The field is also absent from every record written by CLI
  version 2.1.170, giving the metric a version floor.
- **`queue-operation` kinds are paired** — enqueue 2,087, dequeue 1,275, remove
  777, popAll 28. A single total counts one queued prompt up to three times.
- **No abandonment metric is emitted.** The obvious definition, a transcript
  ending on an assistant turn, matches 0.3% of session files literally and 59%
  once trailing bookkeeping records are ignored — the latter being just the shape
  of a session that ended after a reply. Neither separates abandoned from
  finished.
- **The prompt-history file is not a usable second source.** It holds no
  assistant-side data, so corrections and interrupts are not computable from it;
  it covers 58 of 352 sessions (interactive entrypoints only); and for sessions
  present in both, its prompt count disagrees with the transcript's in 83% of
  cases. All metrics derive from transcripts alone.

## Redaction

Anything quoted into a pack passes through `redact()` first: home directory
prefix, account username, email addresses, IPv4 and MAC addresses, and long
credential-shaped tokens. Redaction happens at write time — a pack on disk must
already be safe, because redacting at read time is too late.

**Correction, 2026-08-20.** The home-directory half of that was not true when it
was written. The account-name rule ran first and rewrote the name *inside* the
home path, after which no home-path rule could match its own text: identity was
stripped and the whole directory tree below home survived into packs. The
verification row below recorded a pass because it searched for the account name,
which was genuinely gone — it never checked that the path had collapsed. Fixed
by moving the account rule last; the ordering is now load-bearing and commented
as such in the code.

The work directory (`~/.retro/`, overridable via `RETRO_HOME`) sits outside every
git repository. Harvested session history never becomes a tracked file.

## Non-goals

No scheduler, no database, no dashboard, no skill auto-generation, no training
corpus, no auto-apply.

## Verification

Measured on the live corpus, 2026-08-16, after the metric corrections above:

| Check | Result |
|---|---|
| Full rebuild over 1,800 transcripts | 5.9 s, 1,778 rows, 22 files that hold no conversation |
| Immediate incremental re-run | 0.16 s |
| Row split | 386 session rows, 1,392 subagent rows |
| Ledger totals vs an independent probe | `permission_mode_changes` 68/68 — agree |
| Metrics after the cleanup refactor | unchanged: permission-mode still exactly 68, other counters moved only by newly added sessions |
| 7-day pack | built |
| Redactor over the produced pack | username, home path, email, IPv4, MAC all absent |
| `skills --days 30` | 25 fired, 61 of 86 installed never fired |
| Exit codes | `pack` and `skills` return 1 when they flag something, 0 when clean |
| Manifests | `marketplace.json` and all three `plugin.json` parse |
| Privacy scan over the worktree | 0 hits |

Open:

- The 22 files this table calls unreadable were measured on 2026-08-19: every
  one opened and decoded cleanly and holds no record of type `user` or
  `assistant`. `extract` now reports that outcome under its own name and keeps
  `unreadable` for files whose bytes would not read.
- SETTLED 2026-08-20: the turn classifier was measured against turns read and
  marked by hand, and its thresholds chosen from a sweep rather than guessed.
  Interrupt 1.00/1.00, question 0.96/0.71, approval 1.00/0.70, correction
  0.60/0.93, over 144 marks.
  The first pass of this used 300 marks and reported worse numbers, because the
  sampler was drawing from a population the ledger does not count and running a
  drifted second copy of the classifier. Both are fixed; the surviving marks are
  the ones drawn from the right population.
  A third of what the ledger called a user prompt turned out to be the harness
  rather than a person, and is now excluded by prompt origin.
- `repeat_calls` and `correction_candidates` are heuristics with tunable thresholds
  and have not been validated against a hand-labelled sample. Their absolute
  values should not be trusted; their movement over time is the usable part.
- Transcripts begin 2026-06-20, so "all history" is roughly two months, not the
  four months the prompt-history file reaches back.
