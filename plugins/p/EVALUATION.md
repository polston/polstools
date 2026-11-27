# Local agent evaluation

For automatic workflow diagnosis, review/design mining, and improvement
follow-up, start with [WORKFLOW.md](WORKFLOW.md). `retro-eval-workflow` adds a
stdlib-only immutable record/artifact registry, bounded runner, automatic
planner, domain packs, inspection packets and local replay comparisons beside
the existing commands below. It reuses their adapters and text rules; it does
not rewrite old normalized traces or promote legacy defaults into observations.
Its partial-run receipts report each source separately, so one missing source
does not erase valid work from another or become successful cross-source coverage.

Use this layer when a seven-day Retro pack is too narrow: cross-harness
comparison, scorer calibration, held-out labels, cost analysis, capability
gaps, or reversible improvement proposals. The original `retro.py` commands
remain the dependency-free workflow for Claude, Codex, and Antigravity
transcripts. The evaluation layer has adapters for all three. Antigravity
traces carry population `unknown` (counted as unclassified, never main) and
`unavailable` token, cache, and tool-result status capabilities, so those
metrics report `not_observable` rather than zero. The additive workflow result
observations can expose path-corroborated Antigravity content without changing
those legacy status capabilities; see [result coverage](WORKFLOW.md#result-observation-coverage). The adapters exclude non-user
Codex threads, so an evaluation report and a pack may legitimately disagree
about corpus size. They may also disagree on token totals for the handful of
rollouts whose cumulative token counter resets mid-file: the ledger banks
across resets, the adapter takes the last count.

The adapters and the `retro.py` ledger share the redaction and user-turn rules
(`retro_eval/text_rules.py`) and otherwise differ on four deliberate points,
each pinned by `tests/test_eval_ledger_parity.py`:

1. The Claude adapter counts a prompt only when `promptSource` names a person
   or `origin.kind` is `human`. Unsourced legacy records and `isMeta` records
   are excluded, not guessed.
2. A Codex rollout holding only harness wrappers is a ledger row with zero
   prompts, but it is not an evaluation trace.
3. The adapters drop timestamps that carry no zone. The ledger keeps them.
4. Ledger failure columns and the tool-failure taxonomy are different
   vocabularies. Compare them only through the pinned mapping.

Every `retro-eval-*` command uses the plugin's exit-code convention: 0 ran
clean and flagged nothing, 1 ran clean and flagged something (for example a
taxonomy assessment that is not ready), 2 could not run. A missing source root,
an unregistered source name, an abbreviated commit, or an input that yields no
cases is a 2 with one `error:` line, never an empty success. For `retro-eval-extract`
that includes any requested source root that yields no included trace: nothing is
written and the previous snapshot is left as it was. `retro-eval-report` refuses a
snapshot with no traces.

## Architecture

| Boundary | Versioned definition | Implementation shipped in v1 |
|---|---|---|
| Sources | `profiles/sources.json` | Claude, Codex, and Antigravity adapters; `profiles/capabilities.json` is the capability vocabulary every adapter must declare in full |
| Trace projection | `profiles/semconv.json` | reserved seam: `retro_eval/semconv.py` maps records onto OpenTelemetry GenAI and OpenInference attributes; no command emits projected spans yet |
| Storage | injected trace-store and benchmark registries | canonical JSONL; optional regenerable Parquet cache |
| Metrics and rubrics | `rubrics/metrics.json`, `rubrics/rubrics.json` | provisional catalogues with explicit denominators and abstention |
| Scorers | `rubrics/scorers.json` | repetition, outcomes, tool failure, and input cost |
| Evaluation policy | `rubrics/policy.json` | splits, evidence gates, ranking order, and evidence limits |
| Labels | external JSONL plus manifest | human, deterministic, and model-judge records; loopback review UI. `retro_eval/judge.py` is the provider-neutral judge interface; no shipped command calls a provider, and live judge predictions need separate authority |
| Coverage | metric catalogue joined to source capabilities and scorer results | every metric is measured, insufficient, unavailable, or unscored per source |
| Instruction provenance | external versioned manifest | generic source kinds, activation boundary, privacy class, and content hashes; reports carry only the manifest SHA-256 |

The seam is intentionally small. Source syntax stays in its adapter; mutable
policy stays in JSON. A new adapter is one profile entry plus one adapter class.
A new scorer is one profile entry plus one class. Neither change edits pipeline
or reporting call sites. A new storage implementation is injected into
`EvaluationPipeline`; a new benchmark backend is one registry entry, not a
dispatch edit. Namespaced span kinds and catalogue annotations round-trip
without changing the core enum or loader.

Source discovery is supplied by a profile's `glob` or `module:callable` entry.
Automatic workflow inventory honors that callback even when replacing discovery
for a built-in source name. Native Claude/Codex callbacks supply ordered traversal;
root-only extension callbacks remain valid. A callback may explicitly accept
`stopped` and `on_error` keyword hooks for cooperative deadlines and coverage
reporting. Inventory still checks root containment, file type and its source quota
before capture. Matching non-files remain visible to extraction as unreadable
inputs; they do not become workflow captures.
If discovery later fails, inventory retains already yielded readable paths and
reports incomplete coverage. The native Antigravity callback uses the error hook
to skip an unreadable session and continue to other sessions; it does not silently
substitute a shorter export when the preferred export is unreadable. Without an
error hook, direct discovery still raises. Partial workflow runs do not advance
the completed-history cutoff.
Private inventory and saved plans retain up to sixteen structured enumeration
errors per source: exception type, errno, and a lexical root-relative filename
when available. Missing, outside-root, invalid or overlong filenames remain
unavailable; exception prose is not retained. Error and omitted-sample counts
count observed notifications, not unique files or population prevalence. These
locators preserve transient failure evidence; they do not authorize repair or
establish that a callback supplied an authentic path.
Source resolution preserves non-strict semantics across supported Python
versions. Filesystem stat/open operations report symlink loops as coverage or
capture failures; readable siblings remain usable. Resolving a name alone does
not establish readability, and capture still checks containment before opening.
Adapter exclusions are additive: profile exclusions remain when runtime exclusions
are supplied. Extraction also retains per-call adapter exclusions when adding its
reporting-session exclusions. Other adapter options keep their ordinary runtime
override precedence. This selection rule does not detect unknown generated work.

Annotation packets follow the same rule. Dataset and rubric identity come from
the packet manifest; deterministic predictors are configured as
`module:callable` rubric extensions. The immutable packet fingerprint covers
case identity, source, split, true character counts, redacted evidence, the
rule-based scorer's proposed label and its reason code. It deliberately excludes only
assessment, corrected label, and notes. Comparison refuses partial
case intersections, mismatched dataset/rubric metadata, or prediction files that
do not resolve through their manifests.

Each deterministic report embeds a closed dataset manifest: adapter, schema,
rubric, and scorer versions; source-set and trace fingerprints; time bounds;
selection rules; local split-seed fingerprint; populations; and creation commit.
Source paths and the secret split material remain external.
Normalized JSONL reads and writes reject duplicate span identities, multiple root
records for one trace, and trace identities shared across sources. Repeating or
concatenating the same observations must not satisfy sample-size gates. Partial
traces remain readable; distinct identities alone do not establish independent
samples. A failed write preserves the previous normalized file.

Instruction-source manifests are also external. Create one at each rule
activation boundary, then bind the exact manifest while extracting. The writer
hashes files or directory trees and never persists their paths or content.
Starting with plugin 1.21.1, directory fingerprints use a versioned encoding of
relative filenames and fixed-size per-file SHA-256 digests. Recomputing a directory
fingerprint from an older release produces a new identity even for unchanged
contents; preserve the old manifest and create a new observation instead of
rewriting historical evidence or equating the two hashes. Saved schema-v1 manifests
remain readable without rehashing, and single-file fingerprints are unchanged.
A content fingerprint identifies recorded bytes, not proof a session loaded them.
Extraction rejects any included session without a timestamp at or after the
bound activation boundary; extraction and dataset schema v3 record resolved and
unresolved coverage counts.

```text
<python> <plugin-root>/bin/retro-eval-instructions write \
  --output <RETRO_HOME>/instructions/<version>.json \
  --activated-at <ISO-8601-timestamp> \
  --source standing_instructions=<instruction-file-or-directory> \
  --version standing_instructions=<version>
```

P1/P4 taxonomy packets are external too. Sample them with the same source roots
and 32-byte extraction ID salt used to create the normalized trace snapshot so
redacted tool inputs and results join the correct spans. Raw source content is
read only during packet creation; normalized traces and repository files never
receive it.

```text
<python> <plugin-root>/bin/retro-eval-taxonomies sample \
  --traces <RETRO_HOME>/cross-harness-v1/traces.jsonl \
  --output-dir <RETRO_HOME>/annotations/proposal-taxonomies \
  --source-root claude=<claude-source-root> \
  --source-root codex=<codex-source-root> \
  --id-salt <RETRO_HOME>/cross-harness-v1/id-salt.bin
```

The command writes deterministic calibration and held-out packets plus
protocol-bound manifests. Split policy `source_trace_v1` keeps each normalized
source/trace group in one phase across both taxonomy rubrics and all rounds.
Selection balances candidate strata within each phase; it never breaks a group
to fill a class quota. Manifests retain aligned hashed group identities for audit.
Trace separation does not establish independent tasks: related task families,
projects, time periods and previous annotation exposure still require study-specific
controls. Class support counts are case counts, not independent-session counts.
Existing packets remain readable, but adaptive continuation requires the same
split policy; start a separate review set for legacy per-case partitions. Each row starts with a reason-coded
rule-based scorer proposal. Optional review asks only Correct, Incorrect, or Unsure; an
Incorrect assessment selects a different canonical label and requires no prose.
Failure packets use versioned redacted-evidence hints only to balance selection.
Later adaptive rounds must name every earlier round's calibration and held-out
manifest for both rubrics and reuse the same trace snapshot, private evidence,
candidate taxonomy, rubric and annotation protocol. The total selected cases
across all rounds cannot exceed each rubric's `maximum_total`. Invalid requests
are rejected before packet creation; oversized selections are not silently
trimmed. Sizes must be nonnegative integers, with zero explicitly disabling a
split. Each later round gets its own instruction file; the initial discovery file
and prior packets remain unchanged. Completed packets are never regenerated or
implicitly accepted.
The dashboard sums case and assessment counts across every round within each
rubric version and phase, and displays each round/protocol binding. Combined
row counts do not establish comparable protocols or independent observations.
Human-label counts and optional assessment counts remain separate. Recommendation
text uses the same captured counts as the tables; labels alone never establish
scoped validation. Controlled instruction coverage passes only with explicit
nonnegative integer counts, a nonempty population, and resolved plus unresolved
equal to that population, with none unresolved. Missing, empty or inconsistent
coverage stays unverified. Retired hook observations remain historical evidence
and do not appear as current lifecycle passes.
The assessment command uses the validated packet snapshot and rejects unsupported
labels or invalid review rounds. By default it reports support for that packet
only. Repeat `--prior-packet <earlier.csv> <earlier-manifest.json>` to assess
accumulated labels; include every earlier round of the same rubric and split.
Cumulative assessment requires matching input, candidate taxonomy, protocol and
split-policy bindings, unique candidate identities, and the case budget. It never
scans a directory implicitly or modifies annotations. Output identifies the scope,
rounds, labelled and unlabelled rows. `ready` means class support is adequate;
`decision_support` remains false. Empty or partial review cannot promote a taxonomy.

Use `retro-eval-taxonomies assess --source <packet.csv> --manifest
<packet-manifest.json> --criteria`, with the same explicit prior-packet options,
to compare frozen proposed labels with the captured human labels. Criteria mode
requires versioned input, candidate, protocol and split bindings even for a single
packet. It rejects unsupported proposals, changed fingerprints and missing prior
rounds. It reports numerical checks, missing labels and packet bindings. Null
precision, recall or agreement means that its denominator is absent, not zero
observed accuracy. Exit 0 means every selected case is labelled and all numerical
criteria pass; exit 1 flags unmet criteria or incomplete labels; exit 2 means the
assessment could not run. Neither success nor sufficient class counts authorizes
promotion. The historical `assess_taxonomy_promotion` API now reports assessment
version 2 and `thresholds_met`, while keeping `decision_support: false`.

Before proposing promotion, review held-out exposure and predictor-freeze history,
target population, sampling design and evidence for the intended use. Record the
scoped validation decision through the existing comparison workflow where it fits.
File separation and passing point thresholds cannot supply this evidence.
Research on [adaptive holdout reuse](https://research.google/blog/the-reusable-holdout-preserving-validity-in-adaptive-data-analysis/)
explains why feedback-driven selection can invalidate ordinary holdout assumptions;
this plugin does not implement that paper's reusable-holdout mechanism or claim its
guarantees. Any failed criterion keeps the taxonomy unvalidated and barred from
decision support.

Start or resume that process through the `reviewing-evaluation-taxonomies`
skill. Its single `retro-eval-review` entrypoint discovers the current external
review set, resumes the first open assessment, and keeps calibration separate
from held-out review:

```text
<python> <plugin-root>/bin/retro-eval-review status
<python> <plugin-root>/bin/retro-eval-review serve-next
```

Calibration is the default phase. After its disagreements have been analyzed
and any affected scorer, rubric, protocol, dataset, or report version has been
re-derived, start the untouched held-out phase explicitly with
`serve-next --phase heldout`. The entrypoint validates every packet fingerprint
before serving and never regenerates a packet.

Explicit skill lifecycle spans are normalized only when the source emits an
authoritative attribution boundary. Starts, ends, chain steps, and explicit
outcomes may be counted. Opportunity and missed-trigger rates remain
`not_observable` until a separately calibrated classifier exists; an absent end
or outcome is never invented from transcript termination.

Generic dashboard metrics include every source supplied by the report. Unknown
counts and rates remain distinct from zero; plots identify each source and show
only available rates and complete intervals. Source-specific historical proposal
policies retain their declared scope.

Dashboard disagreement counts route review of the marked cases and their notes;
they do not identify a cause or establish a workflow fix. These are pending review
actions, not evidence that a rule changed.

The dashboard retains historical owned-hook measurements as retired evidence.
The response-format hooks and their telemetry tools were removed in v1.12.0;
those earlier measurements no longer support adopting instrumentation.

Cross-source usage comparisons must first pass
`retro_eval.usage_comparability.validate_usage_comparison`. The versioned gate
requires exactly one row per source and paired case, matching task family,
difficulty, cache treatment, accounting profile, and source accounting version.
Counts must be observed nonnegative integers; missing values, booleans, fractions
and numeric strings are rejected. Observed zero remains valid. Profile versions
are literal integers, and pairing fields are nonempty text.

Profile version 3 requires callers to name compatible dimensions, for example
`dimensions=("cached_input_tokens", "output_tokens")`. The returned
`comparison_dimensions` is the entire scope of approval. The default request
for all dimensions fails: Claude raw input excludes cache reads while Codex input
includes them. This gate does not normalize those input counts. Codex cached input
must not exceed its input total; Claude's separate cache-read count has no such
relationship. Historical profiles without explicit dimension authority remain
readable but cannot authorize comparisons. Return schema version 2 makes this
scoped approval explicit.

Count provenance, capture completeness and task equivalence need separate
evidence. Provider token counts are not common billing or equal-work units.
The gate does not run or authorize a paired experiment.

Claude adapter version 11 records failure taxonomy version 4: cause patterns use
tool results, and explicit pre-execution hook refusals take precedence over probe
intent. Reports expose recorded diagnosis versions; scoring older extracts does
not reclassify them. Re-extract source evidence to apply the current classifier.
These diagnoses remain provisional and do not establish intervention benefits.

A newly accepted direct-human request clears earlier source-completion evidence
in all three adapters. Codex also clears completion or abortion on `task_started`
and removes the previous terminal duration. Subsequent source completion may close
the new request. This is current source state, not verified delivery or quality;
older extracts require re-extraction to receive the correction.

Claude and Antigravity keep existing IDs for ordinary block indices and encode
indices of 1,000 or more with separate identity components. Oversized batches no
longer alias the next record. Tool and result sequences preserve encounter order
without a whole-transcript pre-scan. Private evidence uses the same identity rule;
appending a later batch does not change earlier IDs.

Claude adapter version 11 and legacy ledger schema 12 count the final captured
usage snapshot once per nonempty message ID, using the same grouping as the
workflow importer. Distinct response IDs with equal amounts remain separate;
message and tool content is not deduplicated. Anonymous messages retain separate
contributions because their identity is unknown. Accounting profile 2 requires
`claude-v2` for new comparisons; old stored measurements are not rewritten.

This is accounting within one captured transcript, not unique physical requests
across copied or resumed files. The [Claude usage documentation](https://code.claude.com/docs/en/agent-sdk/cost-tracking)
distinguishes repeated message IDs from independent usage and warns that SDK
stream output counts can be placeholders. A captured native transcript's settled
output counts were checked against separate result usage; that does not validate
all source versions, a truncated capture, or arbitrary SDK stream exports.
The workflow importer preserves missing/invalid final usage as unobserved; legacy
integer counters cannot express that missingness and must not establish complete
cost or billing. New normalized Claude traces expose per-field completeness;
input-per-source-completion scorer version 3 excludes explicitly incomplete input
usage and includes an explicitly observed zero. Historical zeros without coverage
remain ineligible; positive historical traces retain their original
scope limits. Source completion remains distinct from verified delivery.
Input-per-source-completion scorer version 4 reports the observed mean without
population intervals. Its metric definition is version 3. Source trace identity
and a configured bootstrap backend do not establish random sampling or independent
observations. Historical `interval_backend`, `confidence` and `seed` constructor
options remain accepted for profile compatibility but are not imported or executed;
the default profile omits them. The observed mean no longer requires SciPy.
Minimum-count and usage-coverage gates remain in force. Reports expose the same
missing-design reason and comparison-protocol prerequisite as operational rates.

The general paired-effect utility still supports explicit statistical analyses.
It keeps the observed effect when backend limits are nonfinite, with a null
interval and reason; malformed or reversed bounds remain errors. This numerical
handling does not establish a sampling design. The
[SciPy bootstrap documentation](https://docs.scipy.org/doc/scipy-1.16.1/reference/generated/scipy.stats.bootstrap.html)
describes resampling observations and conditions its coverage explanation on
random samples. Choosing an estimator does not supply those conditions.
Historical reports retain their original versions and interpretation limits.

Codex adapter version 10 retains the last captured cumulative counter. It exposes
per-field availability so invalid counts cannot abort import or enter usage
comparisons, and observed zeros remain distinct from missing fields. This scope
does not establish whole-session cost: upstream
[counter recovery](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs)
can replace cumulative dimensions with context-window state. The legacy ledger's
reset-banking heuristic is a different measurement and is not used to backfill
normalized traces. Neither approach proves billing or quota consumption.
In a retained capture, per-response usage records reconciled with their own thread
totals but differed from the final context counter. Upstream
[response accounting](https://github.com/openai/codex/blob/main/codex-rs/core/src/session/mod.rs)
uses a separate reporting path. Monotonic context counters therefore do not by
themselves establish complete spending; these response records do not yet have a
validated common accounting mapping in this plugin.

## Completion and coverage

Antigravity step exports can contain token and cache-token fields. The adapter's
unavailable usage capability means a complete accounting mapping has not been
validated, not that those fields never exist. A later export can include work
absent from an earlier terminal result. The CLI's separate
[headless stream](https://antigravity.google/docs/cli/headless/) distinguishes
per-step usage from cumulative session results; neither surface automatically
establishes billing, equal accounting windows or cross-harness comparability.

`source_completion_rate` and `input_tokens_per_source_completion` describe
source status and its observable input usage. A completed reply can leave the
requested task unfinished. Neither metric establishes successful delivery.
Source-completion cost metric definitions from version 2 include explicitly observed
zero input usage. Zero without coverage and explicitly incomplete usage remain
ineligible; historical positive usage without a coverage field remains supported.
The metric-definition version and executable scorer version are separate.

Operational rate scorers describe the observed records. Repeated-call scorer 7
(metric definition 6), tool-failure scorer/metric 5, skill-invocation scorer/metric 3,
and source-completion scorer/metric 2 withhold sampling intervals: a record count
does not establish an independent sampling design. Reports retain counts, ratios,
minimum-count gates and a structured `sampling_design_not_established` reason;
the dashboard displays “No sampling design.” Before estimating population
uncertainty, specify the target population, sampling or assignment unit,
dependence assumptions and estimator in a comparison protocol, then evaluate
those assumptions. Trace identifiers alone do not establish independent sampling.
Historical intervals are not retroactively corrected.

The [NIST binomial interval reference](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)
describes Wilson intervals for binomial proportions; it does not establish that
session records follow that model. Research on
[nested evaluation observations](https://arxiv.org/html/2606.26422v1)
shows why dependence changes uncertainty. That does not establish a universally
appropriate replacement estimator. Label reports apply the same descriptive default; neither report establishes
population inference or validates taxonomy promotion gates.

Report and normalized-trace annotation/taxonomy fingerprints name the exact bytes
parsed, even if a later extraction atomically replaces the input path. This does
not make extraction metadata, external evidence and trace bytes a cross-file
transaction, or authenticate the producer of any evidence.

Verified-outcome and cost-per-verified-outcome scorer version 2 require
contract-bound verification. Current adapters do not capture that evidence,
so these scorers abstain even on historical snapshots that advertised an
`outcomes` capability. No arbitrary normalized attribute or task-complete
message supplies the missing verification. Historical version 1 scores used a
source-completion proxy and must not be compared as verified delivery.

Report schema version 3 preserves observed numerators even when a sample-size
or uncertainty-backend gate withholds an estimate. Unavailable observations use
`null`; an observed zero remains `0`. This output change does not change scorer
classification rules, calibration or eligibility gates. Older reports may contain
placeholder zeros in abstentions; do not interpret them as observed counts.
Results and coverage expose `configured_minimum_n`, or null when unavailable.
Coverage's existing `minimum_n` remains the catalogue recommendation; injectable
scorers may use a different configured threshold. Reporting that threshold does
not establish calibration or authorize decision use below catalogue requirements.
The report also retains schema version 2's requested sources with zero included traces,
separates included main and child populations, and carries observed snapshot
timestamps and exclusion counts. These bounds do not prove tasks are closed.
Re-extract into a new external work directory to capture changed capability
declarations; preserve old snapshots and their provenance unchanged. Missing
attribution is not evidence of a missed trigger or an unused skill.

Repeated-call candidate taxonomy version 6 recognizes native
`replace_file_content` and `write_to_file` calls as intervening mutation attempts.
An intervening mutation does not prove the write succeeded or the repeated work
was necessary; source review remains required. Current scorers also exclude calls
without observable signatures from repeat eligibility. Such calls cannot establish
identical inputs; their tool kinds still supply intervening-mutation context for
known repeats. Reports retain total population and the missing-signature count.
This observation-integrity correction does not promote the unvalidated taxonomy
or demonstrate improved held-out precision. Taxonomy packets record their candidate
taxonomy version from the same loaded configuration used to generate candidates.
Adaptive rounds reject a changed or unavailable prior candidate version; preserve
older packets and start a separate review set instead of silently mixing methods.
Ordinary annotation, taxonomy and interpretation packets share exclusive CSV
creation and fingerprints computed from their selected serialized rows.
Interpretation regeneration also refuses existing assessment files.
Generation rejects existing CSV or manifest destinations before writing packets;
exclusive creation also prevents replacing a destination created after that check.
Use a fresh round or directory. This is not an atomic transaction across the whole
packet set; inspect partial output after an interrupted or failed generation.

## Privacy boundary

Set `RETRO_HOME` to a directory outside every Git repository. Normalized traces
contain keyed local IDs, counts, timestamps, tool kinds, and call signatures;
they do not contain message text, tool input/output, source paths, or session
IDs. Annotation CSV files contain redacted excerpts and therefore also stay
outside Git. The work-root, label-store, annotation, report, benchmark, and
Parquet-cache writers reject repository paths.

Tool review excerpts retain the beginning and end within the existing size bounds
and mark omitted text with `[... excerpt truncated ...]`. Recent assistant context
keeps its tail with the same notice. Redaction runs on complete text before any
excerpting. Nested packet display bounds preserve the notice and terminal evidence.
An excerpt can still omit decisive middle content; choose Unsure when the shown
evidence cannot support a judgment. Existing saved packets are not rewritten.
Claude adapter version 11, Codex adapter version 10 and taxonomy packet version 7
carry this change.

Antigravity adapter version 5 supplies private tool arguments and bounded assistant
context using the same latest-step selection and call IDs as normalization. Context
identifies whether it came from the same step or a previous assistant step; previous
text does not establish the call's purpose and resets at user input. Generic output
and error steps remain unlinked: the adapter does not invent per-call results or
statuses. This evidence stays outside normalized storage and outside Git.

No report-generation command applies a proposal; reports set `auto_apply` to false.
An agent may implement and verify changes within the requester's existing authority,
including explicitly authorized local skill refinement. A report grants no new
authority. Changes outside that scope, such as global configuration or adoption into
another workflow, require the appropriate authorization; do not re-ask for authority
already supplied for the current work.

Rubric use is also policy, not prose. `rubrics.json` declares allowed uses for
every rubric. Provisional rubrics may sample candidates, support human
annotation, and validate their own scorers; they may not provide decision
support. Proposal candidates that cite label evidence must declare its rubric
provenance. The review generator suppresses an unrelated proposal when that
rubric has not earned `decision_support`, while scorer-validation proposals
remain visible. Resolved proposal statuses remain recorded but leave the active
ranking.

External annotation extracts may overlap. Identical cases contribute once to
sampling; differing content or context for the same case identity is an input
error before packet publication. Annotatable turns require session and line
identifiers; ambiguous identities are rejected rather than merged. Every supplied
input remains in provenance.

## Follow implemented proposals into use

The `reviewing-improvement-effects` skill registers or recovers one reversible
pilot using the existing proposal identity and named evidence index. Its private
record binds activation, task family, baseline and follow-up, owner, review
trigger, quality constraints, rollback, disposition, and next action. The six
observation signals keep response completion, verified delivery, later use,
reopened work, parent rework, and user interventions distinct. Missing evidence
remains null, including when the implementation is complete.

Use the skill's `references/record-template.json` and `references/record-format.md`.
Every record and referenced artifact remains outside repositories. The checker
reuses the proposal evidence resolver, verifies artifact fingerprints and the
selected proposal identity, checks declared comparison and activation metadata,
and applies rubric decision-use gates. The captured evidence index and resolved
artifact paths used for the external-location check also drive resolution;
instruction manifests are validated from their fingerprinted claims. These checks
do not provide atomicity against arbitrary concurrent filesystem changes:

```text
<python> <plugin-root>/skills/reviewing-improvement-effects/scripts/check_record.py \
  --record <RETRO_HOME>/proposals/<pilot>/effect-record.json \
  --evidence-index <RETRO_HOME>/proposals/<pilot>/evidence-index.json
```

Exit 0 means checked bindings and declared prerequisites; exit 1 preserves
evidence gaps or rubric restrictions; exit 2 means invalid input or links. The
checker does not validate the truth of an authored observation, infer causality,
or apply a disposition. The skill reviews the actual evidence and records keep,
revise, revert, or insufficient evidence with a concrete next action. A directly
observed quality regression can justify a narrow rollback even while broader
effects remain unknown.

The older `retro.py effect` report is a Claude main-session before/after lens.
Different task or source populations and absent activation provenance prevent
an effect claim, even when token use or error counts fall. A completed local
pilot registration with no future observations has an insufficient-evidence
review; producing it does not prove adoption or benefit. The skill's cadence
guide connects later reviews to the existing friction, rule, stopped-promises,
agent-contract, adequacy-review, and tool-scouting diagnostics.

## Reproducible commands

Resolve a Python 3 interpreter and the plugin root first. The examples use
`<python>`, `<plugin-root>`, and external roots supplied by the operator.

```text
<python> <plugin-root>/bin/retro-eval-extract \
  --work-dir <RETRO_HOME>/cross-harness-v1 \
  --root claude=<claude-session-root> \
  --root codex=<codex-session-root> \
  --root antigravity=<antigravity-brain-root> \
  --instruction-manifest <RETRO_HOME>/instructions/<version>.json \
  --exclude-session-id <active-session-id>

<python> <plugin-root>/bin/retro-eval-report \
  --work-dir <RETRO_HOME>/cross-harness-v1 \
  --output <RETRO_HOME>/cross-harness-v1/deterministic-report.json \
  --created-commit <full-commit-id> \
  --dataset-id <dataset-id>
```

`--exclude-session-id` applies to every adapter: the Claude transcript or
subagent directory name, the Codex `session_meta` id, or the Antigravity
session directory. `--created-commit` takes the full id that
`git rev-parse HEAD` prints; abbreviated ids are refused.

Evaluation reports require a single adapter version per source population.
Combining differently normalized versions of the same source is rejected before
scoring; re-extract consistently or produce separate snapshot reports. Different
sources may use different adapter versions. This prevents row order from
silently selecting the version recorded in the report and dataset manifests.

The pinned optional analytics environment requires Python 3.12 or newer because
of its [NumPy requirement](https://pypi.org/project/numpy/2.5.2/); its CI job uses
Python 3.14. The stdlib-only core continues to support Python 3.12.
Install optional analytics into an isolated environment from
`requirements-eval.txt`; do not add them to the plugin's stdlib runtime.
The Parquet cache captures one temporary input for conversion and metadata,
then removes that capture. Its final cache file and adjacent manifest are not a
multi-file transaction; serialize refreshes to the same cache target and regenerate
a cache after an interrupted publication. Then benchmark the same trace file
through `jsonl`, `duckdb-json`, and
`duckdb-parquet`. Each result carries the warm-up `answers` of every workload;
answer agreement is necessary but does not establish correctness when backends
share a mistake. Tests also check independent expected window membership.
Workload version 2 defines `30_day_filter` as the inclusive interval from the
latest known timestamp minus exactly 30 days through that latest timestamp;
null timestamps do not belong to the interval. Earlier unversioned results
used a different window and must not be compared as the same workload.
All backends accept calendar dates with `T` or a space, hours, minutes, seconds,
optional one-to-six fractional-second digits, and `Z` or a signed `HH:MM`
offset. Non-null timestamps outside this common grammar are rejected before
measurement, including compact dates, week dates, fractional offsets and
arbitrary date/time separators. Comparisons use exact integer microseconds,
including local dates whose UTC instant crosses year 1 or 9999.
Run the optional-dependency tests with
`RETRO_EVAL_REQUIRE_OPTIONAL=1` so a missing package fails instead of skipping. The benchmark itself:

```text
<python-with-eval-deps> <plugin-root>/bin/retro-eval-benchmark \
  --input <RETRO_HOME>/cross-harness-v1/traces.jsonl \
  --backend duckdb-parquet \
  --work-dir <RETRO_HOME>/bench-parquet \
  --output <RETRO_HOME>/bench-parquet.json \
  --runs 5
```

Ordinary label reports and strict comparisons require complete matching case sets
within the requested splits, with matching per-case dataset, rubric, split and
sampling weight. A partial prediction file is an error, not a favorable overlap
score. Sampling weights and their accumulated totals must be finite and positive
where a nonempty population is scored. Calibration-only selection remains supported.
Label analyses version 2 retain observed confusion counts, agreement, kappa and
weighted ratios, but set agreement/precision/recall intervals to null. Each report
section carries `analysis_version: 2` and a structured missing-sampling-design
reason. Strict comparison output uses schema version 2. Case identity, equal
sampling weights and separate splits do not establish independent random draws;
weights alone do not identify a complete survey or experimental design. Establish
and evaluate a comparison protocol before population inference. The general Wilson
utility remains available for analyses that justify its assumptions. Earlier
reports are not rewritten. This policy does not validate taxonomy promotion gates
or replace missing human labels with predictions.

Import existing human marks as calibration-only, draw a fresh cross-harness
test set, and import it after the human fills `human_label`. `sample` draws
direct-human prompts from the extracted snapshot; like taxonomy packets it
rereads the same source roots with the same ID salt, and only the packet
receives the redacted turns. Every source in the snapshot must contribute at
least one case, or nothing is written:

```text
<python> <plugin-root>/bin/retro-eval-labels import-legacy \
  --source <existing-labels.jsonl> \
  --labels <RETRO_HOME>/labels/human-calibration.jsonl \
  --predictions <RETRO_HOME>/labels/rule-calibration.jsonl

<python> <plugin-root>/bin/retro-eval-labels sample \
  --traces <RETRO_HOME>/cross-harness-v1/traces.jsonl \
  --id-salt <RETRO_HOME>/cross-harness-v1/id-salt.bin \
  --source-root claude=<claude-session-root> \
  --source-root codex=<codex-session-root> \
  --source-root antigravity=<antigravity-brain-root> \
  --output <RETRO_HOME>/labels/heldout.csv \
  --manifest <RETRO_HOME>/labels/heldout-manifest.json \
  --per-source 20

<python> <plugin-root>/bin/retro-eval-labels predict-annotations \
  --source <RETRO_HOME>/labels/heldout.csv \
  --manifest <RETRO_HOME>/labels/heldout-manifest.json \
  --predictions <RETRO_HOME>/labels/rule-test.jsonl \
  --prediction-manifest <RETRO_HOME>/labels/rule-test-manifest.json \
  --created-commit <full-commit-id>

<python> <plugin-root>/bin/retro-eval-labels import-annotations \
  --source <RETRO_HOME>/labels/heldout.csv \
  --manifest <RETRO_HOME>/labels/heldout-manifest.json \
  --labels <RETRO_HOME>/labels/human-test.jsonl

<python> <plugin-root>/bin/retro-eval-labels compare \
  --labels <RETRO_HOME>/labels/human-test.jsonl \
  --sample-manifest <RETRO_HOME>/labels/heldout-manifest.json \
  --predictions rule=<RETRO_HOME>/labels/rule-test.jsonl \
  --prediction-manifest rule=<RETRO_HOME>/labels/rule-test-manifest.json \
  --predictions judge=<RETRO_HOME>/labels/judge-test.jsonl \
  --prediction-manifest judge=<RETRO_HOME>/labels/judge-test-manifest.json \
  --output <RETRO_HOME>/labels/heldout-comparison.json \
  --split test
```

Sampling requires fresh, distinct CSV and manifest destinations and never
replaces an existing annotation packet. The sampling salt is reserved and cannot
be used as an output path. A competing output or changed salt makes sampling
fail; inspect partial files after an interrupted publication before retrying.
Manifest fingerprints describe the selected rows, so replacing a published CSV
cannot make the replacement inherit the original input provenance.

`sample --extract <file>` remains for externally produced extracts (one JSON
object with `source_system` and `sessions[].messages[]`); nothing in this
plugin writes that format. External-extract manifests record every captured input
in `input_artifacts` as `{source, sha256}`; repeated sources retain all contributors.
The compatibility `input_fingerprints` map contains only sources supplied by one
artifact. Complete provenance uses the list, whose digests bind the bytes parsed.
It obeys the same refusal rule: a run with no
annotatable case exits 2 and writes nothing. The `judge` pair is optional: no
shipped command produces model-judge predictions, which require separate
authority.

Annotation import and prediction verify and consume one captured set of CSV rows.
Imported availability counts logical records, including quoted multiline fields.
Freeze both prediction sets before importing truth. Deterministic callbacks receive
a separate mapping without human labels, notes or assessments; immutable evidence
and extension columns remain available. This prevents annotation leakage through
the callback argument, but cannot certify that arbitrary predictor code has no
external knowledge of truth. The comparison command then
requires exact case coverage and verifies every prediction fingerprint against the
same captured bytes used to score per-class precision/recall, agreement,
and kappa. Later path replacement cannot substitute unchecked predictions.

Review packets through the local annotation UI instead of editing CSV by hand.
The server binds only to loopback, validates the packet's rubric and
annotation-protocol hash before serving, rejects cross-origin writes, and saves
each assessment atomically back to the external CSV. A snapshot revision hashes
the exact bytes displayed, so a later external edit makes that view stale. This
serializes cooperating local annotation saves across processes, then rejects stale
revisions. Contention for the file lock and workspace mutex shares a five-second
wait budget; a busy save returns HTTP 503 and can be retried. A persistent empty
sidecar uses a bounded, case/Unicode-normalized filename hash. Do not delete it
while writers are running. External editors, hard-link aliases, arbitrary network
filesystems and hostile directory replacement remain outside this guarantee.
Windows locking follows the platform API but has not been runtime-verified.
Dashboard summaries and review progress consume the same captured rows and
manifest that passed validation. The UI rechecks immutable evidence whenever
it reads a packet and returns a conflict if it changed; a save compares the
submitted revision against the exact captured rows it will edit.
The single review
entrypoint uses the stable URI `http://127.0.0.1:8123/`; random ports are not
part of the default workflow. Taxonomy packets show the
frozen rule-based proposal and offer Correct, Incorrect, and Unsure. Choosing
Incorrect reveals the current protocol's canonical alternatives. Repeated-call
protocol v5 presents separate Earlier call/intervening and Current repeated
call evidence panels with redacted purpose, input, and result evidence. The
review question is never stored or displayed as captured evidence; a matching
signature or sequence number alone is explicitly insufficient. An active job is
also insufficient to excuse repeated polling: bounded polling requires evidence
of reasonable cadence and unavailable completion notification, prolonged
polling is avoidable overhead, and adjacent waits without cumulative context
remain ambiguous. Arrow keys move between cases; reopening resumes at the first
unassessed case.

Mixed interpretation calibration runs before taxonomy review when such a packet
is present. Each authored card links to immutable external source evidence but
shows a bounded plain-language situation, interpretation, reason, and expected
action. Cards may check understanding of a direct-human reply or an assessment
of agent behavior. The operator chooses Accurate, Partly accurate, Wrong, or
Not enough context; notes and raw-evidence expansion are optional. This rubric
is calibration-only and cannot support decisions. Build the packet from an
authored card file into the review directory:

```text
<python> <plugin-root>/bin/retro-eval-interpretations build \
  --cards <RETRO_HOME>/annotations/interpretation-cards.json \
  --output-dir <RETRO_HOME>/annotations/proposal-taxonomies \
  --round 1
```

The same UI also supports older label-first protocols. Questions, choices,
definitions, and overlap rules always come from the packet's bound versioned
annotation protocol; they are not hard-coded in the browser client.

```text
<python> <plugin-root>/bin/retro-eval-labels serve \
  --source <RETRO_HOME>/labels/heldout.csv \
  --manifest <RETRO_HOME>/labels/heldout-manifest.json
```

Use `--no-open` when another process will open the printed loopback URL. A
non-loopback `--host` is rejected; this interface is deliberately not a shared
or remotely hosted annotation service. Label packets are served only by
`retro-eval-labels serve`; `retro-eval-review` serves taxonomy and
interpretation packets and names any label packet it finds instead of
reading it.

Generate proposal reviews in strict mode so every trace reference resolves in
the selected normalized snapshot and every named aggregate resolves through a
fingerprinted external evidence index. Artifact hashes and selected claims use the
same captured bytes. Count claims require exact integers; booleans do not stand in
for numeric measurements. Invalid counts, rates, costs or nonfinite JSON values
fail before either report is written:

```text
<python> <plugin-root>/bin/retro-eval-proposals \
  --candidates <RETRO_HOME>/proposals/candidates.json \
  --trace-evidence <RETRO_HOME>/cross-harness-v1/traces.jsonl \
  --evidence-index <RETRO_HOME>/evidence/evidence-index.json \
  --json-output <RETRO_HOME>/proposals/review.json \
  --markdown-output <RETRO_HOME>/proposals/ranked-proposals.md
```

## Interpretation rules

1. `unavailable`, `not_observable`, `not_applicable`, zero, and missing are
   different states. Never coerce one into another.
2. A rubric marked `provisional` or `unvalidated` cannot support an automated
   change. Calibration-only results cannot be called held-out.
3. Compare rates only over their declared eligible populations and source
   capability floors. Cross-harness token totals are not comparable until task
   difficulty and provider accounting are matched.
4. Identical tool signatures receive a reason-coded candidate diagnosis, not a
   decision claim. Polling, monitoring, and post-mutation verification remain
   separate classes; no class supports decisions until the held-out promotion
   gate passes.
5. Every surviving proposal reports population, independent evidence,
   uncertainty, expected impact, exact change, experiment, threshold, and
   rollback. Suppressed candidates remain visible with their reason. In strict
   mode, every non-null observed rate binds its population and value to exact
   JSON selected from a fingerprinted artifact. Session evidence counts come
   from the declared session population; other evidence units never inherit it.
6. `not_scored` means the required source evidence exists but no configured
   scorer implements the metric. It is a visible implementation or labelling
   gap, never a zero. Model-judge abstention is configured per rubric and is
   reported separately from an invalid provider label.

### External judge authority

Redaction does not make evidence public. The general `ModelJudge` and workflow
`judge_input` boundary permit unapproved external input only when its class is
explicitly `public_synthetic`. Private, redacted, metadata, missing and unknown
classes require explicit external-data authority. The general judge also treats
`public` as insufficient to establish synthetic-public classification; do not
relabel real data merely to bypass the gate. Its provider must declare boolean
`True` to count as local. Authority flags must be booleans, not strings or numbers.

The workflow resolver retains its additional restriction against raw artifacts,
even with external authority, and uses stored classifications rather than a
reference's claimed downgrade. The standalone judge relies on the caller's class
and provider declarations; it does not authenticate their truth. No shipped
command calls a live judge provider. Redacted excerpts can retain local paths,
names and contextual identifiers and remain private review material.

### Automatic notifications and human provenance

Codex adapter version 10 uses the same machine-message rule as the legacy ledger
and evidence extraction. A complete `hook_prompt` envelope with a nonempty,
quoted `hook_run_id` is a recognized notification. Leading or trailing whitespace
is allowed. Missing, duplicate or unquoted attributes, incomplete envelopes,
quoted examples, unknown tag names and human prose outside the envelope are not
silently discarded. Existing known-wrapper exclusions still apply.
A nonempty content array whose complete positional `content_item_kinds` list
contains only `agents_md.instructions` and `environments.environment_context`
is also recognized as context. Mixed, unknown, missing or malformed kinds use
the existing wrapper fallback. AGENTS text by itself is not a new exclusion.
Kinds describe content categories, not authenticated human authorship; future
per-part attribution is not inferred from these two categories.
Codex's default opener list also preserves image attachments followed by written
feedback and image-markup quotations. An image prefix is not machine provenance.
Explicit custom opener policies remain effective. The older Claude fallback keeps
its existing policy; this change does not add image-only semantic interpretation.

This is a transport-content heuristic, not proof of authorship. An exact envelope
typed by a person cannot be distinguished from transport text in a Codex record
that has only a user role. Claude and Antigravity retain their explicit human
source rules; those markers preserve human messages containing the same markup.

Recognized machine messages do not reset the assistant context used to classify
the next human reply. They do not enter human annotation packets. Included Codex
traces retain their content-free sequence and origin method in root attributes;
workflow mapping version 3 also retains these observations for notification-only
captures. Raw snapshots remain available locally. An unperformed origin scan is
reported as missing, not zero machine events.

Legacy ledger schema 11 changed the human-count definition; schema 12 also
corrects Claude usage aggregation. Normal extraction rebuilds earlier ledgers
automatically. Immutable workflow captures and studies
keep their old interpretation; rerun normalization under the new adapter for new
results rather than editing historical evidence. Filtering known notifications
does not validate the intent-based correction detector or resolve unknown origins.


Source adapters leave call signatures unavailable when tool identity or captured
input is absent or null. Repeat analysis then abstains on those calls while still
retaining their mutation context. Explicit empty or false-valued input remains
observed input; Codex canonicalizes equivalent JSON encodings consistently.
Private annotation evidence omits missing inputs instead of showing invented
empty text or a literal null. Re-extract older captures to apply these producer
changes; existing normalized records keep their recorded adapter versions.


Codex adapter version 11 and legacy ledger schema 13 separate complete observed
question-reply envelopes into answer text and question context. Transport metadata
and question wording no longer inflate the user's answer or determine its turn
category. Multiple replies retain their order as one aggregate prompt; this does
not infer independent intent labels for each answer. A recognized answer remains
a prompt even when its text matches the provisional interruption classifier.
Questions contribute observed context, including when the interface delivered the
question outside an assistant prose message. Full context is redacted before legacy
moment excerpting. Existing evidence bounds still apply.

Recognition requires a complete array with nonempty string `answer`, `question`
and `questionItemId` fields and no duplicate or unknown fields. Malformed, quoted
and mixed-content shapes remain unchanged. Exact envelope-shaped text is a structural
heuristic, not proof of transport origin or human authorship. Raw captures remain
authoritative. Existing frozen reports retain their recorded definitions; a new
measurement must declare these identities. No change makes heuristic correction
candidates into verified user intent or supplies absent audience acceptance.

## Dual-axis behavioral skill evaluation

`retro_eval/skill_behavior.py` and `bin/retro-eval-skill-behavior` evaluate
skill-execution scenarios under the provisional `skill_behavioral_dual_axis`
rubric across two opposing axes simultaneously:

- **Axis A (Safety / Root-Cause Rigor):** reproduction before edit, absence of
  band-aid workarounds, preserved validation boundaries, concrete passing
  verification commands, and fresh blinded review on high-risk changes.
- **Axis B (Proportionality / Anti-Waste):** no recycled reviewer threads, zero
  in-repo scratch sprawl, bounded docs-only PR ratios, risk-tiered ceremony and
  compaction limits, plain-language updates, and bounded skill byte budgets.

Public synthetic fixtures remain inside the repository (`run --synthetic`);
private replay fixtures must reside strictly outside every Git repository
(`run --external-cases`, refusing in-repo paths and symlinks resolving inside a
repository) and are forcibly tagged `private_local` under `ModelJudge`'s
external-provider consent boundary. Paired baseline-vs-candidate comparisons
(`compare`) detect single-axis or dual-axis regressions without conflating
offline replay passes with live post-ship pilot evidence.
