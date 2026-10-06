---
name: statusline-preview
description: Preview representative aligned Claude and Codex status lines.
---

# Preview aligned status lines

Claude Code and Codex only. An Antigravity plugin cannot register a status
line; Antigravity changes its status line only through its own `/statusline`
command, and the `aligning-statuslines` skill explains how to check it.

Before any other action, run
`<python> <plugin-root>/bin/skill-profile-ctl check statusline-preview`. If it
exits 1 or 2, stop and report its output. `<plugin-root>` is the absolute path
two directories above this `SKILL.md`, whose directory is
`<plugin-root>/skills/statusline-preview`; take it from this file's own path,
never from the working directory or an environment variable. `<python>` is `sh
<plugin-root>/bin/python-launcher`.

Run `<python> <plugin-root>/bin/statusline-ctl preview`. Present its output unchanged.
