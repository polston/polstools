---
name: aligning-statuslines
description: Use when checking, previewing, applying, repairing, or restoring the shared Claude Code and Codex CLI statusline profile. Keeps Claude's richer two-line renderer and Codex's supported native footer aligned without rewriting configuration at session start.
---

# Aligning status lines

Use the plugin's `bin/statusline-ctl`; never edit a user's whole settings file
or replace unrelated plugin configuration. Inspect only Claude's `statusLine`
field before recommending a change. Resolve the plugin root from this skill's
location, `PLUGIN_ROOT`, or `CLAUDE_PLUGIN_ROOT`. Resolve Python by trying
`python3`, `python`, `py -3`, then `uv run --no-project python`.

## Default run

When invoked without an explicit command, run `statusline-ctl sync`. It checks
first, repairs only recognized safe drift, and checks again. Do not ask the
operator to choose between the supported paths below. If configuration cannot
be read or Claude uses an unknown external renderer, it stops without changing
either settings file and reports the reason.

## Choose the Claude path

- If Claude directly invokes `ccstatusline`, preserve it and read
  [the ccstatusline compatibility guide](references/ccstatusline.md). Align the
  information and percent-left semantics without replacing its layout,
  colors, custom segments, or reset timers.
- If Claude has no status-line renderer, the bundled renderer is the fallback.
- If Claude uses another external renderer, preserve it. Explain the compatible
  fields and do not apply over it without a separate explicit replacement
  decision.

Before proposing a mutation, state what it adds, changes, removes, and
preserves. A missing category is `None`; do not make the user infer scope from
the command name.

## Commands

1. `sync` is the default: check, apply recognized safe drift, then verify. It is
   a no-op when already aligned.
2. `check` reports drift. Exit 0 means the bundled profile is aligned or a
   preserved ccstatusline provider has aligned Codex fields; 1 means drift, and
   2 means the configuration could not be read safely. Verify ccstatusline's
   custom-command semantics separately.
3. `preview` prints fixed representative Claude and Codex output. It does not
   read configuration, credentials, session history, or the working directory.
4. `apply` transactionally installs the fallback only when Claude has no
   renderer. With ccstatusline, it preserves Claude and changes only Codex
   `tui.status_line`. It stages every write and restores all earlier targets if
   any replacement fails. It refuses unknown external renderers and is idempotent.
5. `restore` restores only values changed by `apply`. If a managed value changed
   afterwards, it leaves that value untouched and exits 1.

Invoke `<python> <plugin-root>/bin/statusline-ctl <command>` with one command
from the list above. Rollback metadata stays in the platform-local state
directory outside repositories and contains only owned settings. The Claude
usage token is read by the renderer only for a short-lived request header; it
is never printed, copied, or cached.

## Invariants

- Claude remains a two-line ANSI display and retains any model-scoped weekly
  gauge already configured.
- Context and every quota are shown as percent left.
- Codex uses only its native footer identifiers, in profile order.
- Nothing runs automatically at session start. Apply and restore are explicit.
