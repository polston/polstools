---
name: fmt-off
description: Disable the p response format for the current Claude Code or Codex session.
---

# Turn the response format off

1. Resolve `scripts/toggle.py` relative to the directory containing this
   `SKILL.md`; do not resolve it from the current working directory and do not
   depend on a plugin-root environment variable.
2. Run the script now.
3. If it exits 0, confirm in one line that formatting is off for this session,
   then reply normally for the rest of the session.
