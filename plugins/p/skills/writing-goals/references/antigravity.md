# Antigravity goal adapter

## Capability or semantic

Google Antigravity (`agy`) treats `/goal` as an autonomous execution directive.
Antigravity supports multi-agent pair programming, background task execution,
and tool calls across sandboxed terminals and subagents.

When a goal is active, the agent autonomously executes sequential tasks,
verifying each step against required evidence contracts without stopping
until the goal condition is met or the authorized time/turn bound is reached.

## Adapter

Compress the shared contract into an actionable condition using the five
canonical slots in order:

| Slot | Requirement |
|---|---|
| EVIDENCE | Named verification command run after the change and the exact exit status or output required |
| ARTIFACT | File paths and structures that must exist and be verified upon completion |
| CONSTRAINTS | Behavioral invariants, sandbox bounds, and prohibitions against weakening tests |
| PARKED | Explicit blocker conditions, missing authority requirements, and resume checks |
| BOUNDS | Primary turn and time bounds for autonomous execution (e.g. 120 minutes) |

## Lifecycle

In the chat UI or CLI, `/goal <contract>` launches autonomous execution.
Progress is reported at task checkpoints. The agent concludes when all evidence
is verified, an unresolvable blocker requires human direction, or the bound is reached.

## Equivalent outcome

Execution proceeds toward one bounded objective, supported by concrete
verification commands and artifact checks, and stops cleanly at the designated
finish line.
