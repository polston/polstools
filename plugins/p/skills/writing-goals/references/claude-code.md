# Claude Code goal adapter

## Capability or semantic

Claude Code treats `/goal` as a session-scoped completion condition. After each
turn, a separate model evaluates the conversation, cannot call tools or read
files independently, and starts another turn while the condition remains unmet.
The goal clears when it is achieved, judged impossible, explicitly cleared, or
ended by a documented unrecoverable error. Resuming preserves an active
condition while resetting its counters.

Source: [Claude Code goals](https://code.claude.com/docs/en/goal).

## Adapter

Compress the shared contract into a transcript-judgeable condition, usually
about 120 words even though the command accepts more. Use these five slots in
order and omit only a slot with nothing material to say:

| Slot | Requirement |
|---|---|
| EVIDENCE | Named command run after the last change and the literal summary or exit status that must appear |
| ARTIFACT | What exists where when done |
| CONSTRAINTS | What must remain true; block weakened tests and reduced scope |
| PARKED | Exact failure, attempted remedies, blocker, and resume check |
| BOUNDS | Required turn or time bound; add a stall cap when progress is countable |

Copy decisive command summaries or exit statuses into the condition instead of
relying on the evaluator to inspect a referenced file. A written plan supplies
evidence and artifacts from its verification section; do not invent new gates.

One goal has one finish line. Replace vibe words such as "better" or "robust"
with the command or artifact whose state changes. Do not reward a cheap exit by
omitting constraints, deleting a failing test, or calling a reached bound done.

## Lifecycle

`/goal <condition>` sets or replaces the active goal. `/goal` shows status and
`/goal clear` removes it; `stop`, `off`, `reset`, `none`, and `cancel` are clear
aliases. `/clear` also removes the goal. Resume preserves an active condition
but resets turn, time, and token counters.

## Equivalent outcome

The run continues toward one bounded objective and stops only when transcript
evidence proves completion, human action is required, the condition is judged
impossible, an unrecoverable error clears it, or the bound is reached.

## Disposition

Convergence test: if Claude Code and Codex officially adopt the same evaluator
visibility, lifecycle controls, and contract shape, move the remaining common
rules into `SKILL.md` and delete this adapter.
