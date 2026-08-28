---
name: fmt-off
description: Disable the p response format for the current Claude Code or Codex session, or make off the default globally or for one harness.
---

# Turn the response format off

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check fmt-off`. If it exits 1 or
2, stop and report its output.

1. Resolve `scripts/toggle.py` relative to the directory containing this
   `SKILL.md`; do not resolve it from the current working directory and do not
   depend on a plugin-root environment variable.
2. Pick the scope from the request. Nothing named, or "session": run the
   script with no arguments. "default" or "globally": run the script with the
   single argument `default`. A named harness: run the script with
   `default claude` or `default codex`.
3. If it exits 0, confirm in one line what its output says changed — the
   session state or the written default — and reply normally for the rest of
   the session when this session's format is off.
