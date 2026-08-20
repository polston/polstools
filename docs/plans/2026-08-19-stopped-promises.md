# Stopped promises — design

**Status:** spec, revised after one review round
**Date:** 2026-08-19

## The thing being measured

An assistant message says it is about to do something, the turn ends, and
nothing runs. The work waits until the human speaks again.

**A stopped promise** is the one name for this, everywhere — script, skill,
comments. It is one event, countable, and it is only ever counted among messages
that *ended a turn*.

## What the first version of this spec got wrong

The first version counted a ratio: of all messages committing to an action, how
many carried the tool call in the same message. A review round killed it, and the
disproof reproduced independently three times.

A message either carries a tool call, in which case the turn continues, or it does
not, in which case the turn ends. Measured: of text-bearing messages, mid-turn
ones carry a tool call 6,496 to 28; turn-ending ones 103 to 3,081. So that ratio
was very nearly "did this message end the turn".

The proof it measured nothing about promises: running the same length buckets
over **all** prose-bearing messages, with no commitment test at all, reproduces
the identical curve.

| Population | <200 | 200–499 | 500–1499 | 1500+ | overall |
|---|---|---|---|---|---|
| No classification at all | 90.7% | 70.2% | 36.2% | 22.8% | 68.0% |
| With the commitment filter | 97.6% | 84.8% | 70.2% | 29.2% | 87.2% |

The filter moved the level, not the shape. Worse, the ratio moved over time for a
reason unrelated to behaviour: prose written before a tool call fell from 66.1%
to 40.2% to 23.4% across the window, and 11,326 of 17,936 tool-calling messages
carry no prose at all, so they never enter the ratio. Writing less preamble
lowers the number while nothing changes about whether announced work happens.

**The fix is the denominator.** Count stopped promises among messages that ended
a turn. That population is already conditioned on the thing the old ratio was
accidentally measuring, so style cannot move it.

## The shape

Three outputs, in increasing order of how much they can be trusted.

### 1. Census — structural, no judgement

Counts derived from record structure alone: roots walked (a count, not paths),
files, files skipped and why, sessions, sessions with a real human conversation,
turns, and messages that ended a turn. No sentence is interpreted. These are
quotable as they stand and they make everything below auditable.

### 2. Candidates — needs a human to read

Messages that ended a turn while committing to an action, where nothing was
pending. Written to a file with a stable id each. **The count is not a finding**
and the file says so in its own header. On the reference corpus a strict
detector gives about 167 and a loose one about 356, from the same data — which is
exactly why the number cannot be reported on its own.

### 3. Verified rate — the actual answer

Stopped promises per hundred turn-ending messages, computed only from
hand-written verdicts. Also reports the detector's precision **on this corpus**,
because precision is a property of a corpus and a detector together and does not
transfer between machines.

Reading the candidates is the primary mechanism, not a side loop. A run with no
verdicts file is an unfinished measurement, and says so.

## Mechanism

### Reconstructing a message

Filter to main-thread assistant records **first**, then group runs sharing a
`message.id`. Both halves matter:

- One reply is split across records: prose and tool call land in different
  records for 29–31% of messages. Grouping per record reports the prose half as
  tool-free.
- Other record kinds — attachments, user records, system records — land *between*
  the parts of one message. Grouping "consecutive records" without filtering
  first fractures about 5% of messages and recreates the exact bug the rule
  exists to prevent.

### Excluding subagent work

Subagent transcripts are **separate files in their own directories**; no file
mixes them with main-thread conversation. They are reached because the walk is
recursive, and they are dropped by the sidechain flag on each record. Their final
message is legitimately prose with no tool call, since its text is its return
value. By records they outnumber main-thread traffic about 1.3 to 1; by file
count about 3.3 to 1.

### Which turns count

A turn ends at a genuine human prompt, identified by a **positive test** on the
record's prompt source — typed, queued, or an accepted suggestion. The first
version used a list of things that are *not* human prompts, which fails open:
the corpus carries 21 record kinds and 965 automatic task-completion notices that
a negative list silently accepts as human turns.

Excluded, because no human was waiting: sessions driven by the SDK or running as
background work. They are 22% of the naive population, and their final prose
message is a return value, not a stopped promise.

Turns that end because the human interrupted are excluded. Turns with no
following prompt at all — 295 on the reference corpus — are counted separately
and are not candidates; the human closed the session, the assistant did not stall.

### Classifying the closing region

The closing region is the final non-empty line **and** the final two sentences;
a match in either counts, and the file states this rather than leaving "or" to be
guessed.

Three classes, applied in order: **not a promise** (offers, conditionals on the
human, explicit declines), **deferred** (points at a later moment or a result
still coming), **commitment** (first-person, tool-doable verb, or a bare
declaration of starting).

**The token lists ship as versioned data inside the script, not as prose.** A
review implemented two readings this spec's first version permitted and got
81.1% to 92.1%, 608 to 3,350 messages, and 5 to 122 candidates. Every report
stamps the classifier version, so two runs are comparable or visibly are not.

### Pending work

A candidate requires that nothing was launched that could still report back: no
subagent, no backgrounded command, since the last human prompt. On the reference
corpus this removes 18% of otherwise-qualifying messages.

### Identity of a candidate

The id is a hash of the session id and the message id — never of the message
text. Hashing the closing line collides: one closing line is shared by 50
distinct messages, and 11.5% share a line with something. One verdict would
silently label all of them. Messages are also de-duplicated on that same pair
before counting, because resumed sessions replay history and 7% of messages
appear in two files.

## Portability

Root resolution, highest first: `--root` (repeatable), then
`STOPPED_PROMISES_ROOTS` split on the platform path separator, then
`$CLAUDE_CONFIG_DIR/projects`, then `~/.claude/projects`. An environment value
that is empty **or only whitespace** is treated as unset, so a misconfigured
shell cannot redirect the walk to a filesystem root.

The walk is recursive — a non-recursive walk finds nothing, since every
transcript sits at least one level down. Roots are de-duplicated after resolving
symlinks and relative segments. A root that does not exist, is not a directory,
or holds no transcripts is reported on stderr and skipped; if none resolves,
exit 2.

Reads `.jsonl` and `.jsonl.gz`. The reference machine has **zero** gzipped
transcripts, so that branch is proven by a fixture and this document does not
claim live coverage.

Python 3 standard library only, single file, no dependency, no build step.

## Privacy

Only the candidates file carries message text, and only the closing region,
after redaction.

Redaction covers this machine's home path and account name, email, IPv4, MAC,
long tokens — **and generic absolute paths belonging to anywhere else**. That
last category is new and is the reason this script does not simply copy the
sibling's redactor: measured against the default root, 381 of 9,717 prose-bearing
messages carry an absolute path outside the home directory, across 47 distinct
prefixes, and every one of them survives the sibling's rules. Under this
repository's own constraint about not naming other projects, those directory
names are the sensitive half. The exposure is unconditional, not something that
only appears once a foreign root is configured.

The account-name rule runs **last**. Running it first rewrites the name inside
the home path, after which no home-path rule can match — a defect found in the
sibling script during review and fixed there in this branch.

The candidates file defaults into the system temporary directory, and the path is
printed with the home prefix redacted. The script refuses to write it inside a
git work tree.

## Interface

```
stopped-promises.py [--root DIR]... [--since YYYY-MM-DD] [--until YYYY-MM-DD]
                    [--candidates FILE] [--verdicts FILE] [--json] [--selftest]
```

- The census and the summary print to **stdout**. Every diagnostic, skipped root,
  skipped file and the candidates path print to **stderr**, so `--json` output
  always parses.
- `--since` and `--until` bound a closed window on record timestamps. Both are
  required to reproduce a past run, because the corpus is live — it grew during
  the review that examined it.
- Reports always state the window's actual first and last day and how many days
  carried data. The reference window's three calendar months are 9, 22 and 20
  active days, so a per-month series compares unequal things unless it says so.
- `--selftest` runs the fixtures and exits. The repo has no test runner and takes
  no dependencies, so the checks live in the script.

**Exit codes.** `0` every candidate has a verdict and the verified rate is below
threshold. `1` there are candidates a human has not ruled on, or the verified
rate is at or above it. `2` could not run. There is no threshold on a ratio that
style can move, because that ratio is gone.

## Reference numbers

Measured while reviewing this design, on one corpus, at one moment. They exist to
make a foreign result interpretable and to catch gross regressions — **not as
acceptance targets**, since the corpus is appended to continuously.

| Quantity | Value |
|---|---|
| Transcript files | 1,947 (was 1,906 four hours earlier) |
| Files that are subagent-only | 1,480 |
| Sessions with a real human conversation | 437–446 depending on the human-prompt test |
| Messages that ended a turn, prose-bearing | ~3,081 |
| …after removing pending work | ~2,524 |
| Candidates, strict vs loose detector | 167 vs 356 |
| Hand-verified real, from an earlier sample of 29 | 6–7 |
| Detector precision, that sample | ~20% |

## Done when

1. `--selftest` passes, covering: the gzip branch; a multi-root walk; grouping
   after filtering to assistant records; sidechain exclusion; the positive
   human-prompt test; id collision resistance; cross-file de-duplication. Each
   fixture fails if its rule is removed.
2. A run against the live corpus produces a census whose totals reconcile with an
   independent count, and the reference numbers above land in the right order of
   magnitude.
3. A closed `--since`/`--until` window produces identical output on two runs.
4. `--verdicts` on a fixture with known labels reports the verified rate and a
   precision equal to the share of verdicts marked real, and names any verdict id
   absent from the candidate set.
5. A produced candidates file contains no account name, home path, email, IPv4,
   MAC, or absolute path from outside the home directory.
6. The privacy scan over the worktree shows **no category beyond the documented
   commit-identity hits**, which are an accepted exception and are present before
   this work starts.
7. Exit codes behave: `0`, `1` and `2` each reachable and each demonstrated.
8. Every command in the skill runs as written, invoked through the plugin root
   variable the sibling skills use.

## Work that can run in parallel

The script is a single file, so its own tasks are serial — this is stated
plainly rather than split into lanes that would collide. Three streams run
alongside it from the moment the interface above is fixed:

| Stream | Depends on | Files |
|---|---|---|
| A. Script | nothing | `plugins/retro/bin/stopped-promises.py` |
| B. Fixtures | the interface, not the implementation | fixture directory |
| C. Skill | the interface, not the implementation | one `SKILL.md` |
| D. Manifests | nothing | marketplace and plugin manifests |

B, C and D are file-disjoint from A and from each other and can be written
concurrently by separate agents. The live-corpus run in "Done when" is the only
step needing A finished.

## Non-goals

No scheduler, no database, no dashboard, no ledger, no auto-apply, no editing of
any rule file. The verdicts file is hand-written input the script only reads. No
merging of results across machines. No claim that detector precision transfers
between corpora — measuring it locally is the point. No reporting of any rate
that has not been through human verdicts.

## What ships

| Path | Contents |
|---|---|
| `plugins/retro/bin/stopped-promises.py` | The script, self-contained, with fixtures behind `--selftest` |
| `plugins/retro/skills/counting-stopped-promises/SKILL.md` | When to run it, how to read the three outputs, how to work the verdict loop |
| `docs/plans/2026-08-19-stopped-promises.md` | This document |
| `.claude-plugin/marketplace.json`, `plugins/retro/.claude-plugin/plugin.json` | Version bump, descriptions kept identical between the two |

### Not importing the sibling script

The sibling in the same directory has parsing helpers this tool needs. They are
duplicated — about 65 lines for the reader, 93 with redaction — for two reasons.
Three unmerged branches are concurrently rewriting that file's reader, two of
them replacing its failure signalling and one changing a helper's return shape; a
measurement tool whose parser moves underneath it produces trends that move for
unrelated reasons. And this script's redaction must be *stronger* than the
sibling's, since it adds the foreign-path category described above.

The cost is accepted and marked in both files: a shared parsing fix must be
applied twice. The two scripts also define their populations differently — the
sibling counts every user record, this one counts only human prompts — so they
will publish different session counts for the same corpus. Each says so.
