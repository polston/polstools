---
name: work
description: Select the p work skill profile for the current Claude Code or Codex session.
---

# Use the work skill profile

1. Before any other action, resolve the plugin root from this `SKILL.md` and
   run `<python> <plugin-root>/bin/skill-profile-ctl check work`. If it exits 1
   or 2, stop and report its output.
2. Resolve `scripts/toggle.py` relative to the directory containing this
   `SKILL.md`; do not resolve it from the current working directory or depend
   on a plugin-root environment variable.
3. Run the script now with no arguments.
4. If it exits 0, confirm in one line that the work profile (`p:w`) is active
   for this session.
