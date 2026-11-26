---
name: writing-plans
description: Create or revise an executable plan for consequential multi-step work, including dependencies, uncertain decisions, verification and adaptation during execution. Use when a plan or handoff is needed; ordinary small edits need no separate planning ceremony.
---

# Writing plans

Before any other action, run
`<python> <plugin-root>/bin/skill-profile-ctl check writing-plans`. If it exits
1 or 2, stop and report its output. `<plugin-root>` is the absolute path two
directories above this `SKILL.md`, whose directory is
`<plugin-root>/skills/writing-plans`; take it from this file's own path.
`<python>` is `sh <plugin-root>/bin/python-launcher`.
Quote both paths and write them with forward slashes, also on Windows. If the
check exits 2 because session variables of two harnesses are set, rerun it once
with `P_SKILL_HARNESS` set to this session's harness (`claude`, `codex`, or
`antigravity`).

Produce a route to the requested outcome that an executor can use and revise.
A plan is not execution authority or evidence of completion. Reuse existing
requirements, decisions and bounds. Planning alone needs no new goal or runtime
bound. For an authorized unattended run, the available writing-goals skill owns
the run contract; link the plan rather than duplicating that contract.

## Ground the route

Inspect the relevant implementation, consumer, constraints and available checks.
Distinguish observed facts, chosen design decisions and unresolved assumptions.
Resolve accessible facts yourself. If the approach depends on an unknown, plan
the smallest informative investigation before the dependent implementation;
use the investigation skill for substantial uncertainty. Specify what different
results would change. Ask only for consequential intent, taste or authority that
available evidence cannot settle, while continuing independent authorized work.

Work backward from acceptance to required outputs, then forward from current
state to check feasibility. Each consequential dependency should identify the
condition or artifact the next task consumes and how it will be established.
Task order alone does not establish that a prerequisite will hold. Check for
intervening actions that could invalidate it, especially shared writes, schema
changes and removal of an input still used by a consumer.

## Make the next work executable

Use the project's plan location and existing tracking convention when a durable
plan is useful. Choose the layout for this work; no fixed response format or task
count is required. Keep enough information for the intended executor:

- The outcome, protected requirements and authoritative inputs.
- Tasks with concrete deliverables, dependencies, relevant files or interfaces,
  and checks that distinguish success from a plausible wrong result.
- The next task, its owner, and whether it is currently authorized and ready to
  run. Keep that status consistent in the plan and any handoff; dependency order
  alone does not make a task runnable.
- Consequential assumptions, the observation that would invalidate them, and
  any needed rollback or recovery constraint.

Plan near-term known work precisely; leave later uncertain work conditional on
its decision gate. Verify existing paths, APIs and commands before prescribing
them. Mark proposed new interfaces as proposed. Include exact values and tricky
algorithm details when ambiguity would change correctness; avoid duplicating
complete implementations in prose. More detail can be necessary for an executor
with less context. Shorter plans are not inherently better.

Trace at least the consequential path through intermediate states and its
verification. Use an existing test, dry run or isolated probe where it can check
the claim within authority. Label unexecuted checks as planned. A mental trace
or reviewer agreement cannot certify runtime behavior. Keep plan-writing,
implementation, integration and later-use evidence distinct.

## Revise from observed state

When execution changes a relevant fact, identify which tasks and checks depend
on it. Repair that portion, preserving unaffected work and the original bound.
Reconsider the overall approach if the change invalidates its purpose or central
assumption. Do not silently weaken acceptance to make a revised plan pass.

Also look for newly satisfied prerequisites that remove unnecessary work.
Before deleting a task, verify that its obligations are fulfilled elsewhere and
that no remaining consumer needs its other effects. Reconcile uncertain pending
effects before repeating an operation. A missing result is not proof it did not run.

Record consequential changes and their evidence alongside the current plan,
without maintaining a second backlog. If execution is already authorized, act
on the next ready task; otherwise hand off the plan and its actual decision needs.
When evaluating or improving this planning practice, read
`references/follow-up.md`.
