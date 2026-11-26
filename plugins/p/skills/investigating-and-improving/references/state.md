# State operations

Use `sh "<plugin-root>/bin/python-launcher" "<plugin-root>/bin/investigation"`.
Append a subcommand below. `--store` must point outside Git repositories. Put
private input files and results there too. Exact paths belong in private state,
not plugin source. The `contract` command prints canonical field definitions.

- `begin --store STORE --root KEY --input FILE`: FILE contains frame,
  deadline_ms, limits, authority and capabilities. Deadline and activation are
  UTC epoch milliseconds. Resource amounts are nonnegative integers in the
  contract's named units; omitted dimensions have no claimed enforcement.
- `artifact --store STORE --input FILE --content-class CLASS`: publish actual
  bytes and retain the returned reference. Default classification is private_raw.
- `checkpoint --store STORE --root KEY`: recover sequence, frame, questions,
  candidates, reservations, remaining time and acceptance status.
  Checkpoint, audit and export open existing stores read-only; they do not create
  a missing store or reserve a writer. Inspection requires the default rollback
  journal, not an externally configured WAL store. Do not change journal mode
  concurrently: the header check cannot prevent a racing switch from creating
  SQLite sidecars. Strong filesystem nonmutation requires an execution surface
  denying store writes or an authorized quiescent snapshot. Header inspection
  also requires a bounded child Python process; unavailable child execution
  fails without an unsafe fallback. Readable state does not imply
  permission to admit, execute or settle work.
- `record --store STORE --root KEY --expected-sequence N --type TYPE --input FILE`:
  append a typed event whose fields are listed in the contract. A stale sequence
  requires recovery, not blind repetition with a larger number.
- `admit` takes the same store/root/sequence/input arguments for an action.
  A newly created root has sequence 0 and its initial grant is named `root`;
  use `checkpoint` to recover the current sequence rather than guessing it.
  Dispatch the native tool only if `admitted` is true. Reusing an action identity
  does not authorize another execution. The caller owns the gap between receipt
  and dispatch: an interrupted dispatch is uncertain until reconciled.
- `settle` takes those arguments for a result. Bind actual output artifacts,
  observed usage and receipt origin. A final result requires resolvable selected
  content before releasing its reservation. Older revisions may describe actual
  execution; currentness of a conclusion is a separate check. Unknown execution
  retains reservations and may preserve unresolved observations. Invalid historical
  final receipts fail replay/import rather than silently becoming valid evidence.
- `audit --store STORE --root KEY`: exit 0 means recorded required acceptance
  has current evidence and no unsettled dependencies; exit 1 identifies gaps.
  Neither result establishes the truth of an attributed judgment.
- `export` and `import --input FILE` use the same store argument. Bundles contain
  private evidence. Imported summaries are rebuilt; producer claims are not
  authenticated by importing them.
- `collect-workflow --store STORE --root KEY --expected-sequence N --action-id ID`
  captures configured local corpuses through the existing workflow service. It
  needs an already authorized local-session-history scope. First run the profile
  check for `finding-friction-in-recent-sessions`; a disabled history capability
  must not be bypassed by invoking this helper through a core skill. Read the
  returned private report before using candidate findings as evidence.
  Repeating the same request for an unsettled action inspects its exact linked
  runs and published views without capturing again. Inspect all matches and
  verify effects and usage before settlement. Missing links remain unknown;
  replay preserves the original deadline and held allowance.
- `inspect-workflow --store STORE --root KEY --action-id ID` reads those links
  without capture inputs or writable access. Use it when inspecting an interrupted
  action in a restored store or after the source configuration changed. It does
  not execute or settle work; a known action without linked runs remains unknown.

Authority strings name already authorized scopes; inventing a string does not
create permission. Scope inclusion is mechanical; matching an actual action to
its authorized scope is a harness/agent obligation. Question dependencies name
exact questions; independent actions need not wait. Answers require source
artifacts and the intended respondent. This records attribution, not proof of
identity; transport must establish whose answer it is.

Child grants inherit every enforced parent resource dimension; an omitted child
allocation means zero, never unlimited. Recover the same root and grant after
interruption. Starting another root is a new investigation, not recovery, and
cannot legitimately renew an existing assignment's allowance.

`finish` with `blocked`, `stopped` or `bound` ends new work but still permits
results for already admitted actions, grant closure and question closure. A
`resume` event can reactivate a blocked or stopped root before its original
deadline: supply `source` matching the current requester, a `reason` and exact
source `evidence`. It preserves spent resources, reservations and questions.
Then reconcile pending answers against the current frame. A bound cannot be
resumed. Source attribution records the transport's observation, not authenticated
identity. Material changes to subject, objective, constraints, preferences or
acceptance require requester attribution; routine input-version updates do not.

A frame has subject, objective, requester, constraints, preferences, criteria and
inputs. Each criterion has key, description, claim_type, verification_method and
required. Criteria must include a required item. Evidence lists contain exact
record references or published artifact references. Progress is appended to an
immutable root. A changed substantive input invalidates a dependent claim, not
its historical existence.
Evidence must precede the claim within its own run: a root cannot cite itself or
one of its events, and an event cannot depend on itself or a later event in that
run, including through another run's execution inputs. Import file order is not
event order; valid bundles may list records in any order. This checks dependency
chronology, not the factual truth of a claim or timestamps across different runs.
Historical replay preserves older record revisions, but usable evidence must still
resolve its selector against the exact stored bytes. An absent selected value cannot
support passing acceptance, completion, an answer, recovery or action admission.
Research and unknown acceptance may retain unresolved references without establishing
readiness; a selected value of `false` or zero is still a resolved observation.

For `begin`, supply exactly these five top-level fields. Replace the example's
deadline with the already established UTC epoch millisecond deadline; zero below
is a placeholder and is rejected. Choose criteria, authority and accounting units
for the actual task. An omitted resource dimension is unmetered: do not reserve
it in an action. If counting actions, declare an action allowance at the root.

```json
{
  "frame": {
    "subject": "authored local artifact",
    "objective": "Verify its documented behavior",
    "requester": "parent",
    "constraints": ["local disposable files only"],
    "preferences": [],
    "criteria": [{
      "key": "behavior",
      "description": "Observed boundary cases match the supplied contract",
      "claim_type": "software",
      "verification_method": "Execute discriminating checks and inspect results",
      "required": true
    }],
    "inputs": []
  },
  "deadline_ms": 0,
  "limits": {"actions": 5},
  "authority": ["local-fixture"],
  "capabilities": {
    "admission": {
      "mode": "helper_enforced",
      "scope": "helper admissions and recorded reservations only"
    },
    "native_execution": {
      "mode": "advisory",
      "scope": "agent observes the bound; helper cannot cancel native tools"
    }
  }
}
```

`capabilities` is a nonempty object keyed by capability name. Each value has
exactly `mode` and `scope`; `mode` is one of the contract's enforcement labels,
and `scope` is a nonempty explanation of what that enforcement covers. It is not
a list, a map to plain strings, or a grant of new authority. Record the actual
host's capabilities rather than copying unsupported claims from this example.

The first investigation write upgrades the private registry to format 2 after
saving a format-1 SQLite backup under its backups directory. New readers support
both; old readers refuse format 2. To roll back, stop all writers and restore the
recorded backup into a separate private store together with the immutable objects
it references. Validate that copy before switching callers. Never overwrite the
only current store; post-backup investigation work would be lost.

## Correcting a result interpretation

Use `record --type correction` with the usual store, root, input and recovered
expected sequence. Read the versioned [correction contract](../correction-v1.json);
`contract` exposes it under `extensions.correction`, including its `sha256`.
The input has exactly `contract_sha256` (that digest), `target` (an exact record
reference), `interpretation`, `source`, `reason` and a nonempty `evidence` list.
Publish supporting output bytes first. Attribution is a claimed source, not
authenticated identity or a truth certificate.

Target the original `result` event for the first correction, or its latest
`correction` event for a further correction. Both must belong to this same run.
The correction changes only the observation's interpretation; it cannot change
the execution status, measured usage, grant or deadline. Other event kinds are
not correction targets. A repeated or competing target is rejected: recover and
inspect the existing correction before explicitly revising it. An unchanged
retry does not append another correction.

`checkpoint` retains each action's original `result` beside
`current_interpretation`. Read the latter for the current attributed claim and
its `applicability`; inspect `history`, `original_ref`, `current_ref`, `source`
and `evidence` for its provenance. The top-level `interpretations` map also
retains interpretations of earlier interrupted or uncertain result receipts,
even after an action has a newer settlement. History is rebuilt from immutable
events, not a mutable summary.

Acceptance referencing a superseded result or correction, directly or through
another record, becomes unresolved. In an active run, explicitly revalidate and
record acceptance against the current correction and relevant evidence. Raw
execution artifacts remain valid on their own; the helper cannot infer an
unrecorded dependency between prose claims. Correction support must resolve and
must not depend on the superseded claim itself.
Import also rejects completion, action admission, answers and recovery that use
interpretations already corrected at that point in the run. An event valid before
a later correction remains historical evidence; replay does not move that later
correction backward in time.

Corrections are allowed after closure or the original deadline as reconciliation.
They do not reopen work or extend a bound. A historical `status: complete` stays
in the history; `completion_current` becomes false if required acceptance is no
longer supported. Always inspect the current audit. Closed runs still cannot
record new execution or fresh acceptance merely because a correction exists.

The base v1 policy digest and registry format remain unchanged. Recording a
correction explicitly opts that run into the digest-pinned correction extension.
Before first use, preserve an `export` and stop older writers. Readers without
this extension reject its events; use the matching newer helper to read, finish
or export the corrected run. For rollback, import the pre-correction export
into a separate compatible store and retain the newer history separately. Do not
overwrite the only current store or recreate a root to renew an allowance.
