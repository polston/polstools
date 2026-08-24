---
name: managing-skill-activation
description: Use when checking or changing the p home/work default, reviewing disabled skills, overriding one skill, or repairing Codex skill-catalog visibility.
---

# Manage p skill activation

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check managing-skill-activation`.
If it exits 1 or 2, stop and report its output.

Use the same controller for the requested operation:

- `status` or `status --json` reports the effective profile, source,
  overrides, and enabled, disabled, and limited components without local paths.
- `use home|work --global` changes the default for future sessions and sessions
  without an explicit selection.
- `enable|disable COMPONENT --session|--global` changes one component. Never
  guess the scope; use the scope the operator requested.
- `reset --session|--global` removes that scope's selection and overrides.
- `sync-native` optionally refreshes p-owned Codex catalog entries after a
  plugin update. It affects future-session visibility and is not required for
  enforcement. Do not run it unless the operator explicitly asks for catalog
  hiding; a current session cannot reload a skill removed at startup.
- `validate` checks the policy schema and exact source coverage.

`P_SKILL_PROFILE` is an environment lock above session and global state. If it
is active, report it rather than claiming a lower-precedence change is already
effective. Never edit installed plugin cache files.
