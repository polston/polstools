---
name: statusline-check
description: Check supported Claude and Codex statusline alignment.
---

# Check aligned status lines

Before any other action, run
`<python> <plugin-root>/bin/skill-profile-ctl check statusline-check`. If it
exits 1 or 2, stop and report its output. `<plugin-root>` is the absolute path
two directories above this `SKILL.md`, whose directory is
`<plugin-root>/skills/statusline-check`; take it from this file's own path,
never from the working directory or an environment variable. `<python>` is `sh
<plugin-root>/bin/python-launcher`.

Run `<python> <plugin-root>/bin/statusline-ctl check`. Report its one-line result and exit
code. For ccstatusline, `compatible` confirms provider preservation, Codex
fields, and the owned p profile widget. Verify unrelated custom-command
semantics separately through the skill guide.
