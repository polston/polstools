---
name: finding-friction-in-recent-sessions
description: Use when asked what has been going wrong in how we work, for a weekly or periodic retrospective, or when the same annoyance keeps recurring across sessions and should become a rule instead. Reads measured session history, not memory.
---

# Finding friction in recent sessions

Before any other action, run
`<python> <plugin-root>/bin/skill-profile-ctl check finding-friction-in-recent-sessions`.
If it exits 1 or 2, stop and report its output. `<plugin-root>` is the absolute
path two directories above this `SKILL.md`, whose directory is
`<plugin-root>/skills/finding-friction-in-recent-sessions`; take it from this
file's own path, never from the working directory or an environment variable.
`<python>` is `sh <plugin-root>/bin/python-launcher`.
Quote both paths and write them with forward slashes, also on Windows. If the
check exits 2 because session variables of two harnesses are set, rerun it once
with `P_SKILL_HARNESS` set to this session's harness (`claude`, `codex`, or
`antigravity`).

## Automatic investigation

Use `<plugin-root>/WORKFLOW.md` as the shared operating procedure. Run the
bounded workflow command with configured/default source roots, inspect its
private packet, and produce evidence-backed findings and an actionable next
step. The planner chooses the window, source allocation, packs, and limits;
do not ask the operator to design the study.

Keep observations, agent interpretations, human observations, and efficacy
claims distinct. Inspect positive candidates and noncandidates as well as
complaints. A cue or repeated call is a retrieval lead, not a confirmed defect.
No recommendation is a valid outcome when evidence or practical value is weak.

Choose an intervention only after considering counterexamples and existing
capabilities. It may be a removal, a tool or configuration change, a bounded
experiment, further observation, or no change. Do not require every finding to
become a new instruction. Record local dispositions and follow-up triggers;
apply broader changes only within the operator's existing authority.

For reproduction of old ledger counts or packs, use
[the legacy ledger procedure](references/legacy-retro.md). Its per-session
counters are not interchangeable with case outcomes or calibrated failure rates.
