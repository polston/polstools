---
name: statusline-apply
description: Use only when the operator asks to apply the aligned Codex footer and a compatible Claude statusline; it edits their settings files.
---

# Apply aligned status lines

Before any other action, run
`<python> <plugin-root>/bin/skill-profile-ctl check statusline-apply`. If it
exits 1 or 2, stop and report its output. `<plugin-root>` is the absolute path
two directories above this `SKILL.md`, whose directory is
`<plugin-root>/skills/statusline-apply`; take it from this file's own path,
never from the working directory or an environment variable. `<python>` is `sh
<plugin-root>/bin/python-launcher`.

Run `<python> <plugin-root>/bin/statusline-ctl apply`. This explicit configuration mutation
preserves ccstatusline, installs the fallback only when Claude has no renderer,
adds or refreshes only the tagged p profile widget, refuses unknown external
renderers, and restores every earlier target if a later write fails. Report the
result and immediately run `statusline-ctl check`.
