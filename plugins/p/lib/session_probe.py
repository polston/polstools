"""Names-only evidence about a harness session.

Reads which session-id and plugin-root variables a process received, and
arms, reads, and removes the Antigravity hook's trace. Nothing here stores or
prints a value: only variable names, JSON key names, small integers, and
the on/off/error state.

The trace lives in format-ctl's per-user state directory (one definition:
`format-ctl probe-paths`), which must be a real directory owned by this user
with no group or other access. Arming creates the arming marker beside the
format defaults file and an empty trace file `probe.log` (mode 0600,
create-exclusive, never through a link). bin/agy-format-hook appends to that
file only while the marker exists and only after re-checking the directory.
Reading the trace removes the file and the marker, so a trace never
outlives one probe run.

Trace lines, tab-separated after the record type and key (the key is the
conversation id cut to its leading [A-Za-z0-9._-] run, or "no-session"):
  N <key> <stdin key names> <harness variable names>   first run of a key
  R <key> <invocationNum> <initialNumSteps> <on|off|error>
"""

import os
from pathlib import Path
import re
import stat
import subprocess
import sys


SESSION_VARIABLES = (
    "CLAUDE_CODE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "ANTIGRAVITY_CONVERSATION_ID",
)
ROOT_VARIABLES = ("CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT")
SAFE_ID = re.compile(r"[A-Za-z0-9._-]+")
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
FORMAT_CTL = Path(__file__).resolve().parent.parent / "bin" / "format-ctl"
TRACE_NAME = "probe.log"
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class ProbeError(Exception):
    """The trace cannot be armed or read safely."""


def present_names(env=None):
    """Sorted names of non-empty harness-related variables; never values."""
    env = os.environ if env is None else env
    return sorted(name for name, value in env.items()
                  if value and NAME_PATTERN.search(name))


def session_variable(env=None):
    """The first recognized session variable name that is set, or ""."""
    env = os.environ if env is None else env
    return next((name for name in SESSION_VARIABLES if env.get(name)), "")


def trace_key(session_id):
    """The hook keeps only the id's leading [A-Za-z0-9._-] run."""
    match = SAFE_ID.match(session_id or "")
    return match.group(0) if match else "no-session"


def probe_paths(create, env=None):
    """(state directory, arming marker) from format-ctl, the one definition."""
    completed = subprocess.run(
        [sys.executable, "-B", str(FORMAT_CTL), "probe-paths"]
        + (["--create"] if create else []),
        text=True, encoding="utf-8", capture_output=True,
        env=dict(os.environ if env is None else env))
    lines = completed.stdout.splitlines()
    if completed.returncode != 0 or len(lines) != 2:
        raise ProbeError("the per-user state directory is not private")
    return Path(lines[0]), Path(lines[1])


def require_private(directory):
    info = os.lstat(directory)
    if not stat.S_ISDIR(info.st_mode):
        raise ProbeError("the state directory is not a real directory")
    if hasattr(os, "getuid"):
        if info.st_uid != os.getuid():
            raise ProbeError("the state directory is not owned by this user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise ProbeError("the state directory is accessible to other users")


def _remove(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def arm(env=None):
    directory, marker = probe_paths(create=True, env=env)
    require_private(directory)
    trace = directory / TRACE_NAME
    _remove(trace)
    descriptor = os.open(
        trace, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600)
    os.close(descriptor)
    marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _remove(marker)
    descriptor = os.open(
        marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600)
    os.close(descriptor)


def consume(env=None):
    """Read and remove the trace; {key: record}. Disarms the probe."""
    try:
        directory, marker = probe_paths(create=False, env=env)
    except ProbeError:
        return {}
    trace = directory / TRACE_NAME
    records = {}
    try:
        require_private(directory)
    except (OSError, ProbeError):
        _remove(marker)
        return {}
    try:
        descriptor = os.open(trace, os.O_RDONLY | NOFOLLOW)
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeError):
        lines = []
    for line in lines:
        kind, _, rest = line.partition(" ")
        fields = rest.split("\t")
        record = records.setdefault(fields[0], {
            "invocations": [], "gate": "", "stdin_keys": [], "env_names": []})
        if kind == "N" and len(fields) == 3:
            record["stdin_keys"] = fields[1].split()
            record["env_names"] = fields[2].split()
        elif kind == "R" and len(fields) == 2:
            parts = fields[1].split()
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                record["invocations"].append([int(parts[0]), int(parts[1])])
                record["gate"] = parts[2]
    _remove(trace)
    _remove(marker)
    return {key: value for key, value in records.items() if value["invocations"]}
