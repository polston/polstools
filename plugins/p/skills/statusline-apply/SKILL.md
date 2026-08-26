---
name: statusline-apply
description: Apply the aligned Codex footer and a compatible Claude statusline.
---

# Apply aligned status lines

Before any other action, resolve the plugin root and run
`<python> <plugin-root>/bin/skill-profile-ctl check statusline-apply`. If it
exits 1 or 2, stop and report its output.

Resolve Python and the plugin root as described by the `aligning-statuslines`
skill, then run `statusline-ctl apply`. This explicit configuration mutation
preserves ccstatusline, installs the fallback only when Claude has no renderer,
adds or refreshes only the tagged p profile widget, refuses unknown external
renderers, and restores every earlier target if a later write fails. Report the
result and immediately run `statusline-ctl check`.
