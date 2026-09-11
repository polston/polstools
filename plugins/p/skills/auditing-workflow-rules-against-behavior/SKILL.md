---
name: auditing-workflow-rules-against-behavior
description: Use for a monthly or periodic review of whether standing instructions and installed skills are actually doing anything — which rules are dead letters, which skills never fire, which instructions are contradicted by what sessions show. Also use when instruction files have grown long enough that nobody can say which parts are load-bearing.
---

# Auditing workflow rules against behavior

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check auditing-workflow-rules-against-behavior`.
If it exits 1 or 2, stop and report its output.

## Overview

Instruction files only grow. Every friction adds a clause; nothing removes one.
After a while a CLAUDE.md is part live policy and part sediment, and there is no
way to tell which is which by reading it — a rule that is never followed and a
rule that is never needed look identical on the page.

Measured history can establish applicable opportunities or verified obsolete
references. Attribution gaps and selected moments alone cannot settle whether
a rule or skill is useful. Delete or sharpen only where evidence supports it.

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

**1. Measure and list skill firing.**

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/retro.py" extract
"${CLAUDE_PLUGIN_ROOT}/bin/retro.py" skills --days 30
"${CLAUDE_PLUGIN_ROOT}/bin/retro.py" pack --days 30
```

`extract` exits 1 when a transcript would not read. It still writes the ledger,
and retries that file on the next run.

`skills` reports observed attribution against an inventory that includes cached
installations, not an authoritative active catalogue. Names that fired but
have no SKILL.md on disk are harness built-in commands or a skill since renamed —
check before treating one as missing.

**2. Triage skills without observed attribution.** For each, choose a verdict
only after checking activation, attribution coverage, and applicable cases:

- **Wrong trigger** — the skill is right but its description does not match how
  the work actually gets described. Propose the new description text.
- **Superseded** — something else covers it now. Propose retirement.
- **Genuinely unused** — the situation has not come up. Leave it; note the date.

- **Unobservable or insufficient evidence** — activation, attribution, or
  applicable opportunities cannot be established. Do not change the trigger
  or retire the skill. Zero observed invocations alone establishes neither
  disuse nor a bad description.

**3. Test each standing rule against the record.** For every clause in the
instruction files, ask which of these it is:

- **Enforced** — something mechanical makes violation impossible (a hook, a gate).
- **Followed in observed opportunities** — cite actual applicable cases, the
  instruction version, and coverage limits. No observed opportunities means
  Unmeasurable; absence from selected friction moments is not compliance.
- **Violated in observed opportunities** — verified behavior contradicts an
  applicable instruction known to be active then. Candidate correction labels
  alone cannot establish this verdict or justify a change.
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
- "None of these skills seem useful" — missing attribution does not establish disuse
- A finding that names no clause and no file

All of these mean: name the clause, run the check that settles it, and write the
replacement text.
