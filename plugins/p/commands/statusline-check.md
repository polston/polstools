---
description: Check supported Claude and Codex statusline alignment
---

Before any other action, resolve the plugin root and run
`<python> <plugin-root>/bin/skill-profile-ctl check statusline-check`. If it
exits 1 or 2, stop and report its output.

Resolve Python and the plugin root as described by the `aligning-statuslines`
skill, then run `statusline-ctl check`. Report its one-line result and exit code.
For ccstatusline, `compatible` confirms provider preservation and Codex fields;
it also confirms the owned p profile widget. Verify unrelated custom-command
semantics separately through the skill guide.
