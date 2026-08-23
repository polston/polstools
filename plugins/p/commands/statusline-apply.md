---
description: Apply the aligned Codex footer and a compatible Claude statusline
---

Resolve Python and the plugin root as described by the `aligning-statuslines`
skill, then run `statusline-ctl apply`. This is an explicit configuration
mutation. It preserves ccstatusline, installs the fallback only when Claude has
no renderer, refuses unknown external renderers, and restores every earlier
target if a later write fails. Report the result and immediately run
`statusline-ctl check`.
