# Automatic workflow evidence

Run `retro-eval-workflow run` for a bounded local investigation of Claude,
Codex and Antigravity history. The agent chooses and operates the study. No case
manifest, preference survey, external model service, or scheduler is required.
The original ledger and evaluation commands remain available for reproducing
their own measurements.

## Platform coverage

The investigation process and evidence contracts are OS-independent; filesystem
access is an implementation boundary. Develop and verify the framework on the
available host, recording which platform actually ran each check.

**Native Windows source capture is unfinished and unverified.** It currently
refuses to run because the safe source-opening implementation uses
directory-relative no-follow operations. Restoring Windows capture requires a
native implementation and tests of capture, containment, byte accounting and
cleanup on Windows. This gap does not block framework work or macOS checks.
MacOS results do not establish Windows or Linux runtime support. Keep this
verification item separate from whether the framework produces useful outcomes.

## Normal operation

From an installed skill, `<plugin-root>` is the absolute directory two levels
above its `SKILL.md`. `<python>` means `sh <plugin-root>/bin/python-launcher`;
quote both paths. From this repository:

```sh
sh plugins/p/bin/python-launcher plugins/p/bin/retro-eval-workflow run
sh plugins/p/bin/python-launcher plugins/p/bin/retro-eval-workflow inspect
sh plugins/p/bin/python-launcher plugins/p/bin/retro-eval-workflow status
```

The private store defaults to `RETRO_HOME/workflow`, or the local user data
directory's `retro/workflow` when `RETRO_HOME` is unset. `--store` overrides it
and must precede the subcommand. Stores, exports, labels, and findings belong
outside every Git repository. The CLI prints small receipts and absolute paths;
the agent reads the private report and inspection packet.

Source roots follow `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, and
`RETRO_ANTIGRAVITY_HOME`, with the documented harness defaults. Optional
`run --source NAME=ROOT` overrides replace the default source set. No source
command is executed. Capture precedes parsing and evidence selectors resolve
against those same digest-checked bytes. The current reporting session is
excluded where its harness exposes the necessary identity.

For an existing store, check current source metadata before deciding whether to
capture more history:

```sh
sh plugins/p/bin/python-launcher plugins/p/bin/retro-eval-workflow inventory --output /tmp/workflow-inventory.json
```

This uses the existing bounded discovery function without source-content capture,
parsing, new run receipts or cutoff changes. The store must already exist;
`inventory` never initializes it. Optional `--source NAME=ROOT`, `--file-limit`
(default 1,000) and `--runtime-seconds` (default 30) narrow discovery. The deadline
is cooperative, including registered discovery callbacks; it cannot interrupt a
blocked filesystem call. The new private output must be outside repositories,
the store and source roots. Existing output is never overwritten. Standard output
contains only its receipt and location; read the private JSON for paths and gaps.
Exit 1 preserves incomplete coverage. Changed file metadata is only a retrieval
lead, and equal metadata does not establish unchanged contents. This command does
not identify new eligible tasks or install recurring polling.

Pass `--previous /tmp/earlier-inventory.json` to include a comparison with an
earlier private snapshot (at most 64 MiB). The private output distinguishes newly
observed files, changed metadata, equal metadata and files not observed in the
current view. A missing entry never establishes deletion, even when discovery
reports complete coverage. Comparison requires the same source name and recorded
root; changed roots or missing sources remain incomparable. Different store salts
do not create artificial changes. Gaps in either snapshot remain visible and
return exit 1, as does source incomparability. The previous snapshot and store
remain unchanged; standard output adds counts only. This comparison supplies
retrieval leads, not content hashes, event freshness or task eligibility.

Configured adapter `excluded_session_ids` remain excluded when runtime reporting
session identities are added. Excluded captures retain raw bytes for replay but
produce no prompt/tool excerpts or candidate records. Exclusion is a study-selection
rule, not a promise that the source is never read or stored. Native user-role fields
alone do not prove human authorship: controller-generated sessions can carry those
fields too. Keep known generated trials out of natural-work comparisons using
explicit source selection or adapter exclusions; unknown automation origin remains
unknown. This does not reclassify or rewrite earlier frozen reports.
Codex messages with complete positional `content_item_kinds` consisting solely
of `agents_md.instructions` and `environments.environment_context` are excluded
from prompt counts and inferred task intent. Raw captures and content-free origin
reasons remain available. Missing, malformed, unknown or mixed kinds fall back to
existing wrapper rules; no new text-prefix rule discards AGENTS quotations.
This does not establish the authorship of retained requests. Current captures use
Codex adapter version 11; the legacy ledger rebuilds at row schema 13. Frozen reports
retain their original definitions until a separately identified remeasurement.
The default Codex wrapper filter does not treat `<image` as machine provenance:
image attachments can precede written user feedback. Custom opener policies still
apply, and the older Claude fallback is unchanged. This preserves textual feedback;
it does not add image interpretation or infer a request from an image-only payload
without text.
Complete observed Codex question-reply envelopes contribute their answers as user
text and their questions as review context. Unknown, malformed, quoted and mixed
shapes remain unchanged. This preserves prompts and context; it does not establish
that a heuristic correction label correctly describes the user's intent.
New plans snapshot declared adapter options. A partial run can continue only with
the same options, including exclusions; omission of a custom configuration cannot
silently restore defaults halfway through a study. This check complements installed
code/profile identities; it does not authenticate custom callback behavior.
Plans also retain the original reporting-session exclusions. Continuation combines
those with the current reporting identity so changing sessions does not admit the
original reporter into its own study. This is not a registry of all generated
sessions; other known generated work still needs explicit study exclusions.

Verification receipts bind the selected evidence within an artifact, not just
the file digest. Malformed references stay invalid rather than becoming successful
checks or crashing a packet. JSON Pointer resolution follows
[RFC 6901](https://www.rfc-editor.org/rfc/rfc6901): escaped names and Unicode
object keys are valid; array indices use ASCII digits without leading zeros.
Receipt validation checks binding and declared provenance; it does not authenticate
an external producer or prove that its attributed judgment is true.

The default plan uses a closed fourteen-day bootstrap window, or the last
completed cutoff; older sparse-source exploration is marked separately. Limits
are ten minutes, 100 candidate fragments, 1,000 source-file reads, 100 MiB captured,
25 MiB published derived evidence, three attention cards, zero provider calls,
and zero required human annotations. Header selection hints count toward capture
and read budgets. This is targeted retrieval, not a population sample. Filesystem
metadata enumeration and installed callbacks are cooperative: the deadline stops
admission of new work, rather than forcibly terminating an in-flight OS call.
The runtime receipt covers planning and source operations before report
rendering. Derived limits cover published evidence and records, not SQLite
bookkeeping or temporary memory. If planning outlasts the deadline, its observed
reads are settled without admitting source work. If the plan cannot fit the
derived allowance, a failed receipt retains the read costs without publishing
an over-budget plan. A process killed during inventory can leave unbound capture
bytes before a run receipt exists; it cannot certify zero inventory cost.

Usage counters carry a separate `usage_scope`. An immutable report's `used`
covers `source_operation_reservations`, excluding planning and report publication.
The current run result, `status` and investigation workflow inspection expose
`all_run_operation_reservations`. Use the current receipt when reconciling a
shared byte allowance; source-only counters are not the total. These counters
retain uncertain reservations and do not establish external costs. Legacy report
bytes remain unchanged and may lack this scope field.

`run --limits FILE` accepts a JSON object overriding those positive integer
limits; external-call and human-annotation limits may be zero. It does not grant
transmission or execution authority. `run --pack NAME` narrows enabled packs.
Exit 0 means clean; 1 means findings, coverage gaps, or a partial receipt; 2 means
the operation could not run. A source missing all eligible evidence cannot be
presented as successful coverage merely because another source worked.
Unreadable directories and truncated inventories remain coverage gaps even when
readable files produce useful findings. These runs remain partial and do not
advance the committed review cutoff. Current inspection of an older completed
receipt preserves its historical state but returns a finding exit when its frozen
plan records an inventory gap; it does not repeat the old clean-coverage judgment.
Captured parse errors, unknown record types, unreadable Codex message/event/metadata
payloads and invalid window timestamps also remain interpretation gaps even when
other records are usable. This is not exhaustive nested-schema validation. Their reason
counts appear in the report, return exit 1 and prevent a new committed cutoff.
Records deliberately excluded after the closed window do not create such a gap.

Fresh captures under source mapping version 5 separately report
`observations.record_coverage` and `coverage.<source>.record_classification`.
Classification version 1 recognizes closed, validated shapes for Codex
`token_usage_record`, Claude title/mode/permission metadata, an empty `atis-latch`,
and the numeric token-budget attachment with its exact optional rendering.
Recognized metadata is counted and anchored to the original raw record; it does
not create a warning, supply missing usage totals, or create a user turn or tool
result. Unexpected fields or invalid shapes remain `invalid_metadata_record`
gaps rather than inheriting the familiar envelope's treatment.

World-state records, compaction summaries, non-budget attachments and Antigravity
system/checkpoint records remain `unmapped_context_record` gaps. They may carry
instructions, queued prompts or asynchronous results. Their raw JSONL selectors
retain the evidence for inspection, but do not establish its user attribution,
delivery, chronology or call association. This adds coverage warnings for some
previously accepted compaction envelopes. It is not exhaustive nested-schema
validation; unclassified unknown types retain `unmapped_record_type` warnings.
Older outputs without record classification report null counts and an unmapped
source count. Their frozen diagnostics, receipts and cutoffs are not reclassified;
only a new bounded capture can use the new rules.

Inspect the retained diagnostics and preserve the source history. Repair the
importer or obtain a complete export before a new bounded run; repeatedly
resuming the same capture cannot repair its interpretation.
Older completed runs retain their terminal history and prior cutoff while current
reports expose the known warnings. Newly added parser checks require a new bounded
capture; reopening an old report does not reparse its retained source bytes.
Historical inspection cannot move the committed review cutoff backward. Report
publication merges display history under the writer transaction so another
report completed during rendering does not lose its candidate or follow-up marks.
Pending runs also bind the selected adapter's module/class registration, options
and bundled measurement files. Changed registrations are rejected before further
capture. Arbitrary injected factories or modules outside the frozen composition
can run in a new bounded invocation, but cannot resume as verified unchanged
implementations. Their closure state and external dependencies have no frozen
identity. Completed historical runs remain inspectable.
Domain packs likewise bind their shipped analyzer and capability, claim and
measurement configuration. Recovery compares canonical JSON identities so a
boolean, integer and decimal do not silently substitute for one another. Custom
analyzer callbacks can run in fresh bounded invocations, but partial continuation
requires a shipped analyzer covered by the frozen measurement files. Declared
pack versions alone cannot establish unchanged callback state. This does not
isolate hostile code running inside the same process.
An enumeration failure preserves readable paths already yielded, with an explicit
coverage gap. Cooperative adapters may continue to other sessions. The private
report's `plan.sources` retains bounded error locators and omitted-sample counts
for investigating transient failures; an unavailable locator remains unknown.

`status` is a read-only query: its successful exit means the query ran, not that
the inspected runs succeeded or their evidence remains usable. Read
`current_applicability`, the per-run `result_applicability`, and each pilot's
current applicability before acting. Missing evidence can invalidate a retained
decision while its historical disposition remains unchanged.
An inapplicable pilot's immediate `next_action` directs evidence reconciliation;
`recorded_next_action` preserves what its retained decision originally requested.
Restoring the exact dependency restores the original actionable view. Corrected
inputs instead require explicit review; inspection does not revise the decision.
Status assembles record-backed fields within one SQLite read transaction, so a
concurrent import cannot mix a pilot with decisions from before that import.
This may delay a writer's commit while inspection runs. Artifact files are still
checked separately; the query does not freeze external files or promise that
its observations remain current after it returns.

`status.recurring_followups` exposes the latest local `disposition` decisions,
including undated and inactive ones, with exact decision and evidence references.
Its `attention_status` separates due `ready_for_review` items from
`waiting_for_due_time`, `trigger_unknown`, `inactive`, and
`needs_reconciliation`. A due time means a review is due; it does not establish
benefit, quality, satisfied prerequisites, or execution authority. Missing dates
remain null, and dismissed or retired attention remains inactive. Missing or
superseded evidence takes precedence over readiness and contributes to aggregate
`current_applicability`; its reconciliation action preserves the original in
`recorded_next_action`. Inspection does not consume follow-up cards, schedule
work, revise judgments, or change the local disposition.
Portable decision records may omit local attention extensions. Missing state
remains unknown even with a due date. Unsupported states or malformed candidate
identifiers produce `invalid_metadata` with field-specific
`attention_metadata_issues` and an action to inspect and explicitly correct the
record if authorized. Evidence reconciliation still takes precedence when
inputs are stale, and metadata issues remain visible. These diagnostics do not
rewrite records or turn evidence currentness into validation of local metadata.
Automatic reports use the same interpretation of optional local fields. Unknown
or invalid local attention cannot suppress candidates or create a due-review
card; the report exposes `local_attention_metadata_unusable` with the exact
decision reference and directs the agent to inspect status. A completed source
run with this unresolved diagnostic is not a clean report. Correcting the
decision explicitly restores normal review selection without erasing history.

If a relevant source cannot fit the capture allowance, inspect the coverage
receipt before concluding that no useful recent work exists. A recent filesystem
timestamp does not establish recent events. The agent may use bounded native
navigation of an authorized source to decide whether a larger capture is worth
the cost. Preserve exact private fragments, original byte offsets, read costs,
source identity and skipped boundaries. Retain the request and surrounding
messages, rather than only the latest tool output. Do not link context across
uncaptured or unreadable records. If the source changes, take a fresh bounded
capture and verify the relevant saved anchor before reconciling the observations.
Metadata equality establishes observed stability, not an atomic snapshot.

For reproducible bounded navigation, the agent can use the stateless fragment
command with a chosen external destination whose parent already exists:

```text
retro-eval-workflow fragment --source-root ROOT --file RELATIVE_FILE \
  --tail --max-bytes 1048576 --output NEW_EXTERNAL_DIRECTORY
```

Use `--offset BYTE_OFFSET` instead of `--tail` for an explicit range. The helper
reads at most the selected byte limit (default 1 MiB, maximum 64 MiB) and saves
exact bytes in `fragment.bin`, with offsets, digest, omitted ranges and source
metadata in `capture.json`. It neither opens a workflow store nor runs an adapter
or domain pack. Output must be outside repositories, the source root and the
configured store. It is private and never overwritten. An interrupted directory
without `capture.json` is incomplete; inspect it rather than retrying into it.
The completion receipt is published last. Exit 1 flags a partial or changed-source
capture; exit 2 means capture/publication could not complete. Exit 0 means the
observed whole file was captured with unchanged metadata, not that a session or
task was complete. Metadata checks do not establish an atomic snapshot.
Source opening traverses directory descriptors without following replacement
symlinks. Whole-source capture and planning header reads use the same boundary.
These capture paths fail closed on platforms without directory-relative no-follow
opens; they do not fall back to a weaker pathname check. In particular, this
implementation does not support native Windows source capture. Existing frozen
evidence can still be interpreted independently of live source opening; a native
Windows capture implementation requires separate verified filesystem handling.

Fragments can begin or end inside JSON records or encoded characters. Preserve
those raw bytes and boundaries; do not silently decode, concatenate or normalize
them as a complete session. The agent still chooses surrounding context and
checks the native source format before interpretation. The helper does not
establish request boundaries, authorship or linked outcomes.

Such navigation is discovery only: do not feed a tail or concatenated ranges
through whole-session normalization, count missing history as zero activity, or
promote related messages into independent tasks. Keep the public run's coverage
gap visible. A useful fragment can justify further investigation without proving
delivery or an improvement effect. Stop expanding capture when additional context
cannot change the decision within the investigation's original bound.

## Agent interpretation and action

1. Read coverage, populations, unavailable fields, and current applicability.
   A session fragment is an inferred boundary, not an independent workcase.
   Assistant completion and executed tests alone do not establish delivery.
2. Read `inspect`'s source/category-balanced candidates and noncandidate
   contrasts. `--candidate KEY` retrieves a specific finding; `--run ID` selects
   a historical run. Record open notes and counterexamples outside Git.
   The bounded contrast selection first covers captured cases absent from the
   selected candidate entries, then favors distinct cases before repeating one.
   It still shows at most two contrasts per source. The packet reports available
   and omitted contrast counts; coverage is not exhaustive or representative of
   a population. Selection rule version 3 preserves earlier inspection records.
   Inspection is assisted discovery exposure, never blind calibration truth.
3. Retain only supported interpretations. For each retained finding, preserve
   source/selector, context, uncertainty, contrary evidence or search limits,
   concrete action, owner, prerequisites, and observable acceptance. Explicit
   human approval applies to its stated context; do not invent satisfaction.
4. Choose a direct mechanical repair, a scoped preference decision, a feasible
   comparison, or no change according to the evidence. No-change and retirement
   are useful outcomes. A model confidence score is not an effect estimate.
5. Use `disposition KEY STATE --reason TEXT [--due-at ISO-TIME]` for local
   attention state. States include active, snoozed, dismissed, provisional,
   adopted and retired. These records never apply global changes or assert
   benefit. Snooze requires a due date; due actions reappear once per revision.
   Returning a finding to `active` does not erase its display history. To request
   another review of an already displayed finding, supply `--due-at` with the
   intended review time (a past or current time makes it due on the next new run).
   Reopening a historical report preserves its original cards; it does not create
   another reminder or repeat the action.

Repeated-call taxonomy version 7 recognizes `edit_file` and `write_file` as
mutation tools and `sleep` as polling, alongside the existing names. This avoids
calling a rerun after a recorded edit unchanged work. It does not prove that an
edit succeeded, that it affected the repeated call, or that every poll is useful.
Multi-action tools such as `manage_task` and `schedule` still require contextual
review; their names alone do not establish polling. Historical measurements keep
their recorded versions. The shared scorer is version 8 and the metric version 7.

`pilot prepare` chooses the first feasible installed replay procedure and writes
a blind-label packet before predictions run. The initial procedure evaluates
an exported user-request envelope against the original retrieval rule. The
agent saves a completed copy of the packet, changing only `label`, `reason`, and
`assessor_type` for each unit. Every unit needs a label or explicit null abstention
with a rationale and attribution. Run `pilot compare --labels FILE`; the command
checks the unchanged rubric, displayed prompt/context, and exact evidence and
case identities against the registration before publishing a comparison.
Agent semantic judgments remain distinct from human truth. Discovery source
groups are excluded both at preparation and first comparison, but source grouping alone
does not prove independent tasks. The descriptive result cannot authorize an
efficacy claim. A retired intervention is not silently tried again.

Do not run assisted `inspect` on held-out pilot families before comparison.
If discovery exposes them after registration, preserve that evidence and collect
new unexposed families before preparing another pilot. Resumed inspections record
newly displayed contrasts even when candidate keys do not change. The checker
can detect recorded exposure, not unrecorded viewing outside this workflow.
Correcting an analysis, selection or assessment does not erase its earlier
recorded exposure. Retained revisions still exclude those families from fresh
blind comparisons; current applicability and prior exposure are separate facts.
Exact raw-capture digests connect source-path aliases across retained snapshots.
Copying a reviewed capture to another path, then extending that known alias, does
not make its family fresh. This conservative check survives export/import; it
does not recognize every transformed copy or infer linkage from similar redacted
text. It also does not establish independent tasks among unexposed sources.

The first comparison freezes the submitted labels. An identical submission
recovers that historical analysis; changed labels after predictions were exposed
cannot become a new blind comparison. Preserve corrections as attributed review
notes and use fresh held-out evidence for a new comparison. Invalid or superseded
inputs require recovery or a new registration; `status` identifies stale evidence.
An attributed correction to the computed analysis cannot replace its original
result during an identical replay; that replay reports the superseded dependency.
Rejection exits 2 without publishing labels or analysis. Old records and bundles
remain readable without migration or retroactive certification. As before, a
pending pilot registered against different execution code needs a new registration.

For a different supported question, the optional `pilot register --spec FILE`
and `pilot review --spec FILE` commands provide a recording boundary. The
[custom-pilot procedure and templates](skills/reviewing-improvement-effects/references/custom-pilots.md)
bind an inspected finding to its source evidence, justification, comparison,
assignment, quality guardrails, budget, rollback and checkpoints before grading.
`evidence FILE --sha256 HASH` captures an explicitly supplied external artifact
without executing it. The agent performs the already authorized comparison.
Use a direct repair when a pilot would add no useful evidence; this contract
has substantial authoring and review cost.

Custom review accounts for every assigned unit and guardrail and records an
attributed decision with a next action. `status.pilot_followups` shows pending
reviews, due dates and current applicability without collecting more history.
Explicitly dated custom reviews also enter automatic reports when due, sharing
the existing card limit and once-per-revision display suppression. A pilot's
keep/revert judgment does not suppress discovery candidates. Undated pilots
remain in status; no deadline, schedule or execution authority is invented.
Reports freeze the decision revision and flag changed dependencies before action.
Reopening a frozen report preserves its cards without marking another pending
reminder as displayed.
Decisions do not apply changes or prove benefit. Identical submissions are
idempotent; changed decisions preserve revisions and reject stale writers.
Custom-reviewed families remain discovery evidence for the installed blind
replay, including pending or unknown assignments. Existing typed APIs, installed
replay and v1 effect records remain available; no new record format is required.

## Review, design, and follow-up

The review pack retrieves review opportunities; `evaluate_review` evaluates
findings against separately established defect truth. Count duplicates once,
retain confirmed consequential singletons, and distinguish unknown findings,
escaped known defects, harmful fixes, effort, and usefulness. Reviewer agreement
is not an oracle. Seeded recall applies only to the seeded denominator.
The helper's `oracle_scope` defaults to `seed_set` for seeded checks and
`unknown` for independently verified natural findings. Verifying reported
findings alone (`reported_findings`) establishes their truth, not how many
defects were missed: `recall` remains null. `known_defect_capture` describes only
the supplied known positives. An explicitly declared `independent_reference_set`
permits recall against that reference set when all of its truth labels are
observed; it does not establish recall over every defect in a project. The
helper preserves declared scope but cannot authenticate its independence.
This follows the denominator distinction in
[TREC's evaluation measures](https://trec.nist.gov/pubs/trec14/appendices/CE.MEASURES05.pdf);
the [CrisisFACTS evaluation](https://trec.nist.gov/pubs/trec32/papers/Overview_crisis.pdf)
also discusses how facts absent from submitted outputs can be absent from pooled
reference judgments. No retrieval estimator or new dependency is introduced.

The design pack retrieves design opportunities; `evaluate_design` compares
current acceptance and a separately prepared, withheld change challenge. Include
a simpler alternative, actual initial/change effort, regressions and attributed
maintenance judgment. A challenge-specific result is not universal proof of
maintainability. Express initial and change effort in the same unit across
alternatives. Missing or null effort remains unknown; it is never zero. If any
otherwise eligible alternative has unknown total effort, the comparison retains
its acceptance results but names no least-effort alternative. If historical
artifacts or an independent oracle are absent,
retain a concrete next test instead of retroactively inventing acceptance.

Recurring invocation uses the same runner. It checks due actions, suppresses
unchanged displayed evidence, and defaults to incident, new-evidence or due-followup
triggers. Candidate volume and a legacy interval do not establish useful yield;
`cadence_days` is null until confirmed outcomes support a periodic candidate.
The current runner does not infer those outcomes from candidate counts. No command
installs a schedule. Review useful confirmed findings,
completed decisions, false alerts and observed review cost before increasing
frequency or adopting a rule. Human attention is unknown unless actually observed.

A recurring report is not a new independent statistical test. The optional
`paired_effect(..., method='hoeffding')` interval reports
`coverage_scope: single_prespecified_look`: its confidence applies at one planned
sample size, not to repeated inspection until an encouraging result appears.
For several planned analyses, the agent must justify error control across those
looks and the candidate/outcome comparisons. Outcome-dependent stopping needs a
validated sequential method; otherwise retain descriptive results or wait for
the registered analysis point. Early rollback for observed harm remains allowed.
This distinction follows research on
[continuous monitoring](https://pubsonline.informs.org/doi/10.1287/opre.2021.2135)
and [time-uniform confidence sequences](https://arxiv.org/abs/1810.08240).
The helper does not validate the stopping rule or supply a sequential estimator.

Missing outcomes are a separate problem from interval width. `paired_effect`
summarizes observed matched pairs and lists excluded rows; its interval does not
cover an assigned cohort merely because many observed pairs remain. Inspect why
outcomes are missing before asserting `design_valid` or `measurement_valid`.
For binary outcomes, `missingness_bounds` retains every assigned unit and shows
the possible realized-cohort effect, without claiming a confidence interval.
For example, four successes and six unknown outcomes cannot establish superiority
over seven successes and three failures: the effect can range from minus to plus
thirty percentage points. More favorable observed-only precision cannot resolve
that ambiguity. Stronger selection assumptions need separate justification; see
[research on treatment effects with sample selection](https://www.princeton.edu/~davidlee/wp/resrevision8.pdf).
The generic decision helper checks declared validity flags, not their truth;
custom pilot reviews likewise preserve attributed judgments rather than certify them.

## Recovery and extension boundaries

`run --resume ID` keeps the original deadline, budgets, source authority and
measurement composition. The composition includes the shared evidence parser
and tool-classification profile; changed code or classification rules need a new
bounded run. Committed work is reused; ambiguous provider execution retains its
reservation and cannot be retried as free work. Known failed local attempts need
a new attempt identity.
Plans started by an investigation capture include an optional `invocation_ref`
to its admission event, also exposed by `status`. Explicit resume cannot attach
or replace this reference; ordinary standalone and legacy plans may omit it.
The link survives export/import and supports inspection after interrupted parent
settlement. It does not authorize execution or prove effects, and recovery does
not infer missing links from run order or timestamps.

Successful provider-accounted callbacks retain their provider-call reservation
even if their returned usage claims fewer calls. A no-charge receipt does not
refund call admission. Measured local resources can still settle to actual use.
Status identifies provider operations whose original admission is unverified.
Older completed provider records without an admission receipt remain inspectable
and replayable, but block new provider work on that run: their stored usage may
have refunded the original reservation. Imported unfinished provider operations
also require a positive retained reservation. Local work remains available under
the original bounds. Billing reconciliation cannot reconstruct admission, and no
automatic legacy migration is provided. Recovery requires independent admission
evidence and a separately verified migration preserving the original run and
deadline; resetting the run or inventing an allowance is not recovery.
These are admission bounds for trusted callbacks, not external billing enforcement
or a guarantee that callback code cannot perform unreported work.
Immutable report revisions preserve history; `status` checks exact dependencies,
including superseded records and missing or changed artifacts. Delivery packets
read case selection, outcomes and record applicability from one committed database
view. Artifact files are checked separately; they are not part of SQLite's snapshot.

`status`, `packet` and `export` open an existing store read-only. They neither
initialize a missing store nor reserve its writer. Export still writes its
explicitly requested destination. Inspection supports the registry's default
rollback journal. A WAL header observed before opening is rejected, but that
check is not synchronized with another process changing journal mode. A concurrent
change can let SQLite create auxiliary files despite its read-only connection. The bounded header check runs in a fresh Python
process: opening and closing a raw database descriptor in SQLite's own process
can cancel its POSIX locks. Inspection therefore requires child-process execution;
if that is unavailable it fails without an unsafe fallback. See SQLite's
[descriptor-lock warning](https://www.sqlite.org/howtocorrupt.html).
Use an authorized writable export and import that
private snapshot into a separate store when such a snapshot is needed. Do not
change journal mode concurrently with inspection. When filesystem nonmutation
must hold despite externally changing journal mode, use an execution surface that
denies writes to the store, or an authorized quiescent snapshot. The helper's
read-only option alone does not provide that stronger guarantee. A denied sidecar
creation can make inspection fail. Read access grants no mutation or execution
authority.

A crashed writer can leave a hot journal that SQLite must roll back before
inspection. Read-only commands report `store_recovery_required` and leave the
database and journal untouched. Recovery requires authorized writable access;
retain the journal, allow SQLite to recover, then retry inspection. Journal
presence alone does not prove a crash: an active writer may own it. See SQLite's
[rollback recovery rules](https://www.sqlite.org/lockingv3.html#hot_journals).
An active writer holding an exclusive lock instead produces `store_busy` at
read-only opening; retry inspection after that transaction releases its lock.
Some system SQLite builds report lock refusal as an I/O error; that error is
not automatically classified as retryable or as required rollback recovery.

`export FILE` and `import FILE` preserve records, artifact bindings, revisions,
run receipts, reservations, deadlines, and local planning state. Import conflicts
do not change visible metadata. Private bundle import does not grant external
disclosure authority: a self-declared public synthetic artifact acquires a
private classification at this boundary. Staged orphan bytes after interruption are not
published evidence. `manifest FILE` supports explicit cases and corrections;
automatic discovery does not require it. `effect-import --record FILE
--evidence-index FILE` preserves existing v1 declarations without promoting their
authored judgments into verified effects. `status.legacy_effects` exposes their
proposal identities, declared review triggers, evidence applicability and next
actions without capturing more history. These retained declarations remain
separate from registered pilots: prose triggers are not parsed into due dates,
no job is scheduled, and an imported keep does not establish benefit.
`declared_next_action` preserves the author's original instruction as context,
not execution authority. If bound evidence is missing or superseded, the current
`next_action` directs reconciliation and `recorded_next_action` preserves the
registry's earlier reassessment instruction.

The stdlib package `retro_eval.workflow` owns records, registry, source capture,
planning, runner, inspection, packs, analysis and recurring attention as separate
modules. `run_automatic(..., packs=..., adapters=...)` accepts trusted registries;
new domain packs need no dispatch edit. Pack execution requirements are separate
from claim requirements. The registry supplies saved pack names and versions;
callback result metadata cannot replace those registered identities. This binds
labels to registration, not to authenticated callback code or valid conclusions.
Source methods and versions accompany observations;
unsupported usage stays null and cannot become a cross-harness efficiency claim.

See [EVALUATION.md](EVALUATION.md) for the existing adapter, rubric, and privacy
boundaries, and `plugins/p/tests/` for executable coverage. Actual corpus
artifacts and findings are private and are never bundled with the plugin.

## Result observation coverage

Captures under source mapping version 4 or later include `observations.tool_results`
and a `result_observations_ref` pointing to a private metadata artifact. Existing
`run` reports expose per-source `coverage.<source>.tool_results` with measured
association counts and mapped/unmapped source counts. Old captures remain readable;
no new observation is inferred for them. Reprocess frozen bytes in a new run to
obtain the new mapping. Existing resumed operations retain their original outputs.
The new `jsonl_pointer` selectors require this release's resolver. Older resolvers
cannot consume these new references; existing selector formats are unchanged.

Result observation version 1 separates `identifier_bound`, `path_corroborated`,
`adjacent_context`, `ambiguous` and `unavailable`. Claude and Codex use explicit
unique call/result IDs; duplicate occurrences abstain. Antigravity can corroborate
a single file read from its adjacent exporter path header and ordered timestamps.
That inference never becomes a call-ID match or tool-success claim. Other GENERIC
steps remain context. Legacy traces, status capabilities and scores are unchanged.

Each call has private raw references with a one-based `jsonl_pointer` line and a
JSON pointer within that line. `resolve_evidence` verifies the artifact digest and
resolves the original bytes, including original line positions after filtering.
References retain `private_raw` classification; metadata does not copy arguments,
paths or result text. Duplicate-result candidate references are bounded to four
per call, with an explicit omitted count and the complete raw artifact retained.

Observed sizes count Unicode characters in captured text fields, including source
wrappers. Empty observed text is zero; missing, null and unsupported shapes have
distinct reasons and null sizes. A textual truncation notice is only a reported
signal. Completeness stays unknown, and these counts are not generated output,
delivered context, tokens, billing or time saved. An association establishes
neither task correctness nor whether a repeated read was useful.

All observation artifact bytes count against the derived-byte allowance. A report
with no mapped sources has null result counts; it does not report zero activity.
Source versions are explicit where available and otherwise unknown; supported
boundaries and validation requirements are enforced by the workflow test suite.

### Verification cues and periodic use

Diagnosis pack v2 retrieves test-summary-shaped text without certifying execution
or positive practice. A tool result may quote a proposed command, echo a report,
or describe a rejection. Inspect anchored source context before crediting a
check; even genuine check output does not establish overall task acceptance.
Claude call and result projections produce one cue per call, while distinct
reruns remain separate. Raw and normalized evidence remain available.

The actionable-yield evidence supports event-triggered inspection, not a general
weekly schedule. Request cues
are opportunities to investigate, not demonstrated review or design benefits.
Recurring attention drains an existing backlog before becoming quiet; due
follow-ups appear once per decision revision, and changed evidence can create a
new cue. Suppression does not delete evidence or prove an intervention helped.
