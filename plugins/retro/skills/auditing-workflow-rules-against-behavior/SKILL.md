---
name: auditing-workflow-rules-against-behavior
description: Use for a monthly or periodic review of whether standing instructions and installed skills are actually doing anything — which rules are dead letters, which skills never fire, which instructions are contradicted by what sessions show. Also use when instruction files have grown long enough that nobody can say which parts are load-bearing.
---

# Auditing workflow rules against behavior

## Overview

Instruction files only grow. Every friction adds a clause; nothing removes one.
After a while a CLAUDE.md is part live policy and part sediment, and there is no
way to tell which is which by reading it — a rule that is never followed and a
rule that is never needed look identical on the page.

Sessions settle it. A skill that never fires, a rule whose violation appears in
the record every week, a clause written for a tool that no longer exists: all of
these are visible in measured history.

The point is deletion. A shorter instruction file that is entirely load-bearing
beats a long one where the reader cannot tell.

## The procedure

**1. Measure and list skill firing.**

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/retro.py" extract
python "${CLAUDE_PLUGIN_ROOT}/bin/retro.py" skills --days 30
python "${CLAUDE_PLUGIN_ROOT}/bin/retro.py" pack --days 30
```

`skills` splits installed skills into fired and never-fired. Names that fired but
have no SKILL.md on disk are harness built-in commands or a skill since renamed —
check before treating one as missing.

**2. Triage the never-fired list.** For each, exactly one verdict:

- **Wrong trigger** — the skill is right but its description does not match how
  the work actually gets described. Propose the new description text.
- **Superseded** — something else covers it now. Propose retirement.
- **Genuinely unused** — the situation has not come up. Leave it; note the date.

Never-fired is evidence about the *description*, not about the skill's quality.
Most dormant skills have a trigger problem, not a content problem.

**3. Test each standing rule against the record.** For every clause in the
instruction files, ask which of these it is:

- **Enforced** — something mechanical makes violation impossible (a hook, a gate).
- **Followed** — no violations in the window's moments.
- **Violated** — the pack's quoted corrections show it being ignored. This is the
  interesting case: either the rule needs to be enforced mechanically, or it is
  written in a way that does not survive contact with a real session.
- **Unmeasurable** — no signal either way. Say so plainly rather than guessing.
- **Stale** — it references a tool, path, flag, or workflow that no longer
  exists. Verify by checking, then propose deletion.

**4. Check the stale ones by running something.** A clause naming a binary, a
config path, or a command is checkable. Do not mark anything stale on the basis
that it sounds old.

**5. Deliver a file with three lists:** rules to delete, rules to sharpen (with
replacement text), skills to re-describe or retire (with replacement
descriptions). Propose; do not edit.

## What this cannot tell you

Most instructions about tone, judgment, and taste leave no mechanical trace. This
audit will not tell you whether they are working. Say "unmeasurable" and move on —
guessing, and dressing the guess as a finding, is worse than the gap.

The quoted moments are the only evidence available for those, and a handful of
moments is not a measurement. Treat them as illustrations, never as counts.

## Common mistakes

**Reading the never-fired list as a to-do.** Most of it is fine. A skill for a
rare situation should be dormant most months.

**Marking a rule stale because the tool sounds unfamiliar.** Check. The rule may
be the only surviving documentation of something still installed.

**Proposing deletion without replacement text for the rules being sharpened.**
Half a proposal.

**Auditing everything every month.** Rules that came back "enforced" or
"followed" twice running do not need a third look; spend the window on the
violated and stale ones.

## Red flags

- "This rule is probably obsolete" — probably is not a verdict, go check
- "None of these skills seem useful" — firing data is about descriptions, not worth
- A finding that names no clause and no file
- An audit that proposes zero deletions — instruction files do not shrink by accident
