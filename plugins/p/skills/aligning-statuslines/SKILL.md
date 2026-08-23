---
name: aligning-statuslines
description: Use when checking, previewing, applying, repairing, or restoring the shared Claude Code and Codex CLI statusline profile. Keeps Claude's richer two-line renderer and Codex's supported native footer aligned without rewriting configuration at session start.
---

# Aligning status lines

Use the plugin's `bin/statusline-ctl`; never edit a user's whole settings file
or replace unrelated plugin configuration. Resolve the plugin root from this
skill's location, `PLUGIN_ROOT`, or `CLAUDE_PLUGIN_ROOT`. Resolve Python by
trying `python3`, `python`, `py -3`, then `uv run --no-project python`.

## Commands

1. `check` reports drift. Exit 0 means aligned, 1 means drift, 2 means the
   configuration could not be read safely.
2. `preview` prints fixed representative Claude and Codex output. It does not
   read configuration, credentials, session history, or the working directory.
3. `apply` atomically installs the versioned Claude renderer and changes only
   Claude `statusLine` plus Codex `tui.status_line`. It is idempotent.
4. `restore` restores only the prior values of those two settings. If either
   value changed after apply, it leaves that value untouched and exits 1.

Invoke `<python> <plugin-root>/bin/statusline-ctl <command>` with one command
from the list above. Rollback metadata stays in the platform-local state
directory outside repositories and contains only owned settings. The Claude
usage token is read by the renderer only for a short-lived request header; it
is never printed, copied, or cached.

## Invariants

- Claude remains a two-line ANSI display and retains the model-scoped weekly
  gauge.
- Context and every quota are shown as percent left.
- Codex uses only its native footer identifiers, in profile order.
- Nothing runs automatically at session start. Apply and restore are explicit.
