# Codex goal adapter

## Capability or semantic

Codex treats `/goal` as a durable objective for long-running work. Its official
workflow calls for read-first context, checkpoints, progress evidence, and a
verifiable stopping condition, with explicit status, edit, pause, resume, and
clear controls. The documentation does not establish Claude Code's
transcript-only evaluator contract, so do not impose that constraint on Codex.

Sources: [Follow a goal](https://learn.chatgpt.com/use-cases/follow-goals) and
[Codex developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli).

## Adapter

Preserve the shared contract as a readable objective. Name the sources to read,
protected scope, checkpoint loop, exact validation, terminal states, and final
handoff. Put longer supporting detail in a durable file and point the objective
at it instead of compressing away execution context.

Make the execution loop concrete: recover actual state, work in checkpoints,
validate after meaningful changes, and keep a short progress record. State what
counts as complete, `Review needed`, blocked, or stopped at a bound. A blocker
must keep the outcome incomplete and include the exact resume check.

## Browser evidence

When success requires browser evidence, name an execution surface compatible
with the active environment. In Codex CLI, allow locally launched headless
browser automation when no in-app browser connection exists. Preserve the
required browser interaction and durable visual or DOM evidence; changing the
surface must not weaken acceptance.

## Lifecycle

`/goal <objective>` sets the goal and `/goal` shows it. Use `/goal edit` to
revise the objective and `/goal pause`, `/goal resume`, and `/goal clear` to
pause, resume, and clear it. Do not replace a matching active goal merely to
rewrite its wording; reconcile the work with the existing objective.

Inspect the currently exposed goal tools and latest observed state before asking
the operator to use those controls. Documented user commands are not necessarily
callable agent tools. Do not claim a resume or objective edit that did not occur,
or mark unfinished work complete merely to create a replacement goal.

An explicit new user grant can authorize continuation beyond an earlier bound.
Record its new start/deadline separately and preserve the expired run's evidence;
neither a resume alone nor a stale objective silently renews a bound. Reconcile
stale objective wording with the latest explicit instruction. If the matching goal
is already active, an unavailable objective-edit tool need not block authorized
work. If native execution remains blocked or paused and no exposed tool can
resume it, explain the exact user control needed for unattended continuation.
Continue independently authorized current-turn work while awaiting that action,
but honor an explicit user pause. Recheck state when a resume notification or
reply arrives so an obsolete request does not become a repeated approval gate.

## Equivalent outcome

The run continues toward one bounded objective and stops only at verified
completion, a required human review, a genuine blocker, or the stated bound.

## Disposition

Convergence test: if Codex and Claude Code officially adopt the same evaluator
visibility, lifecycle controls, and contract shape, move the remaining common
rules into `SKILL.md` and delete this adapter.
