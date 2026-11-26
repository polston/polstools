# Changelog

Notable changes to the `p` plugin. Each version heading matches the `version`
field of the plugin manifests; changes made before this file existed are in the
git history.

## [1.21.4]

- Expand `writing-goals` with an optional `Baseline` slot, open-decision
  multi-goal phasing, proposed-default bound labeling, explicit repository and
  live host-state authority reconciliation, and cross-harness subagent
  delegation right-sizing (cost, speed, and reasoning depth). Align the
  Antigravity goal adapter slots with the shared contract and remove brittle
  harness-internal tool names.
- Add a cross-harness draft-quality evaluation suite (`goal_contract_quality`
  rubric, deterministic evaluator, `ModelJudge` integration, and paired
  synthetic fixtures across Claude Code, Codex, and Antigravity).
- Update `p-doctor` and `p-update` to query Antigravity's current
  `agy plugin list --json` array output while preserving fallback support for
  legacy `{"imports": [...]}` payloads.

## [1.21.3]

- Allow activation checks in read-only planning environments without requiring
  Python's temporary-file write probe. Preserve existing session overrides and
  writer locations; reject ambiguous or inaccessible policy locations instead
  of silently enabling a skill. Native Windows verification remains pending.

## [1.21.2]

- Make the investigation loop explicit after each result: continue ready work,
  revise the method, return from an enabling detour, switch branches, wait on a
  named prerequisite, or close against the actual objective and original bound.
  Keep branch completion distinct from completion of the overall run.

## [1.21.1]

- Make instruction-directory fingerprints unambiguous across file boundaries.
  New directory hashes change; saved manifests remain readable and unchanged,
  and single-file fingerprints retain their existing encoding.

## [1.21.0]

- Distinguish narrowly recognized source metadata from unresolved context in
  workflow reports. Surface compaction and checkpoint context gaps, retain raw
  selectors, and keep unsupported transport coverage explicitly unknown.

- Account for native file edits and timed waits when classifying repeated calls.
  Prioritize cases absent from candidate entries when selecting bounded inspection
  contrasts, and report omitted contrasts without implying exhaustive coverage.

- Expose recurring review decisions in workflow status, including due reviews,
  stale evidence and invalid optional metadata, without changing records or
  treating review readiness as execution authority. Keep unsupported local
  metadata visible in automatic reports without crashing, suppressing candidates
  or inventing due-review cards.

- Make pending Windows capture verification prominent in the requirements and
  workflow guide; separate it from framework development and checks on macOS.

- Anchor whole-source and planning-header reads against directory symlink
  substitution, sharing the fragment capture boundary. Capture fails explicitly
  where directory-relative no-follow opening is unavailable, including native
  Windows; no unsafe pathname fallback is used.

- Add bounded private source-fragment capture with exact byte offsets, retained
  digests and explicit omissions, without turning partial history into session
  totals or opening a workflow store.

- Clarify goal continuation after an explicit new bound, separating current work
  authorization from native resume controls and preserving unfinished outcomes.

- Surface imported effect records in workflow status so agents can find their
  pending reviews without treating prose triggers as schedules or claims as
  verified improvements. Preserve declared next actions and direct stale-evidence
  follow-up to reconciliation before reassessment.

- Add a planning skill for grounded dependencies, conditional work and revision
  from observed execution, with optional outcome-based follow-up guidance.

- Clarify that retained verification inputs include programs and data invoked by
  a saved script, rather than relying on its wrapper hash alone.

- Add a request-oriented investigation entry guide with concrete examples, expected
  evidence, requester decisions and continuation limits.

- Correct the classifier precedence description and qualify review-context claims;
  preserve existing classification behavior and the pinned review policy.

- Separate Codex structured answers from their saved questions during workflow
  measurement, retain questions as review context, and redact full legacy context
  before excerpting. Preserve answer prompts containing interruption markers.

- Coordinate cooperating annotation writers across processes, reject stale saves,
  and bound contention without multiplying waits for queued requests. Preserve
  symlink packet aliases and support long packet filenames.

- Reject unsupported or wrong-domain assessment values when loading annotation
  packets and refreshing review progress, so invalid CSV edits cannot count as
  completed reviews.

- Validate retained annotation assessments when a save omits that field, rejecting
  contradictory label changes while allowing consistent note-only edits.

- Ignore held number-key repeats when judging annotations so one press cannot
  unintentionally classify subsequent cases after a save advances the view.

- Give unnumbered annotation assessments the full button width instead of the
  narrow keyboard-label column used by numbered correction choices.

- Preserve annotation drafts across view changes, concurrent-edit conflicts, and
  edits during a pending save; show the saved judgment before reconciliation and
  keep hidden controls hidden at narrow viewport widths.

- Make annotation number shortcuts select the displayed correction choices after
  an incorrect diagnosis, with the same save behavior as clicking the choice.

- Preserve missing tool inputs through all three adapters so unknown arguments
  cannot establish repeated calls; retain observed empty values and mutation context.

- Require resolvable execution receipts before final investigation settlement
  releases reservations, while retaining uncertain observations and older receipts.

- Require observed integer counts and strict pairing metadata for usage gates;
  scope approval to explicitly compatible dimensions under accounting profile 3,
  rejecting raw cross-source input comparisons and inconsistent Codex cache totals.

- Resolve immutable artifact selectors during historical investigation validation;
  preserve older revisions without accepting evidence that never existed.

- Reject self-supporting and future-dependent investigation evidence across live
  recording and import, following existing implicit execution dependencies.

- Reject imported completion, action admission, answers and recovery based on
  interpretations already corrected at that point; preserve valid earlier history.

- Keep diagnostic follow-through conditional on evidence review; candidate cards
  neither establish independent contrasts nor require a new experiment.

- Derive dashboard coverage decisions from explicit, consistent observations;
  distinguish captured human labels from assessments and retired hook evidence.

- Expose fingerprint-bound taxonomy criteria assessment without authorizing
  promotion from label counts; report missing truth and concrete next actions.

- Keep label-calibration statistics descriptive when no sampling design is
  established, preserving observed counts and ratios with explicit analysis versions.

- Report observed source-completion input usage without unsupported bootstrap
  inference or an optional-backend dependency; retain protocol-specific utilities.

- Withhold operational-rate sampling intervals when no sampling design is
  established; preserve observed counts and show the reason in the dashboard.

- Preserve distinct Claude and Antigravity evidence IDs and tool ordering when
  a source batch exceeds 1,000 blocks, retaining ordinary existing IDs.

- Clear prior completion when a new human request arrives across all adapters;
  Codex also resets terminal state and duration when a native turn starts.

- Supply observable Antigravity call arguments and qualified assistant context
  to private review packets without inferring unlinked results or status.

- Mark omitted tool-review evidence and preserve both output boundaries within
  existing excerpt limits, after redacting the complete source text.

- Ground failure causes in tool results, recognize explicit pre-execution hook
  refusals, and retain recorded diagnosis versions when scoring older extracts.

- Assess cumulative taxonomy label support from explicitly supplied prior packets,
  rejecting incompatible or reused evidence and keeping promotion separate.

- Sum taxonomy dashboard phase counts across review rounds and retain each
  round/protocol binding instead of displaying only the last packet.

- Keep taxonomy calibration and test packets separate by normalized source/trace
  across rubrics and adaptive rounds; expose and bind the split policy.

- Recognize native file replacement and creation tools as intervening mutation
  attempts in repeated-call diagnostics; preserve unvalidated status.

- Reuse annotation publication safeguards for interpretation and taxonomy
  packets, preserving completed reviews and rejecting substituted evidence.

- Preserve existing ordinary annotation packets during sampling, reject aliased
  output and salt paths, and bind publication to the selected rows and salt.

- Keep review dashboard counts and workflow progress tied to validated packet
  captures. Refuse changed immutable evidence in the annotation UI and compare
  save revisions against the exact captured rows being edited.

- Assess taxonomy support from the same captured rows and round that passed
  packet validation; reject unsupported labels and invalid review rounds.

- Enforce adaptive annotation case budgets across rounds, require complete prior
  packet provenance, and reject invalid requests before publishing packets.

- Exclude missing call signatures from repeated-call matches and denominators,
  while retaining intervening mutation context. Version the corrected scorer and
  bind adaptive taxonomy packets to their candidate taxonomy version.
  Reject destination reuse so repeated generation cannot erase assessments.

- Reject repeated or conflicting normalized observation identities before they
  inflate report samples; preserve existing trace files when validation fails.

- Preserve observed counts and token totals when deterministic scores abstain;
  report unavailable observations as null in report schema version 3.
  Distinguish configured sample thresholds from catalogue recommendations.

- Include every reported harness in generic dashboard metrics, preserve unknown
  counts and rates, and label rate observations by their actual source. Use native
  text wrapping, visible empty states and neutral, complete interval displays.
  Keep empty taxonomy summaries renderable.

- Stop deriving specific workflow fixes from dashboard disagreement counts. Show
  review actions supported by those assessments, without inventing their causes
  or presenting pending reviews as completed rule changes.

- Deduplicate identical external annotation cases before sampling overlapping
  extracts; reject conflicting content for one case before publishing a packet.

- Resolve effect evidence from the preflighted index and artifact paths. Validate
  instruction manifests from fingerprinted claims, preserving whole-artifact
  references when their JSON pointer is omitted.

- Bind annotation UI revisions to the exact CSV bytes displayed, so replacing a
  packet during snapshot creation cannot authorize a save from an outdated view.

- Hash external annotation extracts from the bytes parsed and retain every input
  in additive `input_artifacts` provenance. Keep legacy source-to-digest entries
  only where one artifact contributed, avoiding loss when files share a source.

- Import and predict annotations from the same CSV records used for packet
  fingerprint validation. Preserve semantic fingerprints and existing loader
  callers; report logical CSV row counts for quoted multiline fields.

- Exclude human labels, notes and assessments from deterministic predictor input.
  Give callbacks a separate mapping so they cannot mutate packet rows used for
  validation; retain immutable extension fields and existing predictor behavior.

- Score prediction comparisons from the exact captured label bytes checked against
  their manifest. Preserve the manifest-only validation API and fingerprint-first
  failure behavior; changed paths cannot substitute unchecked predictions.

- Bind proposal evidence claims to the bytes fingerprinted, preserve boolean versus
  numeric distinctions, and reject invalid counts, rates and costs. Serialize
  strict JSON before publishing either proposal report.

- Require matching selected-case coverage and provenance in ordinary calibration
  reports, reusing the strict comparison validator. Reject invalid sampling weights
  and overflowing totals before reporting or overwriting legacy import outputs.

- Capture one input for optional Parquet cache conversion, row counts and source
  fingerprints. Clean unique temporary files on failure and check dependency
  availability before capture; preserve prior caches on capture/conversion errors.

- Bind report, annotation and taxonomy fingerprints to the exact normalized
  bytes parsed, so a later atomic file replacement cannot relabel older results.
  Preserve the existing records-only reader API.
- Align source-completion cost metric definition version 2 with the scorer's
  explicit observed-zero eligibility; incomplete usage remains excluded.

- Reject mixed adapter versions within one report source before scoring, rather
  than silently recording the final row's version. Homogeneous snapshots and
  different adapter versions across distinct sources remain supported.

- Correct the storage benchmark's named 30-day filter to use an inclusive
  thirty-day interval rather than a year subtraction. Workload version 2
  records the change; all backends use a shared timestamp grammar and exact
  instant comparisons, with independent boundary expectations in tests.

- Keep valid source-completion cost means when a degenerate sample leaves BCa
  uncertainty unavailable. Scorer version 3 emits null limits and an explicit
  reason instead of nonstandard JSON NaN values; malformed and reversed backend
  bounds remain errors.

- Preserve the newest committed review cutoff when reopening historical reports.
  Merge attention state at publication so overlapping reports retain all displayed
  candidate and follow-up identities.

- Require literal boolean verification before a severe candidate can override
  ordinary attention suppression. Add receipt-consistency regressions for human
  attribution, artifact comparisons and inapplicable acceptance criteria.
  Verify that displayed comparison cases retain discovery exposure and invalidate
  stale pilots after portable transfer.

- Flag captured interpretation gaps in automatic workflow reports and preserve
  the previous inspection cutoff. Intentional closed-window exclusions remain
  separate; older completed reports expose their warnings without rewriting
  source-operation history.
  Recognized Codex records with unreadable payloads also leave coverage partial
  instead of silently advancing the cutoff after skipping their content.
  Bind adapter registrations on pending-run recovery and reject resumptions whose
  injected implementation has no frozen identity, preserving original receipts.
  Bind shipped domain-pack analyzers and their configuration as well; arbitrary
  custom analyzers remain available for fresh runs. Preserve JSON scalar types
  when checking both pack configuration and adapter options during recovery.

- Preserve missing and malformed Codex token counts without aborting import;
  expose field availability and last-counter scope, including observed zero in
  usage scoring without treating the counter as verified lifetime cost.
  Include the shared Claude usage helper in frozen measurement dependencies so
  paused runs reject a changed accounting implementation before consuming work.

- Count Claude transcript usage once per message ID across the ledger, evaluation
  adapter and workflow importer. Preserve distinct tool content and anonymous
  contributions and diagnostics on superseded snapshots. Version the changed
  accounting, retain invalid final usage as missing, and exclude explicitly
  incomplete input counts from token-efficiency scoring while preserving measured
  zero counts when their coverage is explicit.

- Require explicit external-data authority for every non-synthetic-public judge
  input, including redacted and unknown classifications. Reject non-boolean
  authority flags and preserve the workflow resolver's raw-artifact restriction.
  Clarify that redacted excerpts remain private.

- Preserve Codex screenshot feedback and image-markup quotations in prompt
  evidence; an image prefix no longer implies machine origin. Keep explicit
  custom opener policies and the older Claude fallback unchanged.

- Exclude positionally classified Codex instruction/environment messages from
  prompt counts and inferred case intent, retaining raw context and origin reasons.
  Share the rule with legacy extraction and correction evidence; version the
  changed measurement without rewriting earlier frozen reports.

- Expose bounded source metadata discovery through `retro-eval-workflow inventory`
  for existing stores, preserving coverage gaps without capturing history or
  advancing review state. Reject empty source names before creating output or
  initializing a run. Optional previous-snapshot comparison preserves root
  changes and coverage gaps without treating missing files as deletions or
  equal metadata as unchanged contents.

- Correct Antigravity usage capability explanations: exported token fields can
  exist while complete, comparable accounting remains unvalidated.

- Preserve registered domain-module identity in saved results when callback
  metadata supplies a conflicting name or version.

- Honor registered discovery for native source names; keep ordered traversal
  behind the adapter callback boundary and preserve unreadable-input accounting.
  Retain readable discovery prefixes after a later error, and let Antigravity
  report unreadable sessions while continuing with readable ones.
  Preserve bounded private error locators and omitted-sample counts so transient
  discovery failures can be investigated after access is restored.
  Handle source symlink loops consistently on older Python runtimes, preserving
  partial capture and failed-receipt inspection instead of an unexpected abort.

- Preserve configured session exclusions when adding reporting-session exclusions,
  and suppress prompt/tool excerpts for excluded workflow captures.
  Apply additive exclusion composition through the shared adapter registry and
  preserve per-call exclusions in the older extraction API as well.
  Freeze declared adapter options so partial continuation rejects configuration
  changes instead of mixing study populations.
  Retain original reporting-session exclusions when continuation changes sessions.

- Clarify Codex interruption versus tool termination: retain outstanding effects
  until reconciled, and scope background-terminal cleanup to identified owned work.

- Link nested workflow plans to their investigation admission events so interrupted
  settlement can recover exact report references without repeating capture or
  resetting held allowances. Preserve unknown legacy links and multiple matches.
  Expose read-only `inspect-workflow` for recovery without original capture inputs
  or a writable store.
- Label saved reports' source-only usage separately from current all-operation
  accounting so recovery does not mistake a partial counter for total usage.

- Name the stateless method-selection executable directly in the investigation
  entry, with its canonical domain catalog, so ordinary-note work does not need
  stored-state documentation or guessed command/domain names.

- Preserve replay discovery exposure across exact captured-source copies and
  retained alias histories; a new path no longer makes reviewed bytes fresh.
  Check both preparation and first comparison without merging similar text.

- Preserve other SQLite connections' operating-system locks during read-only
  format inspection by reading the bounded header in a separate process.
- Prevent callback-reported usage from refunding provider-call admission; validate
  provider flags and raw reservations before execution while preserving measured
  local-resource settlement and explicit billing uncertainty.
  Preserve admission receipts on new completions and expose unverified legacy
  accounting; block new provider work without rewriting or resetting that history.
- Report required SQLite rollback recovery as an actionable read-only inspection
  error, retaining the journal and leaving recovery to authorized writable access.
  Distinguish an active writer's lock at opening from required crash recovery.
  Close writable connections when initialization fails, including lock refusal.
- Identify the existing Hoeffding interval as single-look inference and document
  the separate requirements for repeated or outcome-dependent monitoring.
- Assemble workflow status, delivery packets and pilot follow-ups from one
  database read view, preventing concurrent imports from combining incompatible
  record revisions. Packet case selection shares that same read view.
  Nested applicability checks preserve the caller's transaction.
- Document bounded native discovery when relevant history exceeds capture limits,
  preserving gaps and separating fragment inspection from whole-session measures.
- Report unreadable history paths and inventory truncation as coverage gaps.
  Useful sibling evidence no longer turns a partial inventory into a clean run
  or advances its review cutoff. Historical completion receipts keep their state
  while current inspection surfaces gaps already recorded in their frozen plans.
- Route inapplicable pilot status to evidence reconciliation while retaining its
  historical next action and disposition; document query success separately from
  the applicability of the inspected evidence.
- Separate independently confirmed review findings from recall: retain known
  defect capture, but require a declared reference scope and observed reference
  truth before reporting recall. Seeded checks retain their scoped denominator.
- Include dated custom-pilot reviews in automatic due-follow-up reports, with
  bounded reminders, frozen decision revisions and changed-input checks.
  Reopening a frozen report no longer suppresses an undisplayed reminder.
- Open existing stores read-only for checkpoint, audit, status, packet and export
  inspection. These commands no longer reserve a writer or initialize a store;
  missing stores fail without creation. Mutation still requires writable access.
- Reject nonboolean decision evidence flags and nonfinite computed effects;
  text such as "false" cannot establish valid measurement or operator preference.
- Bind verification receipts to the exact evidence selector, so another check
  inside the same captured file cannot satisfy the declared outcome.
- Keep malformed evidence references unknown without crashing inspection;
  reject invalid JSON Pointer escapes and array indices while preserving valid
  Unicode object keys and escaped names.
- Bind interrupted workflow runs to the shared evidence parser and tool taxonomy
  profile, preventing a resumed report from mixing classification rules.
- Add an adaptive investigation entry using compact notes unless transactional
  accounting, correction dependencies or an audit trail justify stored state.
  Preserve the pinned v1 contract and existing stored-run obligations.
- Require a standalone activation check and stop the investigation when preflight
  cannot execute; preserve absolute checkpoint bounds and verify clock evidence
  before reporting a deadline exhausted.
- Default workflow attention to incidents, new evidence and due follow-ups;
  candidate counts alone no longer recommend a weekly schedule. Periodic
  recommendations require confirmed yield and still need review-cost assessment.
- Clarify that report generation grants no authority while already authorized
  local refinement does not require repeated approval.
- Distinguish closing an investigation branch from finishing other required work.
- Bind replay labels to their registered display and evidence; reject changed
  post-prediction labels and recorded discovery exposure before first comparison.
  Preserve identical historical replay and immutable stored records.
- Record newly displayed contrast families when an interrupted inspection resumes,
  so exposed evidence cannot silently remain eligible as unseen confirmation.
- Preserve unknown implementation or change effort in design comparisons without
  inventing zero cost or selecting a cheapest alternative from incomplete evidence.
- Require affirmative design-challenge provenance and reject overflowing effort
  totals before ranking alternatives.
- Preserve exposure across corrected analysis, selection and assessment records;
  retained history cannot silently become fresh blind evidence again.
- Add optional evidence capture and custom pilot registration/review commands
  with source binding, complete assigned-unit accounting, revision checks and
  visible follow-up actions. Preserve attributed judgments and unknown benefit;
  do not execute interventions or apply decisions. Custom review exposure also
  excludes assigned families from later blind replay.

## [1.20.0]

- Treat test-summary text as an unverified inspection cue: quoted, echoed or
  rejected command text no longer asserts executed checks or positive practice.
- Avoid duplicate Claude verification cues from legacy call/result projections,
  while preserving separate reruns and original evidence. Diagnosis pack is v2.
- Document a bounded workflow-yield audit and actual recurrence checks; periodic
  review remains event-triggered pending stronger evidence of useful new actions.

## [1.19.0]

- Bind owned-process cleanup receipts to the caller's expected owner identity;
  stale receipts from another attempt cannot authorize a successor.
- Reject contradictory or incomplete launch and process-state evidence. Legacy
  receipts and calls without an independently retained expected identity fail
  closed; study controllers must migrate their gate calls.
- Preserve existing POSIX group cleanup and document its opt-in integration and
  limits, with real-process and receipt-consistency regression checks.

## [1.18.0]

- Add evidence-linked result observations to workflow capture and coverage reports,
  separating explicit call IDs, path-corroborated inferences and unresolved output.
- Preserve raw JSONL selectors, observed text size and missingness without changing
  legacy trace records, result-status capabilities or outcome scoring.
- Keep ambiguous and duplicate identities unresolved; count new observation
  artifacts against the existing derived-byte allowance.

## [1.17.0]

- Add optional read-only recovery projections with exact evidence references,
  conservative unresolved-attempt accounting and source-currentness checks.
- Add an experimental adapter for explicitly isolated local recovery services;
  unsupported evidence and stale writer barriers cannot justify execution.
- Keep summaries disabled by default: the registered comparison did not establish
  a practical benefit over ordinary notes;
  no investigation admission, settlement or response-format behavior changes.

## [1.16.0]

- Add reusable recovery judgments that separate service evidence, conservative
  submission accounting, report conformance, original bounds and record closure.
- Preserve partial and missing usage telemetry, reject malformed JSON and retain
  duplicate-event ambiguity instead of treating completed turns as measurements.
- Add a POSIX owned-process cleanup barrier for cooperative local effect writers;
  missing or inconclusive cleanup evidence blocks successor admission.
- Keep investigation execution semantics unchanged; service adapters retain
  provenance and independently verify outcome and comparison integrity.

## [1.15.0]

- Add explicit, evidence-linked corrections to investigation result
  interpretations. Recovery exposes corrected claims beside immutable original
  receipts and retains the full correction chain.
- Invalidate acceptance that depends on superseded interpretations, including
  indirect dependencies; preserve independent evidence and execution accounting.
- Keep the base investigation policy and existing roots compatible through an
  opt-in, digest-pinned extension. Closed-run corrections do not reopen execution;
  current completion is distinguished from historical completion.

## [1.14.0]

- Add a bounded investigation skill with native-harness transport, composable
  domain methods and a canonical policy for research, probes and useful questions.
- Add immutable investigation history, shared reservations, question state,
  evidence-bound acceptance and recovery to the private workflow registry.
  Format-2 writes retain a legacy backup; imports rebuild derived projections.
- Preserve the distinction between verified software behavior, attributed
  judgments and demonstrated improvement; use comparative trials to assess cost
  and benefit rather than treating structured records as a quality certificate.

## [1.13.1]

- Exclude complete automatic hook notifications from human-message counts,
  correction retrieval and annotation evidence using one shared origin rule.
  Preserve quoted markup, unknown wrappers and explicit human provenance.
- Retain content-free machine-origin observations and raw captured evidence,
  including notification-only sessions. Upgrade the Codex adapter and workflow
  mapping versions; rebuild old ledgers when their counting definition changes.
- Add regression coverage for notification boundaries, correction context,
  cross-harness human controls and migration of existing counts.

## [1.13.0]

- Add the stdlib automatic workflow runner, with source-balanced captured
  evidence, explicit capability gaps, immutable records and private artifacts,
  bounded operation receipts, corrections, and portable recovery.
- Add anchored inspection with noncandidate contrasts, separate diagnosis,
  review and design packs, scoped replay comparisons, local dispositions and
  follow-up cadence. Defaults need no manual study setup or external provider.
- Keep inferred fragments, agent interpretations, verified delivery, human
  observations and demonstrated improvement distinct. Preserve v1 effect
  records without upgrading their claims; share their binding checker as a
  library while retaining its compatibility command.
- Route the existing retrospective and improvement skills through the shared
  workflow procedure. No schedule, global policy, or ordinary response format
  is installed.

## [1.12.0]

- Remove response formatting entirely: toggle and maintenance skills, session
  and prompt hooks, payloads, controllers, envelope generation, format checks,
  and format-hook telemetry analysis.
- Keep installation diagnostics and package validation independent of hooks.
  Simplify Python launching and retain the read-only session environment probe
  without hook tracing or probe state.
- Existing format preferences are no longer read. Install the updated plugin
  and start new sessions to stop using hooks from earlier installed versions.

## [1.11.0]

- CI checks out full history, pins every action to a commit, runs on Linux,
  macOS, and Windows under Python 3.9 and 3.14, runs the stopped-promises
  self-test and a shell syntax check, checks whitespace across the whole
  committed tree, runs `p-validate --base` on pull requests, and has a job that
  runs the evaluation tests with the optional analytics packages required.
- README covers Antigravity in every install, update, and development section
  and adds uninstall commands. A `LICENSE` file is added.
- The response-format hook enters through `bin/format-gate`, which settles the
  off case in the shell and cannot block a prompt, and a Python 3.9 floor is
  declared. Antigravity gets a response-format hook, a read-only status line
  check, one plugin-root convention in every skill, and a hook contract in the
  validator and doctor.
- `p-update` reads every harness before changing any, reinstalls Antigravity
  from the copy another harness loads or from `--agy-source`, and offers
  `--dry-run`. `p-validate` runs one relocated copy and, with `--base`, requires
  a version increase.
- The repository privacy audit covers more categories and refuses a shallow
  clone. The Claude status line renderer is Python and fits the terminal width.
- Antigravity token usage is reported as not measured, and the evaluation layer
  has an Antigravity adapter.
- `statusline-ctl restore` leaves `check` and the doctor clean again, and a
  settings file kept as a symbolic link is written through, never replaced.
  The doctor no longer flags unrelated Antigravity plugins as obsolete, and a
  Codex reinstall interrupted between remove and add is finished on the next
  run. A transcript field of the wrong type is skipped and counted instead of
  stopping `retro`. The privacy audit scans long lines in linear time and
  catches UUIDs beside a hyphen. Every `bin/` script answers `--help` with 0.

## [1.10.1]

First changelog entry. The tree at this version is the baseline.
