---
name: doctor
description: Use when a p hook fails, Claude, Codex, or Antigravity may load different plugin versions, obsolete polstools plugin IDs may remain, or the operator asks for a p installation health check.
---

# Diagnose p

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check doctor`. If it exits 1 or
2, stop and report its output.

Run this read-only command exactly:

```sh
sh "${CLAUDE_PLUGIN_ROOT}/bin/python-launcher" "${CLAUDE_PLUGIN_ROOT}/bin/p-doctor"
```

When the operator explicitly asks to compare a local marketplace checkout,
append `--repo-root <marketplace-root>` after verifying that root. With it,
each installed copy's files are compared with that checkout; without it, only
installed copies that report the same version are compared with each other.

Report every check, repair instruction, and the exit code. Exit 0 is healthy,
exit 1 found actionable drift, and exit 2 means an available harness could not
be checked. Do not summarize away a failed or unavailable check, expose paths,
or apply a printed repair without the operator requesting it.
