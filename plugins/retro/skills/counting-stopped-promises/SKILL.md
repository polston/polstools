---
name: counting-stopped-promises
description: Use when checking whether announced work actually ran — a session ended a turn saying it was about to do something and nothing happened — or when a standing rule meant to prevent that needs evidence it worked. Reads measured session history, not memory.
---

# Counting stopped promises

## Overview

A stopped promise is one event: a message that **ended a turn** by saying it was
about to do something, with nothing pending, where nothing ran. The work waits
until a human speaks again.

The failure mode this replaces is judging it from memory. The instances you
recall are the loud ones; the rate is unknown, and so is whether it is moving.

It also replaces a subtler failure: measuring a ratio that writing style can
move. An earlier version of this tool counted, of all messages committing to an
action, how many carried the tool call. That number turned out to be almost the
same as "did this message end the turn", and it fell by a third over three
months purely because less prose was being written before tool calls. Counting
events among turn-ending messages is immune to that.

## The procedure

**1. Check the tool works here.** Fixtures are built in a temp directory; nothing
is written to the repository or to any configuration directory.

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/stopped-promises.py" --selftest
```

**2. Measure a closed window.** Both dates, always — the corpus is appended to
while you read it, so an open window is not reproducible.

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/stopped-promises.py" \
    --since 2026-08-01 --until 2026-08-31 \
    --candidates "$SOMEWHERE_OUTSIDE_ANY_REPO/candidates.txt"
```

**3. Read the candidates, write verdicts, re-run.** One line per id:

```
a1b2c3d4e5f6 real
0f9e8d7c6b5a not-real
```

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/stopped-promises.py" \
    --since 2026-08-01 --until 2026-08-31 \
    --candidates "$SOMEWHERE_OUTSIDE_ANY_REPO/candidates.txt" \
    --verdicts  "$SOMEWHERE_OUTSIDE_ANY_REPO/verdicts.txt"
```

Add `--json` for one object instead of a table. Diagnostics go to stderr, so the
JSON always parses.

## Reading the output

| Output | Trust it? |
|---|---|
| **Census** — files, sessions, turn ends, days covered | Yes, as it stands. Structural; no sentence is interpreted. |
| **Classified counts** — commitment, deferred, handed back, no promise | Only as a shape. These come from the detector. |
| **Verified rate** — stopped promises per hundred turn ends | Yes, and only this. Exists only once verdicts are written. |

Every turn end lands in exactly one bucket, so the classified counts sum to the
turn-end total. If they don't, something is wrong with the run, not with you.

## The rule that makes this honest

**Never quote the candidate count as a rate.** A detector that matches the
grammar of a promise mostly catches sentences that hand control back — offers,
conditionals, and refusals to act now. On the corpus this was built against,
roughly one candidate in five was the real thing. That ratio is a property of a
corpus and a detector together and does not transfer to another machine, which
is why the tool measures its own precision from your verdicts rather than
carrying a number around.

A run with no verdicts file is an unfinished measurement. It exits 1 and says so.

## Running it on another machine

Transcript roots are resolved highest-precedence-first:

1. `--root DIR`, repeatable
2. `STOPPED_PROMISES_ROOTS`, separated by the platform path separator
3. `$CLAUDE_CONFIG_DIR/projects`
4. `~/.claude/projects`

A value that is empty or only whitespace counts as unset, so a misconfigured
shell cannot redirect the walk. Both `.jsonl` and `.jsonl.gz` are read. The
candidates file defaults into the system temporary directory and the tool
refuses to write it inside a git work tree — it is the only file carrying
message text, and it carries it redacted.

Exit codes follow the other scripts here: `0` every candidate has a verdict,
`1` some are unreviewed, `2` could not run.

## Red flags

| Thought | Reality |
|---|---|
| "It found 51, that's the number" | 51 is a reading list, not a finding. Most will not be real. |
| "Last month was 30, this month 51 — it's getting worse" | Not unless both runs carry the same classifier version and both windows are closed. |
| "The precision was 20% before, so 51 means about 10" | Precision is measured per corpus. Write verdicts and let the tool tell you. |
| "I'll compare this machine's rate to the other one's" | Different corpora, different populations. Compare each machine to its own earlier window. |
| "No candidates, so the rule is working" | Check the census first. Zero candidates with zero turn ends means the walk found nothing. |
