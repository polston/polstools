#!/bin/sh
# UserPromptSubmit: plain stdout is appended to the model's context each turn.
# No exit 0: a missing payload must surface as a failed hook, not silence.
cat "${CLAUDE_PLUGIN_ROOT}/style/turn-reminder.md"
