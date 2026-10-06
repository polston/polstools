#!/usr/bin/env python3
"""Claude Code statusLine renderer for the aligned-v1 profile.

Line 1: model + effort | cwd | git branch | context remaining | profile label
Line 2: five-hour + weekly + model-scoped weekly quota remaining
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
lib_path = str(HERE.parent / "lib")
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)


def percent_left(used):
    if used is None:
        return None
    return 100 - max(0, min(100, float(used)))


def _display_percent(value):
    value = float(value)
    return str(int(round(value))) if value == round(value) else ("%.1f" % value).rstrip("0").rstrip(".")


def _bar(left, color=True):
    slots = 10
    bounded = max(0, min(100, float(left)))
    filled = int(round(bounded / 100 * slots))
    body = "█" * filled + "░" * (slots - filled)
    if not color:
        return body
    used = 100 - bounded
    tone = "\x1b[31m" if used >= 80 else "\x1b[33m" if used >= 60 else "\x1b[32m"
    return tone + body + "\x1b[0m"


def _format_token_count(n):
    if n >= 1_000_000:
        val = n / 1_000_000
        formatted = f"{val:.1f}"
        if formatted.endswith(".0"):
            formatted = formatted[:-2]
        return f"{formatted}M"
    return f"{int(round(n / 1000))}k"


def _cache_dir():
    if os.environ.get("LOCALAPPDATA"):
        base = os.environ["LOCALAPPDATA"]
    elif os.environ.get("XDG_CACHE_HOME"):
        base = os.environ["XDG_CACHE_HOME"]
    else:
        base = tempfile.gettempdir()
    return Path(base) / "claude-statusline"


def _get_claude_credential():
    """Read credential from file; never trigger interactive UI."""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
    cred_file = Path(home) / ".claude" / ".credentials.json"
    if cred_file.is_file():
        try:
            with cred_file.open(encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _background_cache_refresh():
    """Fetch usage from Anthropic OAuth API in background without blocking render."""
    try:
        cred = _get_claude_credential()
        if not cred or not isinstance(cred, dict):
            return
        oauth = cred.get("claudeAiOauth") or {}
        token = oauth.get("accessToken")
        expires_at = oauth.get("expiresAt") or 0
        now_ms = int(time.time() * 1000)
        if not token or expires_at <= now_ms:
            return

        import urllib.request
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "polstools-statusline",
            },
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        limits = data.get("limits") or []
        scoped = [x for x in limits if x.get("kind") == "weekly_scoped" and x.get("percent") is not None]
        fresh = {"at": now_ms, "label": "", "percent": None}
        if scoped:
            model_info = (scoped[0].get("scope") or {}).get("model") or {}
            fresh["label"] = str(model_info.get("display_name") or "").lower()
            fresh["percent"] = float(scoped[0]["percent"])

        cdir = _cache_dir()
        cdir.mkdir(parents=True, exist_ok=True)
        cpath = cdir / "usage-cache.json"
        tmp_path = cdir / "usage-cache.json.tmp"
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(fresh, f)
        tmp_path.replace(cpath)
    except Exception:
        pass


def render():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}

    color = sys.stdout.isatty() or os.environ.get("CLICOLOR_FORCE") == "1"
    esc = "\x1b["
    reset = esc + "0m" if color else ""
    dim = esc + "2m" if color else ""
    cyan = esc + "2;36m" if color else ""
    yellow = esc + "2;33m" if color else ""
    magenta = esc + "35m" if color else ""
    sep = " " + dim + "|" + reset + " "

    # Line 1: model, effort, cwd, branch, remaining context, profile label
    model = ((data.get("model") or {}).get("display_name") or "").strip()
    effort = ((data.get("effort") or {}).get("level") or "").strip()
    workspace = data.get("workspace") or {}
    cwd = (workspace.get("current_dir") or data.get("cwd") or "").strip()
    if cwd:
        home = os.environ.get("HOME") or str(Path.home())
        if cwd == home or cwd.startswith(home + os.sep) or cwd.startswith(home + "/"):
            cwd = "~" + cwd[len(home):]

    branch = (workspace.get("git_branch") or "").strip()
    if not branch and cwd:
        expanded_cwd = Path(os.path.expanduser(cwd))
        if expanded_cwd.is_dir():
            try:
                proc = subprocess.run(
                    ["git", "--no-optional-locks", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=str(expanded_cwd),
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    branch = proc.stdout.strip()
            except Exception:
                pass

    remaining = (data.get("context_window") or {}).get("remaining_percentage")

    parts = []
    if model:
        parts.append(cyan + model + reset)
    if effort:
        parts.append(dim + "eff" + reset + " " + magenta + effort + reset)
    if cwd:
        parts.append(dim + cwd + reset)
    if branch:
        parts.append(yellow + branch + reset)
    if remaining is not None:
        left = max(0, min(100, float(remaining)))
        tokens = ""
        size = (data.get("context_window") or {}).get("context_window_size")
        used = (data.get("context_window") or {}).get("total_input_tokens")
        if size and used is not None:
            tokens = f" {dim}{_format_token_count(used)}/{_format_token_count(size)}{reset}"
        parts.append(_bar(left, color) + " " + _display_percent(left) + "% left" + tokens)

    # In-memory profile label resolution
    profile_label = "p:?"
    try:
        import skill_activation
        session_id = data.get("session_id")
        policy_path = HERE / "skill-activation-v1.json"
        if not policy_path.is_file():
            policy_path = HERE.parent / "profiles" / "skill-activation-v1.json"
        if policy_path.is_file():
            policy = skill_activation.load_manifest(policy_path)
            resolved = skill_activation.resolve(policy, session_id=session_id)
            profile_label = skill_activation.label(resolved)
    except Exception:
        profile_label = "p:?"
    parts.append(dim + profile_label + reset)

    # Line 2: Rate limits / Quotas
    limits = []
    rate_limits = data.get("rate_limits") or {}
    for label, key in (("5h", "five_hour"), ("wk", "seven_day")):
        used = (rate_limits.get(key) or {}).get("used_percentage")
        left = percent_left(used)
        if left is not None:
            limits.append(
                dim + label + reset + " " + _bar(left, color) + " "
                + _display_percent(left) + "% left"
            )

    # Model-scoped weekly quota: non-blocking cache read
    cdir = _cache_dir()
    cpath = cdir / "usage-cache.json"
    attempt_path = cdir / "usage-attempt.txt"
    now_ms = int(time.time() * 1000)
    cache = None
    if cpath.is_file():
        try:
            with cpath.open(encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = None

    last_attempt = 0
    if attempt_path.is_file():
        try:
            last_attempt = int(attempt_path.read_text(encoding="utf-8").strip())
        except Exception:
            last_attempt = 0

    # Read from cache if valid (< 15 min)
    if cache and cache.get("label") and cache.get("percent") is not None:
        if (now_ms - cache.get("at", 0)) <= 900_000:
            scoped_used = max(0, min(100, float(cache["percent"])))
            scoped_left = 100 - scoped_used
            scoped_label = str(cache["label"]).strip()
            limits.append(
                dim + scoped_label + reset + " " + _bar(scoped_left, color) + " "
                + _display_percent(scoped_left) + "% left"
            )

    # Schedule non-blocking background refresh if needed
    if (not cache or (now_ms - cache.get("at", 0)) > 60_000) and (now_ms - last_attempt) > 30_000:
        try:
            cdir.mkdir(parents=True, exist_ok=True)
            attempt_path.write_text(str(now_ms), encoding="utf-8")
            # Detached spawn without blocking
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--update-cache"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            pass

    if parts:
        print(sep.join(parts))
    if limits:
        print(sep.join(limits))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--update-cache":
        _background_cache_refresh()
        return 0
    render()
    return 0


if __name__ == "__main__":
    sys.exit(main())
