---
name: aligning-statuslines
description: Use when checking, previewing, applying, repairing, or restoring the shared Claude Code and Codex CLI statusline profile. Keeps Claude's richer two-line renderer and Codex's supported native footer aligned without rewriting configuration at session start.
---

# Aligning status lines

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check aligning-statuslines`. If it
exits 1 or 2, stop and report its output.

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
  colors, custom segments, or reset timers. The only added segment is the
  tagged `p:h`/`p:w` custom-command widget owned by this plugin.
- If Claude has no status-line renderer, the bundled renderer is the fallback.
- If Claude runs a command an earlier p release wrote (an interpreter running
  the installed renderer copy), it is p's own: `sync` replaces it with the
  current command and keeps the original pre-p value for `restore`.
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
   2 means the configuration could not be read safely. It names each stale
   installed bundle file and ends with `repair with: statusline-ctl sync` when
   `sync` can repair the drift. It verifies the owned profile widget; verify
   unrelated ccstatusline custom commands separately.
3. `preview` prints fixed representative input through the shipped renderer
   and the profile's Codex identifiers. It does not read configuration,
   credentials, session history, or the working directory.
4. `apply` transactionally installs the fallback only when Claude has no
   renderer. With ccstatusline, it preserves the renderer and all unrelated
   settings while adding or refreshing one owned profile widget. It stages
   every write and restores all earlier targets if any replacement fails. It
   refuses unknown external renderers and is idempotent.
5. `profile-sync` refreshes only the stable profile-label bundle and its owned
   Claude integration. The `$p:home` and `$p:work` skills call it after a
   successful session switch; indicator failure does not undo the profile.
6. `restore` restores only values changed by `apply`. If a managed value changed
   afterwards, it leaves that value untouched and exits 1.

Invoke `<python> <plugin-root>/bin/statusline-ctl <command>` with one command
from the list above. Rollback metadata stays in the platform-local state
directory outside repositories and contains only owned settings. A render never
reads a credential or the network. The model-scoped weekly value is fetched by
a detached refresh child that reads the credential file, or on macOS the login
keychain, uses the token for one request header, and caches only the label,
percentage, and fetch time; the token is never printed, copied, or cached. The
background usage refresh can be switched off by setting `P_STATUSLINE_NO_REFRESH`
to any non-empty value.

## Invariants

- The bundled Claude renderer remains a two-line ANSI display and retains its
  model-scoped weekly gauge; ccstatusline retains its configured row layout.
- The Python renderer (macOS, Linux) and the PowerShell renderer (Windows)
  meet one output contract, `tests/fixtures/statusline-contract.json`: colour
  even when stdout is piped, whole percentages rounded down, segments dropped
  in a fixed order to fit `COLUMNS`, and a dim `--` marker when the
  model-scoped value is cold or expired.
- Context and every quota are shown as percent left.
- Claude shows the effective profile as `p:h` or `p:w`; an invalid policy shows
  `p:?` without blanking the rest of the status line.
- Codex uses only its native footer identifiers, in profile order.
- Nothing runs automatically at session start. Apply and restore are explicit.
