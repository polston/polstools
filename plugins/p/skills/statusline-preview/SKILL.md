---
name: statusline-preview
description: Preview representative aligned Claude and Codex status lines.
---

# Preview aligned status lines

Before any other action, resolve the plugin root and run
`<python> <plugin-root>/bin/skill-profile-ctl check statusline-preview`. If it
exits 1 or 2, stop and report its output.

Resolve Python and the plugin root as described by the `aligning-statuslines`
skill, then run `statusline-ctl preview`. Present its output unchanged.
