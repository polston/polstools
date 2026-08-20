---
description: Disable the response format for this session
allowed-tools: Bash(python "${CLAUDE_PLUGIN_ROOT}/bin/format-ctl" *)
---

Run `python "${CLAUDE_PLUGIN_ROOT}/bin/format-ctl" off` now. Then confirm to
the user in one line that the format is off for this session, and for the rest
of this session reply normally — the response-format spec and its per-turn
reminders no longer apply.
