# Follow-through measurement — design

**Status:** spec, awaiting review round
**Date:** 2026-08-19

## The thing being measured

An assistant message says it is about to do something — run a check, write a
file, start a build — and the message carries no tool call. The turn ends. The
work does not happen until the human says something.

**Follow-through** is the one name for this throughout the script, the skill, and
every comment: *of the messages that commit to an action the assistant can take
itself, what share carry the tool call in the same message?*

That is the whole metric. Everything below serves producing it honestly on a
machine that is not the one where the pattern was first noticed.

## Why this needs a tool at all

The pattern was measured once, by hand, over a three-month corpus. Two findings
make a repeatable tool worth building, and one makes it dangerous to build
naively.

1. Follow-through is **not uniform** — it tracks message length almost
   monotonically, from 97.7% on short messages to 28.1% on very long ones. A
   single headline number hides the effect entirely.
2. It **moved over time**, and not in the good direction: 92.3% → 85.9% → 81.3%
   across three consecutive months, and 98.3% → 95.3% → 93.2% after controlling
   for a shift in the mix of message types. A number measured once cannot show
   that; only a rerunnable measurement can.
3. The detector that finds candidates runs at roughly **20% precision**. Matching
   the grammar of a commitment catches mostly sentences that hand control back —
   offers, conditionals, explicit refusals to act now. A tool that prints the raw
   candidate count as a finding would be confidently wrong, every time.

Point 3 is why the output is split in two, and it is the single most important
design constraint in this document.

## Output: two sections that must never blur

### Section A — mechanical, trustworthy without a human

Every number here is derived from structure, not from judging what a sentence
meant. These can be quoted directly.

| Metric | Why it is here |
|---|---|
| Follow-through rate, with n | The headline |
| …by message length bucket: <200, 200–499, 500–1499, 1500+ chars | The dominant predictor; the headline is misleading without it |
| Deferred share of all commitments | The confound. A rule that only teaches the promise to rephrase itself as "I'll do it when X" shows up here as a rise, while the headline improves |
| Follow-through for messages that declare nothing is needed *and* state work is proceeding | The one construct measured at 0 of 7; mechanically detectable, and the sharpest single check |
| All of the above by month | Direction of travel is the usable signal |
| Population census: roots walked, files, sessions, multi-turn sessions, messages classified | Makes every rate above auditable rather than asserted |

### Section B — candidates, unverified, for a human to read

A list of messages that look like the failure: turn-final, no tool call, not
deferred, nothing pending. Each entry carries a stable id and the redacted
closing region.

Section B is written to a file and is **labelled unverified in the file itself**.
Its count is not a finding and the script never presents it as one.

### The verdict loop — how Section B becomes a real number

Section B is a dead end without a way to record what reading it concluded. So:

- Each candidate gets a stable id derived from a hash of its own closing text, so
  ids survive re-runs and a candidate keeps its id as the corpus grows.
- A verdicts file maps id → `real` / `not-real`, one per line, hand-written.
- Run with `--verdicts FILE` and the script prints two further numbers: the
  hand-verified dangling-promise rate, and **the measured precision of its own
  detector** on this corpus.

The second of those is the point. Precision is a property of a corpus and a
detector together; assuming it transfers from the machine where it was first
measured is exactly the guessing this tool exists to replace.

## Mechanism

### The unit: one assistant message

Two structural facts about transcripts, both of which will silently wreck the
measurement if missed. They are the reason this is a script and not a grep.

1. **One assistant reply is split across several records** that share a
   `message.id`. Its prose and its tool call routinely land in *different*
   records. Counting per record reports the prose record as having no tool call.
   Measured on the reference corpus, this alone would misclassify roughly 17% of
   messages. **Messages are therefore reconstructed by grouping consecutive
   main-thread records with the same `message.id`.**
2. **Subagent traffic is interleaved into the same files** and outnumbers
   main-thread traffic several times over. A subagent's final message is
   *legitimately* prose with no tool call — its text is its return value.
   **Records flagged as a sidechain are dropped before anything is counted.**

### The closing region

A commitment counts only if it appears in the **final non-empty line** or the
**final two sentences** of the message. A commitment in the middle of a long
message is not the pattern — the message continues past it.

### Three classifications, applied to the closing region in order

| Class | What it captures | Effect |
|---|---|---|
| **Not a promise** | Offers, conditionals on the human, explicit declines and stops | Excluded — stopping was correct |
| **Deferred** | Points at a later moment or a result genuinely still coming | Counted separately; never a candidate |
| **Commitment** | First-person commitment to a verb the assistant can only execute via a tool, or a bare declaration of starting | The measured population |

A message is a Section B candidate only when it is a commitment, is not
deferred, is turn-final with no tool call, and **no work was pending** — the turn
launched no subagent and no backgrounded command. A promise to report on
something still running is not this failure.

### Turn boundaries

A turn ends at the next genuine human prompt. Records that are tool results,
tool-written user records, meta records, interrupts, harness wrappers (system
reminders, command expansions, hook output, captured command output) are not
human prompts. Interrupted turns are tracked separately and excluded — the human
stopped that turn, the assistant did not.

## Portability — the reason this exists as a shipped tool

Resolution order for transcript roots, highest first:

1. `--root DIR`, repeatable
2. `FOLLOW_THROUGH_ROOTS`, a list separated by the platform path separator
3. `$CLAUDE_CONFIG_DIR/projects`
4. `~/.claude/projects`

An **empty-string environment variable is treated as unset**, matching the
resolution the official plugins use, so a misconfigured shell cannot silently
redirect the walk to a filesystem root.

Other portability requirements:

- Reads `.jsonl` and `.jsonl.gz`. The reference machine has zero gzipped
  transcripts, so this path is proven against a synthetic fixture, not against
  live data, and the spec says so rather than claiming coverage it does not have.
- `pathlib` throughout; no shell invocation; no path separator assumptions.
- Python 3 standard library only. Single file. No dependency, no build step, no
  daemon, no state directory required for a plain run.
- Roots that do not exist are reported on stderr and skipped. If no root
  resolves, exit 2.

## Invariants

1. **Stdlib only, single file, no build step.** Inherited from the retro plugin.
2. **No private data in the repository.** No path belonging to the author or to
   another project appears in any tracked file, example, default value, docstring
   or commit message. No other project is named, in any form.
3. **Message text leaves the script in exactly one place** — the Section B
   candidates file — and only after passing through redaction covering username,
   home path, email, IPv4, MAC, and long tokens. The categories are kept in step
   with the sibling privacy scanner.
4. **Redaction does not catch foreign absolute paths.** Once a root outside the
   default is configured, a candidates file can carry that root's paths. The file
   is written outside the repository, is gitignored, and is not safe to paste
   anywhere public without reading it first. Widening redaction is out of scope.
5. **Section B's count is never reported as a finding.** Any presentation of a
   dangling-promise rate requires a verdicts file.
6. **Exit codes:** `0` ran clean and flagged nothing; `1` ran clean and flagged
   something; `2` could not run. Matching every other script in the repo.
7. **Read-only.** The script never writes anywhere under a Claude configuration
   directory, and never modifies a transcript.

## Interface

```
follow-through.py [--root DIR]... [--since YYYY-MM] [--candidates FILE]
                  [--verdicts FILE] [--threshold PCT] [--json]
```

- Section A prints to stdout as a table; `--json` emits the same numbers as one
  object for a caller that wants to diff two runs.
- `--candidates FILE` chooses where Section B is written. It defaults to a
  named file in the system temporary directory, and the chosen path is always
  printed. The default is deliberately not the current directory, which may
  itself be a repository — invariant 2 forbids that file landing in one by
  accident.
- `--since YYYY-MM` narrows the window.
- `--threshold PCT` sets the follow-through percentage below which the run is
  flagged. Default **90**, which sits below every monthly figure in the
  baselines and above the last one measured, so an unchanged corpus does not
  flag and further decline does.
- Exit `1` when overall follow-through is below the threshold, **or** when the
  nothing-needed-and-proceeding construct appears at all. That construct is
  flagged on sight rather than on a rate, because every occurrence measured so
  far failed.

## Baselines

Measured on the reference corpus (three consecutive months, 1,906 transcript
files, 422 sessions carrying real conversation, 60 of them multi-turn). These are
what a first run elsewhere is compared against — not targets, but the numbers
that make a foreign result interpretable.

| Quantity | Value |
|---|---|
| Commitment-ending messages classified | 2,059 |
| Follow-through, overall | 87.2% |
| Follow-through by length: <200 / 200–499 / 500–1499 / 1500+ | 97.7% / 84.8% / 70.1% / 28.1% |
| Deferred share of commitments, first → last month | 9.3% → 17.1% |
| Follow-through, controlled for deferral and questions, by month | 98.3% → 95.3% → 93.2% |
| Nothing-needed-and-proceeding construct | 0 of 7 carried a tool call |
| Detector precision before hand-verification | ~20% (about 6–7 real in 29 candidates) |

## Done when

1. The script runs on this machine against the live corpus and reproduces the
   Section A baselines above within rounding.
2. It runs with no environment configuration on a machine whose transcripts are
   in the default location, and with `--root` on one where they are not.
3. A synthetic fixture proves the gzip branch, the multi-root walk, the
   `message.id` grouping rule, and the sidechain-exclusion rule. Each of those
   four has a fixture that fails if the rule is removed.
4. `--verdicts` against a fixture whose labels are known in advance prints a
   verified rate and a detector precision equal to the share of that fixture's
   verdicts marked real. A verdict naming an id that is not in the candidate set
   is reported rather than ignored.
5. The privacy scanner reports zero hits over the worktree, and a produced
   candidates file contains no username, home path, email, IPv4 or MAC.
6. Exit codes behave: `0` clean, `1` flagged, `2` when no root resolves.
7. The skill exists, and every command in it runs as written.

## Non-goals

No scheduler, no database, no dashboard, no ledger, no auto-apply, no editing of
any rule file. The verdicts file is not a ledger — it is hand-written input the
script only ever reads, and a run without one is complete on its own. No merging
of results across machines — two machines produce two
reports, and comparing them is a human reading two tables. No attempt to widen
redaction to foreign absolute paths. No claim that the detector's precision
transfers between corpora; measuring it locally is the whole point of the verdict
loop.

## What ships

| Path | Contents |
|---|---|
| `plugins/retro/bin/follow-through.py` | The script. Self-contained. |
| `plugins/retro/skills/measuring-follow-through-on-announced-actions/SKILL.md` | When to run it, how to read the two sections, and the hand-verification loop |
| `docs/plans/2026-08-19-follow-through-measurement.md` | This document |
| `.claude-plugin/marketplace.json`, `plugins/retro/.claude-plugin/plugin.json` | Version bump and description, kept in step |

### Deliberately not importing the sibling metrics script

The sibling script in the same directory already has parsing helpers this tool
needs. They are duplicated instead, roughly sixty lines, for one reason: three
unmerged branches are concurrently rewriting that file's reader, including its
failure signalling. A measurement tool whose parser changes underneath it
produces trends that move for reasons unrelated to the behaviour being measured.
Duplication also means the file can be copied to another machine on its own,
which is the requirement that prompted the work.

The cost is real and is accepted: a future fix to a shared parsing rule must be
applied in two places. The two readers are marked in both files as intentional
duplicates so neither looks like an oversight.
