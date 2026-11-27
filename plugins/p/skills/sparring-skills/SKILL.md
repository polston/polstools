---
name: sparring-skills
description: Run blinded parallel A/B skill comparisons (harness default vs. candidate skill) with context-inferred zero-argument defaults, blinded judging, anti-bloat edit gating, and multi-stage chaining.
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

- Single-target code or design review: use `adequacy-review`.
- Technical diagnosis outside skill calibration: use `investigating-and-improving`.
- Post-ship live-session tracking (`keep`/`revise`/`revert`): hand off to `reviewing-improvement-effects`.
- Trivial edits where parallel A/B workers are disproportionate overhead.

## Zero-argument defaults

When invoked bare with no extra arguments, infer context read-only (recent
conversation, active plan or goal artifact, git branch and tracked diff) and
run `<python> <skill-root>/scripts/skill_spar.py resolve-defaults` with the
observed inputs. Do not interrogate the user; ask at most one concise question
only if `resolve-defaults` exits 1.

- Follow the resolved route from `contract-v1.json`: a specific discussed or
  edited skill runs a single-stage comparison; an existing plan starts at
  `writing-goals`; a task with no plan runs the default chain (`writing-plans`
  -> `chain-advance` -> `writing-goals`).
- If `recursive_guard` is true, produce the requested artifact directly without
  spawning nested sparring workers.
- Stage candidate skill edits in an external work directory to run `edit-gate`
  without mutating the plugin repository. Pause on contested Stage 1 verdicts
  or before mutating/pushing the plugin repo or executing the winning goal,
  presenting one consolidated approval summary (verdicts, staged gated edits,
  and winning goal).

## Procedure

1. Ground the shared task brief once read-only and pick an external work
   directory outside every Git repository.
2. Identify the active harness from session context. Read exactly one adapter:
   - Claude Code: `references/claude-code.md`
   - Codex: `references/codex.md`
   - Antigravity: `references/antigravity.md`
3. Follow that adapter to run `init`, spawn fresh isolated parallel workers,
   run `anonymize` and `judge-packet`, dispatch fresh cold comparative judges,
   and run `unblind-score`.
4. For any generalizable skill improvement, tighten existing prose first, add a
   regression test, and run `edit-gate`. Reject any edit that fails the gate.
5. For chained stages, call `chain-advance` with the promoted artifact so
   downstream stages inherit verified context without judge commentary. After
   execution, hand off the pilot draft to `reviewing-improvement-effects`.
