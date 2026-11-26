---
name: investigating-and-improving
description: Conduct a sustained, bounded investigation of a system, artifact, idea or workflow, test competing improvements, and preserve evidence across iterations. Use for rigorous diagnosis and iterative improvement, including improving this process itself.
---

# Investigating and improving

Before any other action, run
`<python> <plugin-root>/bin/skill-profile-ctl check investigating-and-improving`. If it exits
1 or 2, stop and report its output. `<plugin-root>` is the absolute path two
directories above this `SKILL.md`, whose directory is
`<plugin-root>/skills/investigating-and-improving`; take it from this file's own path, never
from the working directory or an environment variable. `<python>` is `sh
<plugin-root>/bin/python-launcher`.
Quote both paths and write them with forward slashes, also on Windows. If the
check exits 2 because session variables of two harnesses are set, rerun it once
with `P_SKILL_HARNESS` set to this session's harness (`claude`, `codex`, or
`antigravity`).

Invoke the preflight as a standalone command with literal absolute paths. Do not
combine it with variable assignments, pipelines or subsequent inspection commands:
those can prevent an already authorized command prefix from matching. A shell
permission denial is not a profile result. Correct a malformed invocation within
existing authority if possible; if preflight still cannot run, report that boundary
and stop this skill. Do not continue its investigation after a failed preflight or
infer that the profile passed from readable source files.

## Choose the recording needed by the work

For an existing stored run, read its pinned [v1 contract](contract-v1.json)
and [state operations](references/state.md); preserve its bounds, pending effects
and acceptance obligations. Do not turn it into a new run to avoid those checks.

For new work, start with [adaptive investigation v1](references/adaptive-v1.md)
and ordinary private notes. Choose the full stored procedure only for a concrete
need such as shared transactional allowances, linked corrections/currentness or
an explicitly requested audit trail. Duration alone does not establish that need.
For that procedure, read the v1 contract and state operations before `begin`.
The adaptive guidance does not change pinned event or policy semantics.

Use the [result-to-next-action loop](references/adaptive-v1.md#progress-from-a-result-to-the-next-action)
in either recording mode. It guides work selection and closure without changing
pinned stored-state transitions or granting additional authority.

Read exactly the active harness reference: [Codex](references/codex.md),
[Claude](references/claude.md), or [Antigravity](references/antigravity.md).
Adapters describe transport, not new authority. Select relevant domain guidance
with `sh <plugin-root>/bin/python-launcher <plugin-root>/bin/investigation methods --domain DOMAIN`.
This command is stateless and needs no store. Load only returned files needed for
the next method, resolving conflicts and prerequisites. If a domain name is
unclear, inspect the names and mappings in
`<plugin-root>/profiles/investigation-methods-v1.json` rather than guessing them.

Take the subject and purpose from the request and available context. Choose
methods yourself. Reuse supplied bounds and authorization. For sustained work
without a bound, use the available writing-goals skill to establish one; do not
ask for a supplied bound again. A parent agent may be the requester.

Frame the consequential unknowns before choosing an implementation. Select an
observation or experiment that can distinguish credible explanations; then
implement when the evidence and cost justify it. Preserve negative results and
worthwhile branches. A complete implementation may itself be the appropriate
experiment. Apply claim-specific verification rather than one generic score.

Record consequential decisions and experiments, not every search click. Native
tools perform the work: admission is not execution. Recover original bounds,
pending questions and evidence from the checkpoint after interruption. Stored
state does not wake an inactive harness. Report only actual enforcement levels.

Escalate accessible facts only after retrieving them proves insufficient.
Technical escalation needs relevant attempts or explained infeasibility, not a
fixed failure quota. Missing authority can warrant an immediate question. Present
concrete taste alternatives. Continue independent work while an answer is pending;
children bring uncertainty to the parent before interrupting the human.

For stored runs, use `audit` to check acceptance linkage, then inspect whether the evidence really
establishes the requested outcome. A green audit validates structure and recorded
judgments; it is not an independent research-quality certificate. At a bound,
record incomplete work and its exact continuation needs. Summarize useful findings,
verified changes, uncertainty and next action in ordinary language.
