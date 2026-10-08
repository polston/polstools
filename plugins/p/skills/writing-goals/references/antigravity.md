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
the goal prompt using these canonical slots:

| Slot | Requirement |
|---|---|
| OBJECTIVE | One bounded, coherent architectural outcome. Must not span multiple independent finish lines. |
| READ FIRST | Durable plans, repository working agreements (`AGENTS.md`), contracts, or schemas that ground the run before making changes. |
| ARTIFACTS | Concrete file paths and structures that must exist and be verified upon completion. |
| CONSTRAINTS & AUTHORITY | Invariants, repo authority boundaries (e.g. no git push, no unreviewed PRs, no test weakening, zero-downtime rules). |
| EVIDENCE | Deterministic verification commands (tests, linters, types) and the exact exit status or output required. |
| PARKED | Explicit blocker conditions, missing authority requirements, and exact resume checks. |
| BOUNDS | Primary turn and time bounds for autonomous execution (e.g. 45 minutes / 25 turns). |

### Grounding and Execution Rules

1. **Ground Before Mutating (`READ FIRST`):**
   The agent must inspect referenced durable plans, handoffs, and working agreements
   (`AGENTS.md`) before modifying code. Never assume architecture from memory.
2. **Authority Boundaries (`CONSTRAINTS`):**
   State explicit repository boundaries in the contract: permitted actions (e.g., local
   commits for checkpoints) vs forbidden actions (e.g., git push, PR creation,
   deleting historical benchmarks, or changing production secrets without confirmation).
3. **Subagent Delegation (`invoke_subagent`):**
   When research spans many files or a long compiler/test verification run is needed,
   delegate to `research` or `self` subagents to keep the primary goal execution context lean.
4. **Structured Artifacts (`ArtifactMetadata`):**
   Use Antigravity's artifact system for complex plans, diffs, and validation summaries
   stored in the session directory rather than cluttering the conversational transcript.
5. **Sequential Multi-Goal Phasing:**
   For cross-cutting architectural work spanning multiple layers or repositories, do not
   combine them into one monolithic goal. Decompose them into sequential goals where each
   goal leaves a durable, verifiable contract or schema for the next.

## Lifecycle

In the chat UI or CLI, `/goal <contract>` launches autonomous execution.
Progress is reported at task checkpoints. The agent concludes when all evidence
is verified, an unresolvable blocker requires human direction, or the bound is reached.
Upon completion, the agent updates durable repository tracking documents (e.g., `HANDOFF.md`)
rather than recording ephemeral git hashes.

## Equivalent outcome

Execution proceeds toward one bounded objective, supported by concrete
verification commands and artifact checks, and stops cleanly at the designated
finish line.
