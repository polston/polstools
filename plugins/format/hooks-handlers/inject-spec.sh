#!/bin/sh
# SessionStart: plain stdout is appended to the model's context.
# No exit 0: a missing payload must surface as a failed hook, not silence.
cat "${CLAUDE_PLUGIN_ROOT}/style/response-format.md"
