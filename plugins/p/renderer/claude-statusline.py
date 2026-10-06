#!/usr/bin/env python3
"""Claude Code statusLine renderer for the aligned-v1 profile.

Line 1: model | effort | cwd | git branch | context left | profile label
Line 2: five-hour, weekly and model-scoped weekly quota left

The render path reads stdin, the local usage cache and git only. It never reads
a credential or calls the network: `--update-cache` is the detached refresh
child that does both. claude-statusline.ps1 meets the same output contract on
Windows; tests/fixtures/statusline-contract.json is that contract.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
for _path in (str(HERE), str(HERE.parent / "lib")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
FRESH_MS = 60_000
SHOW_MS = 900_000
ATTEMPT_MS = 30_000
LOCK_STALE_S = 60
SCOPED_FALLBACK_LABEL = "scoped"

RESET = "\x1b[0m"
DIM = "\x1b[2m"
CYAN = "\x1b[2;36m"
YELLOW = "\x1b[2;33m"
MAGENTA = "\x1b[35m"
RED = "\x1b[31m"
AMBER = "\x1b[33m"
GREEN = "\x1b[32m"


def as_object(value):
    return value if isinstance(value, dict) else {}


def strip_controls(text):
    """Remove C0 controls, DEL and C1 controls: nothing from outside the
    renderer may reach the terminal as an escape sequence."""
    return "".join(ch for ch in text if not (ord(ch) < 32 or 127 <= ord(ch) <= 159))


def as_text(value):
    return strip_controls(value).strip() if isinstance(value, str) else ""


def as_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def clamp(value):
    return max(0.0, min(100.0, value))


def percent_text(left):
    return str(int(math.floor(left))) + "% left"


def bar_text(left):
    filled = int(math.floor(left / 10 + 0.5))
    return "█" * filled + "░" * (10 - filled)


def tone(left):
    used = 100 - left
    return RED if used >= 80 else AMBER if used >= 60 else GREEN


def token_text(count):
    if count >= 1_000_000:
        tenths = int(math.floor(count / 100_000 + 0.5))
        whole, part = divmod(tenths, 10)
        return (str(whole) if part == 0 else "%d.%d" % (whole, part)) + "M"
    return str(int(math.floor(count / 1000 + 0.5))) + "k"


def shorten_home(cwd, home, windows=False):
    if not cwd or not home:
        return cwd
    home = home.rstrip("/\\") or home
    probe, base = (cwd.lower(), home.lower()) if windows else (cwd, home)
    if probe == base:
        return "~"
    if probe.startswith(base) and probe[len(base)] in "/\\":
        return "~" + cwd[len(home):]
    return cwd


def _segment(key, plain, painted):
    return {"key": key, "plain": plain, "painted": painted}


def _gauge(key, label, left, tokens, color, label_paint):
    bar = bar_text(left)
    pct = percent_text(left)
    head = (label + " ") if label else ""
    suffix = (" " + tokens) if tokens else ""
    painted = head + bar + " " + pct + suffix
    if color:
        painted_head = (label_paint + label + RESET + " ") if label else ""
        painted_suffix = (" " + DIM + tokens + RESET) if tokens else ""
        painted = painted_head + tone(left) + bar + RESET + " " + pct + painted_suffix
    return _segment(key, head + bar + " " + pct + suffix, painted)


def _separator(color):
    return " " + (DIM + "|" + RESET if color else "|") + " "


def join_line(segments, color):
    return _separator(color).join(segment["painted"] for segment in segments)


def render_lines(data, *, profile_label, scoped=None, home="", windows=False,
                 branch="", color=True):
    """Pure renderer: every input is an argument; returns the printed lines.

    scoped is None (no model-scoped gauge), ("gauge", label, used_percent), or
    ("unavailable", label) for a cold or expired cache.
    """
    data = as_object(data)
    profile_label = as_text(profile_label)
    home = as_text(home)
    if scoped:
        scoped = (scoped[0], as_text(scoped[1])) + tuple(scoped[2:])
    paint = (lambda code, text: code + text + RESET) if color else (lambda code, text: text)
    workspace = as_object(data.get("workspace"))
    context = as_object(data.get("context_window"))

    line1 = []
    model = as_text(as_object(data.get("model")).get("display_name"))
    if model:
        line1.append(_segment("model", model, paint(CYAN, model)))
    effort = as_text(as_object(data.get("effort")).get("level"))
    if effort:
        line1.append(_segment("effort", "eff " + effort, paint(DIM, "eff") + " " + paint(MAGENTA, effort)))
    cwd = shorten_home(as_text(workspace.get("current_dir")) or as_text(data.get("cwd")), home, windows)
    if cwd:
        line1.append(_segment("cwd", cwd, paint(DIM, cwd)))
    branch = as_text(workspace.get("git_branch")) or as_text(branch)
    if branch:
        line1.append(_segment("branch", branch, paint(YELLOW, branch)))
    remaining = as_number(context.get("remaining_percentage"))
    if remaining is not None:
        size = as_number(context.get("context_window_size"))
        used = as_number(context.get("total_input_tokens"))
        tokens = ""
        if size and size > 0 and used is not None and used >= 0:
            tokens = token_text(used) + "/" + token_text(size)
        line1.append(_gauge("context", "", clamp(remaining), tokens, color, DIM))
    line1.append(_segment("profile", profile_label, paint(DIM, profile_label)))

    line2 = []
    limits = as_object(data.get("rate_limits"))
    for key, label, field in (("5h", "5h", "five_hour"), ("wk", "wk", "seven_day")):
        used = as_number(as_object(limits.get(field)).get("used_percentage"))
        if used is not None:
            line2.append(_gauge(key, label, 100 - clamp(used), "", color, DIM))
    if line2 and scoped:
        if scoped[0] == "gauge":
            line2.append(_gauge("scoped", scoped[1], 100 - clamp(scoped[2]), "", color, DIM))
        else:
            text = (scoped[1] or SCOPED_FALLBACK_LABEL) + " --"
            line2.append(_segment("scoped", text, paint(DIM, text)))

    lines = [join_line(line1, color)]
    if line2:
        lines.append(join_line(line2, color))
    return lines


def cache_dir():
    for name in ("LOCALAPPDATA", "XDG_CACHE_HOME"):
        if os.environ.get(name):
            return Path(os.environ[name]) / "claude-statusline"
    return Path(tempfile.gettempdir()) / "claude-statusline"


def private_cache_dir(create=False):
    """The cache directory, or None when it cannot be trusted.

    It must be a real directory (not a symlink) owned by this user with no
    group or world access. A directory that fails is left untouched: no mode
    repair, no cache I/O. Same lstat rule as skill_activation._private_dir, but
    that helper tightens a looser mode where a shared cache must refuse it.
    """
    directory = cache_dir()
    try:
        if create:
            directory.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.mkdir(str(directory), 0o700)
            except FileExistsError:
                pass
        info = os.lstat(str(directory))
    except OSError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        return None
    if hasattr(os, "getuid") and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        return None
    return directory


NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _read_cache_file(path):
    try:
        fd = os.open(str(path), os.O_RDONLY | NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, UnicodeError):
        return None


def _write_private(path, text, exclusive=False):
    """Create or truncate path 0600 without following a link at path."""
    flags = os.O_WRONLY | os.O_CREAT | NOFOLLOW | (os.O_EXCL if exclusive else os.O_TRUNC)
    with os.fdopen(os.open(str(path), flags, 0o600), "w", encoding="utf-8") as handle:
        handle.write(text)


def _read_json(path):
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, UnicodeError):
        return None


def scoped_state(cache, now_ms):
    """Map the cache to a render state and whether a refresh is due."""
    cache = as_object(cache)
    at = as_number(cache.get("at"))
    age = None if at is None else now_ms - at
    label = as_text(cache.get("label"))
    percent = as_number(cache.get("percent"))
    refresh_due = age is None or age > FRESH_MS
    if age is not None and age <= SHOW_MS:
        if label and percent is not None:
            return ("gauge", label, percent), refresh_due
        if not label:
            return None, refresh_due
    return ("unavailable", label), refresh_due


def _schedule_refresh(directory, now_ms):
    attempt = directory / "usage-attempt.txt"
    try:
        fd = os.open(str(attempt), os.O_RDONLY | NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as handle:
            last = int(handle.read().strip())
    except (OSError, ValueError, UnicodeError):
        last = 0
    if now_ms - last <= ATTEMPT_MS:
        return False
    try:
        _write_private(attempt, str(now_ms))
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--update-cache"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        return False


def _git_branch(cwd):
    path = Path(os.path.expanduser(cwd)) if cwd else None
    if not path or not path.is_dir():
        return ""
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(path), capture_output=True, text=True, timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return as_text(proc.stdout) if proc.returncode == 0 else ""


def profile_label(session_id):
    try:
        import skill_activation

        policy_path = HERE / "skill-activation-v1.json"
        if not policy_path.is_file():
            policy_path = HERE.parent / "profiles" / "skill-activation-v1.json"
        policy = skill_activation.load_manifest(policy_path)
        resolved = skill_activation.resolve(policy, session_id=session_id)
        return skill_activation.label(resolved)
    except Exception:
        return "p:?"


def render(raw, now_ms=None):
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    data = as_object(data)
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    workspace = as_object(data.get("workspace"))
    cwd = as_text(workspace.get("current_dir")) or as_text(data.get("cwd"))
    branch = "" if as_text(workspace.get("git_branch")) else _git_branch(cwd)
    session_id = data.get("session_id") if isinstance(data.get("session_id"), str) else None

    scoped = None
    if as_object(data.get("rate_limits")):
        directory = private_cache_dir(create=True)
        if directory is not None:
            scoped, refresh_due = scoped_state(_read_cache_file(directory / "usage-cache.json"), now_ms)
            if refresh_due:
                _schedule_refresh(directory, now_ms)

    return render_lines(
        data,
        profile_label=profile_label(session_id),
        scoped=scoped,
        home=as_text(os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""),
        windows=os.name == "nt",
        branch=branch,
    )


def read_credential(platform=None):
    """Return the stored Claude OAuth credential object, or None.

    The credential file is read first. On macOS the credential lives in the
    login keychain; it is read only here, in the detached refresh child, with a
    timeout and no terminal attached.
    """
    platform = sys.platform if platform is None else platform
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
    value = _read_json(Path(config) / ".credentials.json" if config else Path(home) / ".claude" / ".credentials.json")
    if isinstance(value, dict):
        return value
    if platform != "darwin":
        return None
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
        )
        value = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _acquire_lock(directory):
    lock = directory / "refresh.lock"
    for _ in range(2):
        try:
            os.close(os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY | NOFOLLOW, 0o600))
            return lock
        except FileExistsError:
            try:
                if time.time() - os.lstat(str(lock)).st_mtime <= LOCK_STALE_S:
                    return None
                lock.unlink()
            except OSError:
                return None
        except OSError:
            return None
    return None


def update_cache(now_ms=None, opener=None):
    """Detached refresh: fetch the model-scoped weekly limit into the cache."""
    directory = private_cache_dir(create=True)
    if directory is None:
        return 2
    lock = _acquire_lock(directory)
    if lock is None:
        return 0
    try:
        oauth = as_object(as_object(read_credential()).get("claudeAiOauth"))
        token = as_text(oauth.get("accessToken"))
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        expires = as_number(oauth.get("expiresAt"))
        if not token or expires is None or expires <= now_ms:
            return 1
        import urllib.request

        request = urllib.request.Request(
            USAGE_URL,
            headers={
                "Authorization": "Bearer " + token,
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "polstools-statusline",
            },
        )
        with (opener or urllib.request.urlopen)(request, timeout=3) as response:
            usage = json.loads(response.read().decode("utf-8"))
        fresh = {"at": now_ms, "label": "", "percent": None}
        for item in as_object(usage).get("limits") or []:
            item = as_object(item)
            percent = as_number(item.get("percent"))
            if item.get("kind") == "weekly_scoped" and percent is not None:
                model = as_object(as_object(item.get("scope")).get("model"))
                fresh = {"at": now_ms, "label": as_text(model.get("display_name")).lower(), "percent": percent}
                break
        temporary = directory / "usage-cache.json.tmp"
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        _write_private(temporary, json.dumps(fresh), exclusive=True)
        os.replace(temporary, directory / "usage-cache.json")
        return 0
    except Exception:
        return 1
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--update-cache"]:
        update_cache()
        return 0
    try:
        raw = sys.stdin.read()
    except (OSError, UnicodeError):
        raw = ""
    try:
        lines = render(raw)
    except Exception:
        lines = ["p:?"]
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
