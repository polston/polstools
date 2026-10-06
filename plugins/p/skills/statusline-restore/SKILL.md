---
name: statusline-restore
description: Use only when the operator asks to restore the statusline settings changed by the last apply; it edits Claude and Codex settings files.
---

# Restore aligned status lines

Before any other action, run
`<python> <plugin-root>/bin/skill-profile-ctl check statusline-restore`. If it
exits 1 or 2, stop and report its output. `<plugin-root>` is the absolute path
two directories above this `SKILL.md`, whose directory is
`<plugin-root>/skills/statusline-restore`; take it from this file's own path,
never from the working directory or an environment variable. `<python>` is `sh
<plugin-root>/bin/python-launcher`.

Run `<python> <plugin-root>/bin/statusline-ctl restore`. Report any setting deliberately left
untouched because it changed after apply.
