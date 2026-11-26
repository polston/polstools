"""Names-only inspection of harness session and plugin-root variables."""

import os
import re


SESSION_VARIABLES = (
    "CLAUDE_CODE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "ANTIGRAVITY_CONVERSATION_ID",
)
ROOT_VARIABLES = ("CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT")
HARNESS_BY_VARIABLE = {
    "CLAUDE_CODE_SESSION_ID": "claude",
    "CODEX_THREAD_ID": "codex",
    "CODEX_SESSION_ID": "codex",
    "ANTIGRAVITY_CONVERSATION_ID": "antigravity",
}
NAME_PATTERN = re.compile(
    r"^(ANTIGRAVITY|AGY|CODEX|CLAUDE)_|PLUGIN_ROOT|PLUGIN_DATA"
    r"|SESSION_ID|THREAD_ID|CONVERSATION_ID"
)


def present_names(env=None):
    """Sorted names of non-empty harness-related variables; never values."""
    env = os.environ if env is None else env
    return sorted(name for name, value in env.items()
                  if value and NAME_PATTERN.search(name))


def session_variable(env=None):
    """The first recognized session variable name that is set, or ""."""
    env = os.environ if env is None else env
    return next((name for name in SESSION_VARIABLES if env.get(name)), "")
