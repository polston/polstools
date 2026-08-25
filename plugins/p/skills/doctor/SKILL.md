---
name: doctor
description: Use when a p hook fails, Claude and Codex may load different plugin versions, obsolete polstools plugin IDs may remain, or the operator asks for a p installation health check.
---

# Diagnose p

Run this read-only command exactly:

```sh
sh "${CLAUDE_PLUGIN_ROOT}/bin/python-launcher" "${CLAUDE_PLUGIN_ROOT}/bin/p-doctor"
```

When the operator explicitly asks to compare a local marketplace checkout,
append `--repo-root <marketplace-root>` after verifying that root.

Report every check, repair instruction, and the exit code. Exit 0 is healthy,
exit 1 found actionable drift, and exit 2 means an available harness could not
be checked. Do not summarize away a failed or unavailable check, expose paths,
or apply a printed repair without the operator requesting it.
