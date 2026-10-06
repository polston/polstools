---
name: work
description: Select the p work skill profile for the current Claude Code or Codex session.
---

# Use the work skill profile

1. Before any other action, run
   `<python> <plugin-root>/bin/skill-profile-ctl check work`. If it exits 1 or
   2, stop and report its output. `<plugin-root>` is the absolute path two
   directories above this `SKILL.md`, whose directory is
   `<plugin-root>/skills/work`; take it from this file's own path, never from
   the working directory or an environment variable. `<python>` is `sh
   <plugin-root>/bin/python-launcher`.
2. Resolve `scripts/toggle.py` relative to the directory containing this
   `SKILL.md`; do not resolve it from the current working directory or depend
   on a plugin-root environment variable.
3. Run the script now with no arguments.
4. If it exits 0, confirm in one line that the work profile (`p:w`) is active
   for this session.
5. If it refuses because session variables of two harnesses are set,
   rerun it once with `P_SKILL_HARNESS` set to the harness this session runs in
   (`claude`, `codex`, or `antigravity`).
6. On Antigravity, if it exits 2 because no session id reached the agent's
   shell, say so in one line and offer `use work --global`, which applies to
   every session.
