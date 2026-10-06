---
name: managing-skill-activation
description: Use when checking or changing the p home/work default, reviewing disabled skills, overriding one skill, or repairing Codex skill-catalog visibility.
---

# Manage p skill activation

Before any other action, run
`<python> <plugin-root>/bin/skill-profile-ctl check managing-skill-activation`.
If it exits 1 or 2, stop and report its output. `<plugin-root>` is the absolute
path two directories above this `SKILL.md`, whose directory is
`<plugin-root>/skills/managing-skill-activation`; take it from this file's own
path, never from the working directory or an environment variable. `<python>`
is `sh <plugin-root>/bin/python-launcher`.
Quote both paths and write them with forward slashes, also on Windows. If the
check exits 2 because session variables of two harnesses are set, rerun it once
with `P_SKILL_HARNESS` set to this session's harness (`claude`, `codex`, or
`antigravity`).

Activation is advisory on every harness. Each governed skill and command runs
that check first and stops when it exits 1 or 2, but no harness blocks a skill
that skips the step, and no hook enforces it.

Use the same controller for the requested operation:

- `status` or `status --json` reports the effective profile, source,
  overrides, and enabled, disabled, and limited components without local paths.
- `use home|work --global` changes the default for future sessions and sessions
  without an explicit selection.
- `enable|disable COMPONENT --session|--global` changes one component. Never
  guess the scope; use the scope the operator requested.
- `reset --session|--global` removes that scope's selection and overrides.
- `sync-native` optionally refreshes p-owned Codex catalog entries after a
  plugin update. It affects future-session visibility only. Do not run it unless
  the operator explicitly asks for catalog hiding; a current session cannot
  reload a skill removed at startup. Claude
  Code and Antigravity have no per-skill switch for plugin skills, so there a
  disabled skill stays listed and is stopped only by its own
  `skill-profile-ctl check` guard.
- `validate` checks the policy schema and exact source coverage.

`P_SKILL_PROFILE` is an environment lock above session and global state. If it
is active, report it rather than claiming a lower-precedence change is already
effective. Never edit installed plugin cache files.
