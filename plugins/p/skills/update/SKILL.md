---
name: update
description: Use only when the operator asks to update p across installed harnesses (Claude Code, Codex, Antigravity); it keeps versioned cache snapshots that active sessions still reference.
---

# Update p safely

Before any other action, run
`<python> <plugin-root>/bin/skill-profile-ctl check update`. If it exits 1 or
2, stop and report its output. `<plugin-root>` is the absolute path two
directories above this `SKILL.md`, whose directory is
`<plugin-root>/skills/update`; take it from this file's own path, never from
the working directory or an environment variable. `<python>` is `sh
<plugin-root>/bin/python-launcher`.
Quote both paths and write them with forward slashes, also on Windows. If the
check exits 2 because session variables of two harnesses are set, rerun it once
with `P_SKILL_HARNESS` set to this session's harness (`claude`, `codex`, or
`antigravity`).

Run the plugin-owned updater exactly once:

```sh
<python> <plugin-root>/bin/p-update
```

It reads every available harness before changing anything: a harness without
p is skipped, and one that cannot be read stops the update with nothing
changed. It preserves prior Codex cache snapshots across the remove/add
operation, reinstalls Antigravity from the copy Claude or Codex now loads,
refreshes p's Claude status indicator, runs `codex plugin marketplace upgrade
polstools` first when Codex's marketplace is a remote repository, regenerates
Codex skill-activation entries with `skill-profile-ctl sync-native` when p owns
a region of the Codex config, and runs the newly installed doctor.
Antigravity does not record where p came from, so when it is the only harness
with p the updater stops and asks for `--agy-source <plugin directory>`; ask
the operator for that directory rather than guessing it. Use `--dry-run` when
the operator wants the plan without changes.

Report every PASS, SKIP, FAIL, and PLAN line, the doctor result, and the exit code.
Exit 0 means installed harnesses agree and existing sessions retain their
original skill paths; exit 1 means the update landed but the doctor or the
status indicator flagged drift; exit 2 means it could not complete safely.
Start new sessions to load the new version.

Do not replay individual update steps after an ambiguous interruption. Rerun
the updater: its operations are cache-preserving and final doctor verification
reconciles the installed state. If the Codex re-add fails, the updater exits 2
and prints `codex plugin add p@polstools`; run that once the cause is fixed.
