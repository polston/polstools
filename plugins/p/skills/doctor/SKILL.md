---
name: doctor
description: Use when a p hook fails, Claude, Codex, or Antigravity may load different plugin versions, obsolete polstools plugin IDs may remain, or the operator asks for a p installation health check.
---

# Diagnose p

Before any other action, run
`<python> <plugin-root>/bin/skill-profile-ctl check doctor`. If it exits 1 or
2, stop and report its output. `<plugin-root>` is the absolute path two
directories above this `SKILL.md`, whose directory is
`<plugin-root>/skills/doctor`; take it from this file's own path, never from
the working directory or an environment variable. `<python>` is `sh
<plugin-root>/bin/python-launcher`.
Quote both paths and write them with forward slashes, also on Windows. If the
check exits 2 because session variables of two harnesses are set, rerun it once
with `P_SKILL_HARNESS` set to this session's harness (`claude`, `codex`, or
`antigravity`).

Run this read-only command exactly:

```sh
<python> <plugin-root>/bin/p-doctor
```

When the operator explicitly asks to compare a local marketplace checkout,
append `--repo-root <marketplace-root>` after verifying that root. With it,
each installed copy's files are compared with that checkout; without it, only
installed copies that report the same version are compared with each other.

Report every check, repair instruction, and the exit code. Exit 0 is healthy,
exit 1 found actionable drift, and exit 2 means an available harness could not
be checked. Do not summarize away a failed or unavailable check, expose paths,
or apply a printed repair without the operator requesting it.
