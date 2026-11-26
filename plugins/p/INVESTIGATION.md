# Bounded investigation and improvement

The `investigating-and-improving` skill accepts a subject and desired outcome,
uses relevant context, researches competing approaches, chooses informative
probes, verifies results and continues within a supplied bound. It chooses methods
without making the requester configure a study. Material intent, authority and
preference decisions still belong to the appropriate requester.

The native agent performs research and tool work. The `bin/investigation` helper
preserves consequential state and checks structural invariants. It does not
replace the harness, schedule future execution, certify research quality or
prevent unmediated tool use. No session-wide response format is installed.

## How results choose the next step

The existing skill now makes its loop explicit: choose a consequential decision,
investigate, select a discriminating check, execute and verify, make a disposition,
then choose and perform the next useful action. A result can change the route;
it does not silently change the objective or extend the bound.

An enabling repair returns to the investigation it unblocked. Resolving a branch
prompts a check of the remaining objective, not automatic closure of the run.
Unavailable evidence blocks only dependent work. The run ends at actual acceptance,
its bound, an explicit stop, or a grounded finding that no useful authorized action
remains. This is reasoning guidance shared by both recording modes, not a new
scheduler, state-machine transition or mandatory response format.

## Start with the thing you want improved

Ask your agent to use `investigating-and-improving`, name the subject and the
outcome you care about, and give a maximum elapsed time. Include constraints the
agent cannot infer, such as “design only” or “keep the current file format.” You
can point to a repository, document, recurring problem or an earlier investigation.
You do not need to choose research methods, evaluation metrics or helper commands.

When testing an unreleased checkout, explicitly ask the agent to read that checkout's
`plugins/p/skills/investigating-and-improving/SKILL.md` and use its referenced helpers.
Keep the skill at its canonical location so relative helper resolution stays valid.
This selects the source under test without installing it or relying on an installed
copy having the same helper code.

For example:

> Use investigating-and-improving on this importer. Find the most consequential
> reliability problems, investigate competing fixes, implement justified changes
> and test them against realistic inputs. Keep the current output format. Six-hour
> upper bound.

> Use investigating-and-improving to find out why our sessions keep reopening work
> previously called finished. Use the local history already authorized for this
> task. Distinguish unfinished work from newly requested scope, and test a proposed
> improvement if the evidence supports one. Four-hour upper bound.

> Use investigating-and-improving on this first-visit guide. Check its facts and
> whether a new visitor can find the instructions they need. Prepare concrete
> alternatives where the choice depends on taste. Design only; two-hour upper bound.

The agent first inspects the available context, identifies worthwhile questions
and chooses work that can distinguish the competing explanations. It resolves
accessible facts itself and asks you about consequential intent, permission or
taste it cannot establish. A missing permission can require an immediate question.
It can continue independent work while an answer is pending. A broad
request is not a promise that every possible improvement will be explored.

Expect findings tied to evidence, any resulting changes or prototypes, checks of
what those changes accomplished, and the important unresolved questions. A failed
experiment or a decision to keep the existing approach can be a useful result.
Passing software tests does not establish that a workflow helped people; reader
comprehension and preference need actual reader evidence.

The time limit is a ceiling. The agent can finish earlier when the requested
outcome is verified; reaching the limit with unresolved work means incomplete.
For unattended execution, `writing-goals` defines the bounded outcome using the
active harness's supported goal mechanism. A saved checkpoint preserves progress
but does not itself keep an agent running. After an interruption, ask the agent
to resume from that checkpoint and preserve the original deadline and pending
work. An expired deadline does not renew itself.

The commands below are the agent's implementation tools. Start with the request
above; the agent chooses whether a private note is sufficient or a stored run is
needed for accounting, corrections or recovery.

## Operation

Run helpers as `sh plugins/p/bin/python-launcher plugins/p/bin/investigation`
from the checkout, or substitute the absolute plugin root in another directory.
Use `--help` for command arguments and `contract` for exact event fields. The
[skill's state reference](skills/investigating-and-improving/references/state.md)
describes input contracts. The agent prepares these inputs from the request;
they are not a questionnaire for the user.

1. Establish the subject, purpose, existing authority, acceptance and fixed
   deadline. Inspect accessible context before asking about missing information.
2. Start with ordinary private checkpoints and the skill's
   [adaptive guidance](skills/investigating-and-improving/references/adaptive-v1.md).
   Use stored state only for a concrete transactional accounting, correction or
   audit need; a long duration alone is insufficient. Select methods with
   `methods --domain DOMAIN` without creating a store; load applicable guidance
   and resolve conflicts. When stored state is needed, create a root with `begin`
   in a private store outside Git.
3. For stored runs, record significant research, conjectures and questions. Use `admit` before
   native execution, then bind actual output artifacts with `settle`. Do not
   dispatch when admission reports that the action identity already exists.
4. Recover the compact notes, or use `checkpoint` for stored runs. Shared grants cannot exceed their parent, restart
   the deadline or erase observed use. Uncertain execution retains reservations.
5. Verify acceptance with relevant evidence; stored runs also record it and use `audit`. Inspect the actual
   evidence before claiming the requested outcome; a passed structural audit
   is not independent verification of a semantic judgment.

The `collect-workflow` operation reuses the existing local transcript service
with a root-bounded deadline and a recorded action. It discovers configured
corpus roots, runs diagnosis/review/design packs and returns private report
references and source coverage. It requires an existing `local-session-history`
authority scope. The skill must also honor the active profile's capability gate
before using local history. It never makes provider calls. When a byte allowance
is supplied, accounting covers the workflow runner's captured and derived bytes;
state metadata and the bridge's report-reference publication are outside that
measurement. Partial byte accounting remains reserved until reconciled.

New captures freeze the admission event reference into the nested workflow plan.
If a process stops after publishing a report but before settling its action,
repeat the same `collect-workflow` request to inspect linked runs and published
views. Replay does not run capture again, extend the deadline, settle the action
or release its reservation. Inspect every linked receipt and verify effects and
usage before using the existing settlement operation. Multiple links remain
visible; legacy runs without a link remain unknown rather than being matched by
timestamps. The reference records provenance, not independent proof of effects.
For read-only recovery, use `inspect-workflow --store STORE --root KEY --action-id ID`.
It returns the same exact linked runs and views without needing the original
capture configuration, a new sequence number or writable access. An unknown
root or action is an input error; a known action without linked runs is unknown.

## Questions and evidence

Retrieve available facts before escalating them. Technical questions need
relevant attempted methods or an explained infeasibility; a fixed number of
failures is not required. Missing authority can warrant an immediate question.
Taste questions need concrete alternatives. A child asks its parent first;
parents consolidate questions requiring the human. Independent work can continue
while a dependent action waits.

Answers bind the exact question and frame, intended respondent and source
artifact. This preserves claimed attribution; only the transport can establish
who actually answered. Simulated answers cannot satisfy actual question state.
Late answers to changed frames require explicit reconciliation. Duplicate pending
questions use a normalized caller-supplied topic; semantic equivalence remains
an agent responsibility.

Evidence references bind actual artifacts or exact record revisions. Artifact
selectors must resolve for passing acceptance. Historical membership retains the
initial frame without making every later event stale when an initial input
changes. Current claims check the current frame and their substantive inputs;
result receipts retain the originating action's input and answer dependencies.
Answer evidence must resolve and remain current before dependent admission.
Old records and negative results remain readable.

Result interpretations can be explicitly corrected without rewriting execution
receipts. The versioned correction extension links an exact result or its latest
correction to an attributed replacement interpretation and supporting evidence.
Recovery exposes the original result beside `current_interpretation` and its
history. Superseded interpretations invalidate dependent acceptance; independent
raw evidence remains usable. Revalidation is explicit, and no audit establishes
the truth of a correction. See the [state reference](skills/investigating-and-improving/references/state.md#correcting-a-result-interpretation)
for the public operation and compatibility boundary.

Blocked, stopped and bound runs still accept reconciliation of already admitted
work. A requester-attributed `resume` can reactivate blocked or stopped work
before the original deadline; it cannot replenish resources. A bound remains
incomplete. Source instructions and modules cannot confer new authority.
Corrections also reconcile closed or expired runs without reopening execution.
`status` records the lifecycle; `completion_current` and the audit distinguish
historical completion from acceptance invalidated by a later correction.

## Persistence and compatibility

Legacy-only stores stay at format 1. The first investigation write saves and
checks a format-1 database backup, then changes the registry to format 2 in the
publication transaction. New readers support both. Old readers refuse format 2
on open; already-running old processes must be stopped before migration. Do not
upgrade an actively shared production store during an experiment.

Backups contain the database, not copies of immutable artifact objects. Preserve
both for restoration. Test restoration in a separate private store before moving
callers. Rollback to a backup omits all later changes; it must never overwrite the
only copy of newer evidence. Format-2 bundles retain investigation history;
imports validate chains and rebuild projections, ignoring imported projections
and machine-local backup receipts. Import preserves producer claims without
turning them into authenticated execution.

A root binds the canonical contract digest. Resume with the same contract
version; a changed digest is rejected for new events. Keep the original plugin
version available to finish or export that run. Automatic policy migration is
not provided, and recreating the root is not a valid way to reset its allowance.
The correction extension preserves the base policy digest and format-2 storage;
each correction separately pins its extension digest. Existing runs need no root
replacement. Export before opting a run into corrections: older readers that
support base investigation v1 still reject correction events. Preserve that
export and the newer history separately if rolling back.

The workflow package owns shared wire validation and replay invariants so generic
registry import cannot bypass them. The investigation package owns orchestration,
method resolution and CLI transport. Neither package executes a script to import
the other. The canonical policy and event shapes live beside the skill.

## Verification and limits

Tests cover malformed records, migration interruption, stale sequence conflicts,
concurrent reservations, deadline equality, uncertain settlement, pending and
late answers, selector validity, history/currentness, module conflicts and CLI
transport. These are structural and software checks. Live comparisons are needed
to establish useful effect, acceptable administrative cost and reliable requester
interaction. The implementation's evaluation record must retain failed and
incomplete cases as well as favorable ones.

Self-improvement compares a versioned candidate against the incumbent on later
investigations. Evaluator changes are separate candidates and require rescoring
both process versions. A shared unrestricted filesystem is only a procedural
boundary, not isolated confirmation. A no-change or insufficient-evidence result
can be correct; it is not proof that the candidate improved.

The initial implementation evaluation
reports actual agent comparisons, self-application, corpus use and transport
limits. Structured state did not demonstrate a general task-quality or
productivity advantage in the small authored comparison. Use its stronger
accounting and recovery machinery where those needs are material; retain a
policy-only comparator when evaluating whether its administration earns its cost.

## Recovery evaluation helpers

The stdlib modules `retro_eval.investigation.recovery`, `telemetry` and
`process_barrier` support reusable evaluation without changing investigation
admission or settlement. They are Python library interfaces, not a new scheduler
or a second registry. Study adapters supply service-specific observations and
retain exact artifacts and normalization versions outside repositories.

`reconcile_attempts` merges explicit attempt identities across invocation,
dispatch, admission and acknowledgment observations. Never add those source
counts together. Missing trace entries are not evidence of nondispatch; a
backend counter after validation is only a lower bound. Incomplete coverage,
unlinked observations and contradictions remain visible. `evaluate_recovery`
returns independent service, knowledge, retry, allowance, report, deadline and
closure judgments. A held reservation remains charged; a completed record is
not service evidence. Evidence references support attributed observations, not
authentication or semantic truth. A service adapter must inspect actual outcomes
and the evidence visible to its worker. Report references can be nonempty strings
or objects containing nonempty `path` and `observation` strings; naming a file
alone does not establish what happened.

`trial_integrity` checks normalized freeze, fault, visibility, cleanup and bound
receipts before comparative use. Keep invalid assignments in the denominator,
with their infrastructure disposition separate from agent performance. Historical
field units that were unspecified stay ambiguous under reassessment; preserve
original grades and use one frozen evaluator for both arms.

`read_jsonl_usage` reads Codex-style turn events with strict JSON parsing;
`summarize_usage` accepts normalized events from other adapters. Each field has
an observed sum, measured-turn count and missing-turn count. No measurement is
null, observed zero is zero, and partial totals are not full-run cost. Stable
event IDs permit deduplication; identical events without IDs remain ambiguous.
Cached input is a subset of input. These interfaces do not establish provider
billing or cross-harness comparability.

`OwnedProcess` is a POSIX adapter for cooperative local writers that remain in
one fresh anchored process group. Use it as a context manager, or call `cleanup`
in `finally`; the control directory must be fresh and protected from worker
mutation. Use file streams, not pipes. Cleanup runs after normal target exit,
timeout, interruption and controller exceptions. Before starting a successor, call
`successor_allowed(receipt, expected_owner=owned.owner_id)` with the identity of
the process owner whose work must be quiescent. Retain that identity from the
controller's `OwnedProcess` object (or its trusted attempt record), never from the
receipt being checked. Version-two receipts bind a fresh owner identity and require
consistent launch and process-state evidence. Wrong-owner, legacy unbound,
incomplete or contradictory receipts fail closed, as do missing or failed process
observations. Passing a receipt without an expected identity now returns false;
existing study controllers must update their call before relying on this gate.
The anchor retains group ownership until termination; unrelated processes are not
signaled. Call from one owner, with no competing waiters on its process object.
The host must permit `ps` observation. Failed observations are retained even when
owned-group termination succeeds.

These are opt-in library calls, not automatic harness or registry enforcement.
The expected identity and control directory must remain trusted; an identity is
correlation, not authentication. Successful cleanup establishes local writer
quiescence under the stated model, not task success or freshness of administrative
status. A completed target can still have live descendants, while a stale status
entry or a command-text match does not establish that any owned writer is alive.

This does not contain children that detach into another session, cancel remote
operations, or survive an externally killed controller. A native harness may
create separate process groups; do not infer effect quiescence from its exit.
Use a separately owned effect service or stronger host containment, and verify
that scope before scored recovery. Portable evaluation logic and synthetic adapter
tests do not establish live effectiveness in every harness.

## Experimental recovery summaries

`retro_eval.investigation.recovery_summary` provides a read-only projection of
adapter-normalized observations, using the recovery evaluator's existing
knowledge and retry rules. It is optional and is not enabled by a skill or hook.
It creates no reservations, executes no effects and does not change investigation
records. A source digest establishes byte identity, not the truth of a claim.

The initial `recovery_local` adapter is deliberately narrow: isolated local
ledger, immutable publication and opaque dispatch fixtures using
`isolated-local-service-v1`. It requires an original `task.json`, a complete
`client-journal.jsonl`, retained `transport/<identity>.sent` request envelopes,
matching `.response` files, and a `recovery-barrier.json` receipt. Every invocation
is recorded before dispatch with its identity, mode and timestamp. The service
has one serial request handler, no other clients or writers, and receipts mean
the identified effect has finished. The barrier's verified timestamp covers
earlier writers; an unresolved newer submission invalidates its use for retry.
This is a fixture protocol, not an adapter for arbitrary harness logs or remote
APIs. The public contract is in the adapter docstrings, validation and synthetic
tests; other adapters must establish their own service guarantees.

The generic `project` interface accepts normalized task, attempts, observations,
barrier, coverage and exact source hashes. Adapters validate evidence selectors
and semantics. Unsupported or contradictory evidence must remain an issue; never
label a partial query complete or infer channel coverage from an empty directory.
The resulting view keeps spent and held slots, known effects, unresolved evidence
and conditional next steps distinct. The generic layer cannot detect a dishonest
adapter or authenticate a barrier receipt.

For a supported fixture, run the module from the plugin directory with
`python3 -B -m retro_eval.investigation.recovery_local WORKSPACE`; `--json` returns
the machine-readable view. `summarize` discovers and hashes the entire declared
source set, detects changes during generation and writes nothing. `check_current`
rebuilds the view, checking additions, deletions, changed bytes and deadline
expiry. Refresh after every action. These are snapshot checks, not locks: the
caller must prevent concurrent service changes and apply normal admission and
authorization before acting. Output references use `path@sha256#selector`.

Source correctness and live usefulness are separate gates. The registered
comparison did not establish a practical benefit over ordinary notes: the full
summary saved a median paired 0.44 seconds across 16 valid matched tasks, below
the required ten seconds and twenty percent. Two of 54 assigned successors had
provider failures; all 52 completed recoveries preserved protected constraints.
Keep ordinary notes as the default and the summary experimental.
A future adoption decision requires fresh confirmation in its intended scope.
