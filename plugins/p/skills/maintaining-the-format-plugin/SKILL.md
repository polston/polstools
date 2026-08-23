---
name: maintaining-the-format-plugin
description: Use when the p response format misbehaves — replies stop following FINDINGS/PROBLEMS/ASKS, injected text is missing or mangled, /p:fmt-off or /p:fmt-on seems stuck — or after editing its wiring or payloads.
---

# Maintaining the p response format

## Health check

1. Run `<plugin-root>/bin/format-e2e`. Exit 0 = the wiring, gates,
   toggle, and catalogs all check out mechanically. Exit 1 names the failing
   check.
2. The e2e cannot see live state. Check separately: is p enabled in
   the harness (it loads from the installed marketplace path, not from a repo
   worktree — a healthy repo copy proves nothing about live sessions)? Did
   this session actually receive the spec at start? Is there a stale off-flag
   for the current session (`format-ctl status`)? A missing injection with a
   passing e2e is almost always one of these three.

## Repairing — committed state is canonical

Diagnose direction before editing anything: `git status` + `git log` on the
plugin files. A working-tree or single-file oddity is damage until history
says otherwise.

1. Catalog drift (`plugin.json` vs the marketplace entry must stay
   byte-identical in description, version, keywords): git history shows which
   side changed — sync **from the unchanged side**. Syncing toward the edited
   side propagates the corruption while making the checker pass.
2. A payload/hooks.json filename mismatch: restore the committed name; never
   infer a rename's intent from the index.
3. After any repair, re-run `format-e2e` and expect every check green.

## Deliberate choices that look like bugs — do not "fix" these

| Looks wrong | Why it is right |
|---|---|
| `gate` exits 1 on a bad payload while the toggles exit 2 | Exit 2 from a UserPromptSubmit hook blocks processing and erases the user's prompt |
| Flag dir sits under the OS temp directory | Both harness sandboxes permit temp writes; config paths may be blocked or may themselves be repositories |
| stdin/stdout reconfigured to UTF-8 at the top of `format-ctl` | Piped stdout on Windows defaults to the ANSI code page and mangles non-ASCII payload bytes |
| Spec text duplicated in miniature in `turn-reminder.md` | Per-turn reinjection is the measured drift antidote; the repetition is the feature |

## Removing the response format for good

1. Remove the format hooks, commands, payloads, controller scripts, and this
   maintenance skill from p. Do not uninstall the whole plugin unless
   all of its other tools should disappear too.
2. Delete the `p-format-toggle` directory under the OS temp directory —
   orphaned flags can outlive the plugin until normal temp cleanup removes them.
3. Remove any instruction-file carve-out that defers other formatting rules
   to this feature (grep the user/global instructions for `format`).
4. Verify: a fresh session shows no FINDINGS/PROBLEMS/ASKS injection and no
   per-turn reminder.

## Common mistakes

- Syncing catalog drift toward the freshly-edited side (observed: an agent
  copied a corrupted description into the marketplace to make them "match").
- Treating an uncommitted rename as intent and rewiring hooks.json to follow
  it (observed), instead of restoring the committed name.
- Declaring the plugin healthy from file reads alone — only running the hook
  commands catches execution-level defects (the UTF-8 mangling was invisible
  in every file read and caught only by running the gate).
