---
name: sparring-skills
description: Run blinded parallel A/B skill comparisons (harness default vs. candidate skill) with blinded comparative judging, anti-bloat edit gating, and multi-stage chaining.
---

# Sparring skills

Before any other action, run
`<python> <plugin-root>/bin/skill-profile-ctl check sparring-skills`. If it
exits 1 or 2, stop and report its output. `<plugin-root>` is the absolute path
two directories above this `SKILL.md`, whose directory is
`<plugin-root>/skills/sparring-skills`; take it from this file's own path,
never from the working directory or an environment variable. `<python>` is `sh
<plugin-root>/bin/python-launcher`.
Quote both paths and write them with forward slashes, also on Windows. If the
check exits 2 because session variables of two harnesses are set, rerun it once
with `P_SKILL_HARNESS` set to this session's harness (`claude`, `codex`, or
`antigravity`).

The canonical policy is `<skill-root>/contract-v1.json`; the deterministic
helper is `<skill-root>/scripts/skill_spar.py`. Do not restate policy rules or
schemas in an adapter.

## When NOT to use this skill

- Single-target code or design review with no default-vs-skill comparison: use `adequacy-review`.
- Open-ended diagnosis or testing competing technical hypotheses outside skill calibration: use `investigating-and-improving`.
- Post-ship live-session outcome tracking and Keep/Revise/Revert verdicts: hand off to `reviewing-improvement-effects`.
- Trivial mechanical edits where running parallel A/B workers is disproportionate overhead.

## Procedure

1. Ground the shared task brief once (read-only) and choose an external work
   directory outside every Git repository (such as `/tmp/skill-spar-<slug>`).
2. Identify the active harness from session context. Read exactly one adapter:
   - Claude Code: `references/claude-code.md`
   - Codex: `references/codex.md`
   - Antigravity: `references/antigravity.md`
3. Follow that adapter to run `init`, spawn fresh isolated parallel control and
   treatment workers (never reuse or recycle worker threads), run `anonymize`
   and `judge-packet`, dispatch fresh cold comparative judges, and run
   `unblind-score`.
4. If the unblinded comparison identifies a generalizable skill improvement,
   apply the minimal edit (compressing or evicting prose before adding) with a
   regression test and run `edit-gate` before committing. Reject any edit that
   fails the gate.
5. For chained stages (such as Plan A/B -> Goal A/B -> Execute -> Pilot), call
   `chain-advance` with the selected winning artifact so downstream stages
   inherit verified context without leaking earlier judge commentary. After
   execution, hand off the emitted pilot draft to `reviewing-improvement-effects`.
