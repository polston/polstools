---
name: update
description: Safely update p in Claude Code and Codex without deleting versioned cache snapshots still referenced by active sessions.
---

# Update p safely

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check update`. If it exits 1 or
2, stop and report its output.

Run the plugin-owned updater exactly once:

```sh
<python> <plugin-root>/bin/p-update
```

It updates every available installed harness, preserves prior Codex cache
snapshots across the remove/add operation, and runs the newly installed doctor.
Report every PASS line, the doctor result, and the exit code. Exit 0 means both
harnesses agree and existing sessions retain their original skill paths. Start
new sessions to load the new version.

Do not replay individual update steps after an ambiguous interruption. Rerun
the updater: its operations are cache-preserving and final doctor verification
reconciles the installed state.
