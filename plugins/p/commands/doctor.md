---
description: Diagnose p installation drift and both live format hooks
---

Run this read-only command exactly:

```sh
sh "${CLAUDE_PLUGIN_ROOT}/bin/python-launcher" "${CLAUDE_PLUGIN_ROOT}/bin/p-doctor"
```

Report every check, repair instruction, and the exit code. Exit 0 is healthy,
exit 1 found actionable drift, and exit 2 means an available harness could not
be checked. Do not summarize away a failed or unavailable check.
