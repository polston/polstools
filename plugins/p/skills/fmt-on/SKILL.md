---
name: fmt-on
description: Enable the p response format for the current Claude Code or Codex session, or make it the default globally or for one harness.
---

# Turn the response format on

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check fmt-on`. If it exits 1 or
2, stop and report its output.

1. Resolve `scripts/toggle.py` relative to the directory containing this
   `SKILL.md`; do not resolve it from the current working directory and do not
   depend on a plugin-root environment variable.
2. Pick the scope from the request. Nothing named, or "session": run the
   script with no arguments. "default" or "globally": run the script with the
   single argument `default`. A named harness: run the script with
   `default claude`, `default codex`, or `default antigravity`.
3. If it exits 0, confirm in one line what its output says changed — the
   session state or the written default — and resume the response format with
   that reply when this session's format is on.
4. If it refuses because session variables of two harnesses are set,
   rerun it once with `P_FORMAT_HARNESS` set to the harness this session runs in
   (`claude`, `codex`, or `antigravity`).
5. On Antigravity, if it exits 2 because no session id reached the agent's
   shell, say so in one line and offer `default antigravity`, which applies to
   every Antigravity session.
