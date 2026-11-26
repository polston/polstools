# Antigravity goal adapter

## Capability or semantic

Google Antigravity (`agy`) treats `/goal` as an autonomous execution directive
for multi-step, bounded tasks. Antigravity supports multi-agent pair programming,
background command execution, schedule/liveness timers, subagent delegation,
and structured artifact generation.

Unlike Claude Code's external transcript-only evaluator, the Antigravity agent is
both the executor and validator. It directly inspects repository state, executes
commands and tests, launches background tasks, writes structured artifacts, and
interacts with the user or subagents. Do not impose Claude Code's 120-word
transcript-only compression on Antigravity; doing so discards essential execution
grounding (read-first context, authority boundaries, and structured handoff).

When a goal is active, the agent autonomously executes sequential tasks,
verifying each step against required evidence contracts without stopping
until the goal condition is met, an unresolvable blocker requires operator
direction, or the authorized time/turn bound is reached.

## Adapter

Preserve the shared contract as an actionable, structured directive. Structure
the goal prompt using these canonical slots (omitting `BASELINE` only when no
prior measurement exists):

| Slot | Requirement |
|---|---|
| OBJECTIVE | One bounded, coherent outcome. Must not span multiple independent finish lines. |
| BASELINE | *(Optional)* Verified starting measurements, observed defects, or before-state numbers already established so the run does not waste turns re-auditing. |
| READ FIRST | Durable plans, repository working agreements (`AGENTS.md`), contracts, or schemas that ground the run before making changes. |
| ARTIFACTS | Concrete file paths and structures that must exist and be verified upon completion. |
| CONSTRAINTS & AUTHORITY | Invariants, explicit reconciliation with repo standing rules (affirm or override auto-push/auto-release per phase), and safety boundaries for both git and live host state outside git. |
| EVIDENCE | Deterministic verification commands (tests, linters, types) and the exact exit status or output required. Exclude setup mutations; run commit-dependent gates (privacy scans, base-diff validators) after committing. |
| EXECUTION LOOP | Checkpoints, validation after meaningful changes, short progress tracking, and background-task and subagent hygiene before exit (zero lingering tasks or idle/recycled subagents). |
| EXITS | Distinct stop conditions: `Review needed` (operator choice required), `PARKED` (exact failure, attempted remedies, missing authority/state, resume check), and `BOUNDS` (primary turn/time cap; reaching it stops without claiming completion). |
| HANDOFF | Final artifacts, validation results, before/after delta, and unresolved items. |

### Grounding and Execution Rules

1. **Ground Before Mutating (`BASELINE` + `READ FIRST`):**
   Record already-verified measurements in `BASELINE` and inspect referenced
   durable plans, handoffs, and working agreements (`AGENTS.md`) before
   modifying code. Never assume architecture from memory.
2. **Authority Boundaries (`CONSTRAINTS & AUTHORITY`):**
   State permitted actions (e.g., local checkpoint commits) vs. forbidden
   actions (e.g., `git push`, PR creation, test weakening, or destructive
   cleanup without confirmation). Explicitly reconcile with repo standing rules
   and require pre-mutation verification before changing live host state outside
   the repository (installed binaries, user-scope skills or configs, hooks,
   state logs).
3. **Subagent Delegation & Right-Sizing:**
   Apply Shared Rule 9 using Antigravity's native subagent delegation and
   worker-tier selection when work spans broad read-only surveys, parallel
   checks, or long verification runs. Keep contracts expressed in behavioral
   roles and capability tiers rather than hardcoding internal tool names or
   model IDs.
4. **Structured Artifacts:**
   Store complex plans, diffs, and validation summaries as session artifacts
   rather than cluttering the conversational transcript.
5. **Sequential Multi-Goal Phasing:**
   Split phases along real write-boundary or decision-gate seams—do not multiply
   launch count or introduce intermediate rule-deferral hacks when adjacent
   same-boundary work shares one clean verification gate. Factor shared
   authority matrices once above phased contracts, point to referenced plan
   sections rather than copying specification prose, and let later phases
   inherit shared invariants from the prior phase's handoff.

## Lifecycle

In the chat UI or CLI, `/goal <contract>` launches autonomous execution.
Progress is reported at task checkpoints, external builds or waits use reactive
notifications rather than polling loops, and background tasks and subagents are
audited clean (zero lingering tasks or idle/recycled subagents) before declaring
completion. The agent concludes when all evidence is verified,
a `Review needed` or `PARKED` stop condition triggers, or the bound is reached.
Upon completion, update durable repository tracking documents following the
repository's own conventions (e.g., `HANDOFF.md`), preserving verifiable
receipts without relying on ephemeral chat state.

## Equivalent outcome

Execution proceeds toward one bounded objective, supported by concrete
verification commands and artifact checks, and stops cleanly at the designated
finish line.

## Disposition

Convergence test: if Antigravity, Claude Code, and Codex officially adopt the
same evaluator visibility, lifecycle controls, and contract shape, move the
remaining common rules into `SKILL.md` and delete this adapter.
