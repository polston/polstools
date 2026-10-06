#!/usr/bin/env python3
"""Antigravity CLI statusline renderer for p.

Antigravity pipes its state JSON (model, workspace, vcs, context_window,
quota, conversation_id, terminal_width, ...) to this command and renders
stdout, ANSI colour included. Line one is the Claude renderer's own line
(render_lines) fed the mapped payload; line two is one gauge per quota
bucket, drawn with the same gauge and fitting rules. Runs from the installed
plugin directory, whose path is stable across reinstalls; it reads stdin and
the activation policy only and writes nothing. Set NO_COLOR to suppress
colour. Exit: always 0; a failure prints "p:?".
"""

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
for _stream in (sys.stdin, sys.stdout):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent


def load_claude_renderer():
    loader = importlib.machinery.SourceFileLoader(
        "p_claude_statusline", str(HERE / "claude-statusline.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def to_claude_shape(data):
    workspace = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    vcs = data.get("vcs") if isinstance(data.get("vcs"), dict) else {}
    context = data.get("context_window") if isinstance(data.get("context_window"), dict) else {}
    return {
        "model": data.get("model") if isinstance(data.get("model"), dict) else {},
        "cwd": data.get("cwd") or "",
        "workspace": {
            "current_dir": workspace.get("current_dir") or "",
            "git_branch": vcs.get("branch") or "",
        },
        "context_window": {
            key: context.get(key) for key in
            ("remaining_percentage", "context_window_size", "total_input_tokens")
        },
    }


def quota_buckets(data):
    quota = data.get("quota") if isinstance(data.get("quota"), dict) else {}
    buckets = []
    for name, value in sorted(quota.items()):
        fraction = value.get("remaining_fraction") if isinstance(value, dict) else None
        if isinstance(fraction, (int, float)) and not isinstance(fraction, bool):
            buckets.append((str(name), 100 * max(0.0, min(1.0, float(fraction)))))
    return buckets


def render_lines(data, renderer, *, profile_label, home="", color=True):
    columns = data.get("terminal_width")
    columns = columns if isinstance(columns, int) and columns > 0 else None
    lines = renderer.render_lines(
        to_claude_shape(data), profile_label=profile_label, home=home,
        windows=os.name == "nt", columns=columns, color=color)
    gauges = [renderer._gauge("quota-" + name, name, left, "", color, renderer.DIM)
              for name, left in ((renderer.as_text(n), v) for n, v in quota_buckets(data))
              if name]
    if gauges:
        lines.append(renderer.fit_line(gauges, (("bars", None),), columns, color))
    return lines


def main():
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            data = {}
    except (ValueError, OSError, UnicodeError):
        data = {}
    try:
        renderer = load_claude_renderer()
        home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
        label = renderer.profile_label(data.get("conversation_id") or data.get("session_id"))
        lines = render_lines(data, renderer, profile_label=label, home=home,
                             color="NO_COLOR" not in os.environ)
        sys.stdout.write("\n".join(lines) + "\n")
    except Exception:
        sys.stdout.write("p:?\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
