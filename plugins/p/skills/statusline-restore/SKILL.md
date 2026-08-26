---
name: statusline-restore
description: Restore statusline settings changed by the last apply operation.
---

# Restore aligned status lines

Before any other action, resolve the plugin root and run
`<python> <plugin-root>/bin/skill-profile-ctl check statusline-restore`. If it
exits 1 or 2, stop and report its output.

Resolve Python and the plugin root as described by the `aligning-statuslines`
skill, then run `statusline-ctl restore`. Report any setting deliberately left
untouched because it changed after apply.
