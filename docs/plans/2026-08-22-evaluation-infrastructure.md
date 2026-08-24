# Agent evaluation infrastructure v1 — implementation plan

**Status:** Continuing; approved next-stage proposal controls are in implementation
**Recommendation:** extend `retro` behind a normalized trace and scorer boundary,
keep the proven commands compatible, and earn every new dependency against the
real corpus before adopting it. Do not build a second transcript miner or one
opaque “quality score.”
**ASK:** none during execution. Stop at the four numbered review artifacts.

| Decision | Status | Evidence |
|---|---|---|
| Reuse `plugins/p` | decided | It already owns transcript discovery, privacy, skill firing, subagent, effect, and cost lenses |
| Add Claude and Codex adapters | decided | Their direct-human and subagent provenance rules are materially different |
| Normalize to trace/span records | decided | It preserves source differences while giving scorers one stable input |
| Use external analytical dependencies | benchmark-gated | Portability and cold-start cost must buy measured runtime, memory, or statistical correctness |
| Use model judges | interface in v1; live use requires separate authority | Raw private session text may not be sent to a provider under this Goal |
| Produce proposals, not automatic edits | decided | Human judgment remains the final control plane |

### Outcome and stopping contract

#### Outcome

**Measured evaluation system**

V1 ingests local Claude Code and Codex history into a privacy-safe, versioned
trace model; evaluates it with versioned deterministic, human-label, and
model-judge-compatible scorers; builds reproducible datasets; and emits ranked,
evidence-backed improvement proposals across skills, prompts, rules, hooks,
agents, orchestration, context, repetition, cost, clarity, security, and
outcomes.

#### Done when

**Evidence, not feature count**

The full test suite passes; source selection and adapter capability checks pass;
held-out precision, recall, and agreement are reported; estimates carry sample
sizes and uncertainty; storage/statistics choices have corpus benchmarks; the
real-corpus baseline runs without private output; manifests and documentation
agree; the privacy audit is classified clean under repository policy; changes
are committed on and integrated into the intended base; and the tracked base is
clean.

#### Protected scope

**No harvested data in Git**

Raw transcripts, absolute machine paths, identities, session IDs, unredacted
excerpts, labels derived from private text, and generated datasets stay under
`RETRO_HOME`. No other project is named in tracked files. Existing commands and
their exit-code meanings remain compatible until parity tests prove a deliberate
replacement. Recommendations are never applied automatically.

#### Review gate

**Four current artifacts**

Before writing a new review set, archive the prior set under
`RETRO_HOME/review/_archive/YYYY-MM-DD/`. Stop at `Review needed` with only:

1. `01-architecture.md`
2. `02-baseline.md`
3. `03-rubric-catalogue.md`
4. `04-ranked-proposals.md`

### Verified starting point

#### Repository and runtime

**Clean baseline**

| Item | Verified value | Method |
|---|---:|---|
| Base | `main` at `9c20aaf` | `git branch --show-current`; `git rev-parse HEAD` |
| Tracked changes | 0 | `git status --porcelain --untracked-files=no` |
| Existing reducer | 2,005 lines; schema 6 | source inspection |
| Existing tests | 49 passed in 0.289 s | isolated Python 3.12, `unittest discover` |
| Direct tests for `retro.py` | 0 | test inventory |
| Python entrypoint on shell PATH | only `uv` | command discovery |

#### Corpus benchmark

**Current reducer is already fast**

Measured against the local Claude corpus at this commit, with work products in a
temporary directory outside Git:

| Item | Value |
|---|---:|
| JSONL transcript files | 2,343 |
| Raw bytes | 1,297,102,524 |
| Measured transcript rows | 2,321 |
| Main-session rows | 595 |
| Subagent rows | 1,726 |
| Non-transcript journals | 22 |
| Unreadable files | 0 |
| Full rebuild | 7.100 s |
| Incremental no-change run | 0.192 s |
| Metrics ledger | 2,263,115 bytes |

This baseline forbids “DuckDB is faster” as an argument by assertion. A new
backend must beat the workload that matters or improve statistical capability
enough to justify its installation and portability cost.

#### Cross-harness selection oracle

**Human provenance is source-specific**

The installed `working-review` extractors selected 30 substantive direct-user
sessions per source with zero parse errors. Codex needed 132 candidates and
excluded 84 subagents plus 13 automations. Claude needed 329 candidates and
excluded 1,748 subagent files plus 298 non-direct sessions. All selected Codex
rows had `thread_source=user`; all selected Claude rows passed direct-human and
substantive checks. These selectors become adapter-oracle fixtures, not code to
copy blindly.

### Cited landscape

#### Trace representation

**Use standards as a mapping seam**

[OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
standardize provider, operation, agent, and tool attributes. The
[OpenInference specification](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md)
adds explicit `LLM`, `CHAIN`, `TOOL`, `AGENT`, `GUARDRAIL`, `EVALUATOR`, and
`PROMPT` span kinds. V1 will map local records onto that vocabulary while
retaining source-native attributes under an adapter namespace. It will not
pretend archived CLI transcripts were emitted as complete OTLP spans.

#### Agent evaluation

**Evaluate outcome and trajectory**

[LangSmith’s complex-agent evaluation](https://docs.langchain.com/langsmith/evaluate-complex-agent)
separates final-response and trajectory evaluation. The
[OpenAI Agents SDK tracing guide](https://openai.github.io/openai-agents-js/guides/tracing/)
captures generations, tool calls, handoffs, guardrails, and custom events.
Accordingly, V1 scores the final outcome, individual spans, and the tool/handoff
trajectory separately; none may be inferred from another.

#### Scorers and judges

**Deterministic first, judges calibrated**

[Inspect](https://inspect.aisi.org.uk/scorers.html) treats exact, similarity,
model-graded, and arbitrary rubric scorers as composable forms.
[MLflow](https://mlflow.org/docs/latest/genai/eval-monitor/scorers/index.html)
separates code scorers from built-in and custom judges and recommends
domain-specific criteria derived from real failures. [Phoenix evaluator
tracing](https://arize.com/docs/phoenix/evaluation/llm-evals/evaluator-traces)
records evaluator prompts, models, scores, explanations, timing, and execution
details. V1 therefore records scorer version, input contract, abstentions,
explanation, latency, and estimated cost for every score. Model judges must be
validated against held-out human labels and may abstain.

#### Datasets and experiments

**Compare identical inputs**

[MLflow’s trace evaluation workflow](https://www.mlflow.org/docs/latest/genai/eval-monitor/running-evaluation/traces/)
reuses trace collections for multiple evaluation runs, while its
[evaluation-dataset guidance](https://mlflow.org/docs/latest/genai/datasets/)
uses real traces and golden examples to compare versions and prevent
regressions. V1 snapshots immutable dataset manifests, evaluator versions, and
closed time windows. Naive before/after reports remain descriptive, never
causal.

#### Local analytics and uncertainty

**Benchmark columnar queries; use established statistics**

[DuckDB](https://duckdb.org/docs/current/guides/file_formats/query_parquet)
queries Parquet in parallel with projection and filter pushdown.
[SciPy’s bootstrap](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html)
supports paired resampling and bias-corrected accelerated confidence intervals.
Both are candidates, not conclusions. The dependency benchmark below decides
whether they become required, optional, or rejected.

### Architecture

#### Source adapters

**One protocol, honest capabilities**

Create `plugins/p/retro_eval/` as an importable package and keep
`plugins/p/bin/retro.py` compatible. The adapter protocol has one method to
discover source files and one iterator that yields normalized records. Each
adapter publishes a capability map with `available`, `unavailable`, or
`version_floor` for human provenance, subagent identity, skill attribution,
usage, cache, tools, handoffs, permissions, hooks, and outcome evidence.

Implement exactly two adapters in v1:

1. Claude Code, grounded by the current reducer and direct-human oracle.
2. Codex, grounded by rollout `session_meta`, `response_item`, tool, usage, and
   collaboration records observed in fixtures and local samples.

Ambiguous legacy records are excluded by default and counted by reason. Missing
capability produces `not_observable`, never zero.

#### Modularity boundary

**Configuration owns policy; adapters own source syntax**

The first implementation audit found four change-prone decisions embedded in
Python: the adapter list and discovery globs, direct-prompt and handoff mappings,
dataset split share, and proposal suppression/ranking. They are now versioned
profiles under `plugins/p/profiles/` and `plugins/p/rubrics/policy.json`.
The pipeline receives adapter and trace-store registries by injection, and the
benchmark command receives a backend registry. Adding a source, store, or
benchmark backend does not edit pipeline or benchmark dispatch call sites; adding a namespaced span
kind or catalogue annotation does not edit the core enum or loader.

Keep source-record parsing in its adapter. Moving arbitrary record expressions
into configuration would create an untyped interpreter and a security/review
burden. The acceptance rule is narrower and testable: policy changes are data;
source syntax changes are isolated adapter code; orchestration knows neither.
All uncalibrated rubric thresholds are marked `provisional` and `unvalidated`.

#### Normalized trace model

**Content-free analytical core**

The core trace/span record contains:

| Group | Fields |
|---|---|
| Identity | `schema_version`, locally keyed `trace_id`, `span_id`, `parent_span_id`, `source`, `adapter_version`, `source_version` |
| Shape | `trace_kind`, `span_kind`, `actor_kind`, `main_or_subagent`, `workflow_depth` |
| Time | `started_at`, `ended_at`, `duration_ms`, `sequence` |
| Result | `status`, `ending`, `error_kind`, `outcome_evidence[]` |
| Tooling | `tool_kind`, `call_signature`, `target_kind`, `handoff_target_kind` |
| Usage | input/output/cache tokens when observable, latency, provider/model label when safe |
| Skills | attributed skill IDs, candidate skill IDs, chain position, observable trigger source |
| Provenance | source file fingerprint, source line/event index, eligibility/capability flags |
| Extension | namespaced additive attributes; unknown fields survive round-trip |

IDs are keyed hashes using a random local salt stored under `RETRO_HOME`; this
prevents dictionary recovery from tracked or shared reports. Message text is not
stored in the analytical record. A separate evidence store may contain minimal
redacted excerpts and is never a committed artifact.

Every schema or metric-definition change increments its version and re-derives
all affected data. Mixed-version aggregates are rejected.

#### Dataset manifests

**Reproducible without copying transcripts**

Each dataset is a manifest over trace/span IDs with:

1. source adapters and versions;
2. closed timestamp bounds;
3. inclusion and exclusion predicates;
4. population counts and exclusion reasons;
5. rubric/scorer versions;
6. deterministic seed and split assignment;
7. content/redaction policy;
8. source fingerprints and creation commit.

Train/calibration/test splits are stable by keyed hash. Threshold tuning cannot
read the held-out test split. A dataset with fewer than the scorer’s declared
minimum population produces `insufficient_evidence`.

#### Scorer protocol

**Every score explains itself**

A scorer consumes declared trace/span capabilities and returns:

`scorer_id`, `version`, `scope`, `value`, `label`, `abstained`, `reason`,
`evidence_refs`, `population`, `eligible_population`, `latency_ms`,
`estimated_cost`, and `limitations`.

Three implementations share this contract:

1. deterministic scorers for exact observable behavior;
2. human-label scorers for calibration and adjudication;
3. a provider-neutral model-judge interface, tested with a fake local judge in
   v1 and never sent private text without separate authority.

Evaluator executions are traces too. Their rubric, prompt hash, model/config,
inputs by evidence reference, outputs, cost, and failure are auditable.

#### Metric catalogue

**No denominator-free number**

Create `plugins/p/rubrics/metrics.json`. Every metric defines:

`id`, `version`, `domain`, `unit`, `direction`, `numerator`, `denominator`,
`eligible_population`, `source_capabilities`, `version_floor`, `minimum_n`,
`uncertainty_method`, `validation_dataset`, `known_biases`, and `retirement_rule`.

The v1 domains are:

| Domain | Required measures |
|---|---|
| Skills | observed invocation, opportunity candidate, missed trigger, false trigger, dormant/superseded overlap, chain order, skill context bytes, post-use outcome |
| Prompts/rules/hooks | loaded bytes, duplicated or conflicting clauses, applicable behavior, compliance, correction after applicability, hook firing/failure, effect after versioned change |
| Agent/orchestration | fan-out, depth, delegation latency/cost, duplicated work, tool/schema/workspace failures, handoff completeness, parent reuse of result, outcome by strategy |
| Context/cost | input/output/cache tokens, instruction share, repeated context, rereads, duplicate calls, latency, cost where current prices are sourced and versioned |
| Clarity/process | correction candidates, interrupts, continuation-only turns, stopped promises, premature final, discretionary handoff, required review gate, plan churn |
| Security/privacy | permission transitions/refusals by eligible population, path/identity leakage, unredacted output, unsafe target, secret-shaped content, external judge exposure |
| Outcomes | tests/build/verification evidence, requested artifact existence, final state, review decision, regression after change, unresolved blocker |

Do not combine these into one quality score. Rank proposals by evidence quality,
estimated avoidable cost, recurrence, and changeability, with each component
visible.

#### Rubric catalogue

**Versioned judgments with abstention**

Create `plugins/p/rubrics/rubrics.json`. Each rubric defines scope, required
inputs, label set or scale, ordered criteria, positive and negative examples,
evidence required, abstention conditions, minimum sample, calibration set,
target precision/recall/agreement, and change history.

The implemented provisional catalogue contains twelve rubrics covering:

1. direct-human prompt versus injected/tool/meta content;
2. required gate versus discretionary handoff versus continuing status;
3. skill opportunity, correct trigger, missed trigger, false trigger, and chain
   ordering;
4. duplicate work versus legitimate repeated observation;
5. correction, clarification, approval, continuation, and new request;
6. completed outcome, review-needed outcome, legitimate blocker, premature stop,
   and stopped promise;
7. complete, missing, contradictory, redundant, or unsafe direct-human prompt contracts;
8. complete, underspecified, redundant-context, or unsafe agent prompts;
9. effective versus unused subagent result;
10. evidence-backed versus conjectural proposal;
11. security/privacy incident versus expected permission control; and
12. the legacy five-class friction protocol, restricted by versioned allowed-use
    policy to candidate sampling, human annotation, and scorer validation.

Human and model-judge annotation for the legacy friction rubric is governed by
the separate, versioned `turn-friction-dominant-intent` protocol in
`plugins/p/rubrics/annotation-protocols.json`. It defines all labels,
dominant-intent precedence, overlap tie-breaks, and the judge prompt as data.
The loader verifies that its labels exactly match the referenced rubric and
computes a canonical SHA-256 binding. Annotation packets and prediction
manifests carry that protocol identity and hash; the human guide is rendered
from the same object. Changing any decision-producing definition or prompt
therefore invalidates prior predictions without changing the preserved legacy
rubric or predictor.

The human review client is a replaceable loopback adapter over the same packet
contract. `AnnotationWorkspace` loads and validates the external CSV, manifest,
rubric, and annotation-protocol hash; its update method uses optimistic
revisions and atomic replacement while proving the immutable packet fingerprint
is unchanged. The shipped browser client provides one-case-at-a-time review,
five keyboard-addressable decisions, progress, notes, clear/back navigation,
autosave, and resume. The server refuses non-loopback binds and requires a
per-process CSRF token for every write. CSV remains canonical, so another TUI or
desktop client can reuse the workspace without reimplementing label integrity.

Protocol v2 replaces the first human-facing wording before any held-out truth
was entered. The v1 interface exposed canonical classifier names without first
stating the rater's task; the operator could not tell what judgment Case 1
required. V2 asks "What is the user doing with this reply?" and stores a plain
action plus explanation for every canonical label in protocol data. The v1
packet and truth-blind predictions remain archived. The unchanged 40-case
sample, guide, deterministic predictions, and truth-blind model predictions
were re-derived under protocol v2 hash `6673408c...fb4f0bb`; both prediction
artifacts validate 40/40. Human truth is complete at 40/40; held-out agreement
is 0.275 for the rule and 0.450 for the frozen model judge, so neither can
provide decision support.

#### Proposal contract

**Executable, reversible recommendations**

Every proposal contains:

`proposal_id`, `rank`, `target_kind`, `target_ref`, `population`, `window`,
`evidence_refs`, `evidence_rubric_ids`, `observed_rate`, `comparison`, `effect_size`, `uncertainty`,
`confidence`, `expected_impact`, `exact_change`, `experiment`, `success_threshold`,
`rollback`, `dependencies`, `risks`, and a lifecycle status of `proposed`,
`accepted`, `implemented`, `rejected`, or `superseded`.

A proposal is suppressed when it has fewer than two concrete sessions, lacks an
eligible denominator, duplicates an active instruction, cannot name an exact
change, or cannot define a falsifiable experiment. Online tool scouting starts
only from a surviving measured seam and cites primary documentation.

Label-derived proposals must declare rubric provenance. A provisional rubric
cannot support an unrelated change unless its versioned catalogue entry permits
`decision_support`; candidate sampling and scorer self-validation remain allowed.
Resolved proposals stay visible but leave the active ranking.

### Dependency benchmark

#### Storage candidates

**Measure before adoption**

Benchmark the same normalized dataset through:

1. stdlib JSONL baseline;
2. DuckDB over JSONL;
3. DuckDB over Parquet;
4. Polars only if DuckDB leaves a measured transformation bottleneck.

Measure five isolated runs after one warm-up for full ingest, incremental append,
30-day filter, grouped domain aggregation, trace reconstruction, dataset split,
and report generation. Record median, p95, peak working set where observable,
on-disk bytes, cold dependency setup, and locked dependency size.

Adopt a non-stdlib storage path only if it provides at least one decisive benefit
without a regression larger than 20% on another critical path:

1. at least 2× median or p95 improvement on two report workloads;
2. at least 40% lower peak memory or stored bytes; or
3. a required query/statistical capability whose correct stdlib implementation
   would create more maintenance surface than the dependency.

The backend interface is created regardless; only the winning implementation is
shipped. If JSONL wins, record the rejection and keep the seam.

Measured on the frozen 279,780-span / 1,859-trace normalized corpus, after one
warm-up and across five isolated runs:

| Backend | Stored bytes | Ingest median | Append median | Filter median | Group median | Trace median | Split median | Report median |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| stdlib JSONL | 155.27 MiB | 0.7813 s | 0.0036 s | 0.7985 s | 0.8128 s | 0.8006 s | 0.7915 s | 0.8381 s |
| DuckDB over JSON | 155.27 MiB | 0.1452 s | 0.2133 s | 0.1400 s | 0.1442 s | 0.1449 s | 0.1469 s | 0.1448 s |
| DuckDB over Parquet | 11.90 MiB | 0.4497 s | 0.0147 s | 0.0044 s | 0.0038 s | 0.0011 s | 0.0034 s | 0.0016 s |

Decision: retain JSONL as canonical append/replay storage and adopt
DuckDB/Parquet only as a regenerable analytical cache. Parquet is 92.3% smaller
and accelerates analytical workloads by roughly 182--751 times, while canonical JSONL
preserves the measured append advantage and dependency-free portability.
DuckDB adds 34.4 MiB installed; the complete pinned DuckDB/NumPy/SciPy optional
environment adds 184.4 MiB. A cold approved install completed in 7.3 seconds.

#### Statistical candidates

**Uncertainty is mandatory**

Implement exact confusion matrices, precision, recall, specificity, F-scores,
Cohen’s kappa, prevalence, and Wilson intervals with deterministic fixture
oracles. Benchmark and validate SciPy BCa paired bootstrap for rate differences,
medians, and cost deltas. Adopt SciPy when BCa intervals are used in delivered
reports; otherwise reject it rather than carrying an unused dependency.

Use effect sizes and confidence intervals, not significance alone. Apply paired
comparisons when the same traces are rescored. Multiple exploratory comparisons
are labelled exploratory; confirmatory claims declare their family and
correction rule before evaluation.

### Execution plan

#### Task 1 — Freeze compatibility and source oracles

**RED before refactor**

1. Add direct tests for current `retro.py` schema, selection, counters,
   redaction, eligibility denominators, exit codes, and incremental state.
2. Add minimal synthetic Claude and Codex fixtures covering direct human,
   automation, injected meta, subagent, tool result, skill attribution, usage,
   handoff, error, and final outcome records.
3. Assert the 30+30 working-review selections through adapter-oracle summaries
   without committing paths or excerpts.
4. Run the new tests and confirm they fail because the adapter/schema modules do
   not exist; record the exact failure.

#### Task 2 — Implement schema and adapters

**One implementation per seam**

1. Implement additive dataclasses/typed dictionaries for normalized records,
   capabilities, evidence references, and dataset manifests.
2. Implement keyed local IDs and content-free serialization.
3. Implement Claude and Codex adapters independently.
4. Add exclusion-reason and capability-coverage reports.
5. Make focused tests green, then run all existing tests.

#### Task 3 — Implement datasets, metrics, and deterministic scorers

**Definitions are data**

1. Add failing schema-validation tests for metric and rubric catalogues.
2. Add dataset snapshot/split/replay tests, including version mismatch and
   insufficient-evidence cases.
3. Implement deterministic scorers domain by domain, starting with provenance,
   outcome, tool, repetition, and existing measured signals.
4. Preserve `not_observable`, `not_applicable`, zero, and missing as distinct
   states.
5. Make focused and full suites green after each domain.

#### Task 4 — Benchmark storage and statistics

**Adopt only the winner**

1. Build a reproducible benchmark command that writes raw results only under
   `RETRO_HOME` and prints aggregate timings/counts.
2. Run the declared storage matrix on the frozen corpus snapshot.
3. Validate statistical functions against fixtures and SciPy where applicable.
4. Record the decision, version pins, install cost, and rejected alternatives.
5. If dependencies win, add a locked, isolated environment and a cross-harness
   launcher without breaking the stdlib-compatible existing commands.

#### Task 5 — Calibrate rubrics

**No threshold sees the test split**

1. Import existing marked labels only into local versioned datasets.
2. Draw additional stratified candidates from both harnesses with stable seeds.
3. Separate calibration and held-out test splits by keyed trace ID.
4. Tune only on calibration; report held-out confusion matrices, precision,
   recall, agreement, prevalence, and confidence intervals.
5. Retire or narrow rubrics that miss their declared target instead of hiding a
   weak class inside a macro average.

#### Task 6 — Implement proposals and effect experiments

**From evidence to one exact action**

1. Add failing tests for proposal completeness, suppression, ranking stability,
   duplicate active guidance, and abstention.
2. Generate candidates from scorer results, metric movement, and repeated
   evidence clusters.
3. Attach exact file/config/prompt changes without applying them.
4. Define matched replay, closed-window, or prospective experiments per proposal.
5. Implement tool scouting only after a measured seam survives suppression.

#### Task 7 — Produce the baseline and review set

**Current view only**

1. Archive any prior review set before writing a new one.
2. Run both adapters and every eligible scorer against a closed corpus snapshot.
3. Emit the four numbered Markdown artifacts plus machine-readable supporting
   JSON outside Git.
4. Audit every claim for denominator, uncertainty, evidence references, source
   imbalance, unsupported inference, stale instructions, and privacy.
5. Stop at `Review needed` with one question: which proposals are accepted,
   rejected, or revised?

#### Task 8 — Close the repository completely

**Verified intended base**

1. Run the complete retro, review, format, statusline, and applicable core tests.
2. Parse both marketplaces and every Claude/Codex plugin manifest; confirm retro
   versions/descriptions match.
3. Run `git diff --check`, inspect the complete diff, and audit documentation
   against behavior.
4. Run `sh plugins/p/bin/repo-privacy-audit`; classify the documented commit
   metadata exception separately from any real finding.
5. Commit with a substitution-safe message, read the commit back, verify the
   intended base is clean, and perform the required `$draft-goal` refinement
   review before marking the Goal complete.

### Validation ledger

#### Commands

**Record every RED and GREEN result here during execution**

| Stage | Command | Expected evidence | Current result |
|---|---|---|---|
| Baseline | isolated Python 3.12 `-m unittest discover -s plugins/p/tests -t plugins/p/tests -v` | native unittest summary | 49 passed, 0 failed, 0.289 s |
| Baseline extract | `retro.py extract --rebuild` with temporary `RETRO_HOME` | counts, exit, wall time | 2,343 walked; 2,321 measured; 22 non-transcripts; 0 unreadable; exit 0; 7.100 s |
| Baseline incremental | `retro.py extract` against unchanged snapshot | unchanged count, exit, wall time | 2,343 unchanged; exit 0; 0.192 s |
| Adapter/core RED | isolated Python `-m unittest plugins.retro.tests.test_eval_core plugins.retro.tests.test_eval_adapters -v` | missing-package failure before implementation | 2 import errors: `No module named 'retro_eval'` |
| Adapter/core GREEN | same focused command after implementation | all focused contracts pass | 13 passed, 0 failed, 0.035 s |
| Full plugin tests | isolated Python `-m unittest discover -s plugins/p/tests -t plugins/p/tests` | all pass | 146 passed, 0 failed, 1.472 s with isolated optional dependencies; dependency-free run retains only the 2 expected DuckDB/SciPy skips |
| Other repository tests | isolated Python discovery for core, review, and statusline | no adjacent regressions | 23 passed, 0 failed |
| Modularity RED | focused discovery of `test_eval_modularity.py` | missing registry before implementation | 1 import error: `retro_eval.adapters.registry` absent |
| Modularity GREEN | focused discovery of `test_eval_modularity.py` | additive adapter/backend/span/policy checks pass | 5 passed, 0 failed, 0.012 s |
| Catalogue extension RED | focused discovery of `test_eval_catalogs.py` | missing extension round-trip and provisional state | 2 errors |
| Catalogue extension GREEN | focused discovery of `test_eval_catalogs.py` | extension and provisional-state contracts pass | 7 passed, 0 failed, 0.017 s |
| Full tests after modularity refactor | isolated Python `-m unittest discover -s plugins/p/tests -t plugins/p/tests` | no regression | 80 passed, 0 failed, 0.392 s |
| Storage benchmark RED | focused discovery of `test_eval_benchmark.py` | benchmark module absent | 1 import error: `retro_eval.benchmark` absent |
| Storage benchmark GREEN | focused discovery of `test_eval_benchmark.py` | content-free seven-workload output and work-root guard | 2 passed, 0 failed, 0.010 s |
| Backend-registry RED/GREEN | focused benchmark test | additive backend initially cannot import, then runs without dispatch edits | import error, then 4 passed |
| Storage benchmarks | `retro-eval-benchmark` for three backends, one warm-up plus five runs | JSONL/backend comparison | final 279,780-span / 1,859-trace snapshot; JSONL canonical plus optional Parquet cache selected from measured result |
| Statistics RED | focused paired-effect and confusion contracts | missing F1/prevalence and backend seam | `KeyError: f1`; missing `paired_effect_interval` import |
| Statistics GREEN | focused statistics contracts with optional dependencies isolated | exact metrics and seeded BCa | 4 passed, 0 failed, 1.798 s with SciPy |
| Legacy calibration RED/GREEN | current-rule report regression | stale stored prediction fails, recomputed rule passes | regression failed at 0/1 then passed at 1/1 |
| Calibration | imported legacy human calibration labels | weighted per-class precision/recall plus agreement interval | 300 labels; weighted population 4,129; agreement 0.8067 (95% CI 0.7582--0.8474); kappa 0.7237; retained as calibration-only |
| Model judge | provider-neutral private-data gate plus held-out predictions | no external disclosure; reproducible protocol and prompt; no self-labelled truth | 5 interface tests pass; replacement predictions bind the stored prompt and protocol and validate 40/40; held-out agreement is 0.450 (95% CI 0.307--0.602), so the judge remains uncalibrated |
| Held-out packet RED | focused annotation and label tests | dataset identity is configurable; prediction comparisons cannot silently intersect cases | hard-coded dataset id, discarded source character counts, missing deterministic packet predictor, and silent partial-case scoring exposed |
| Held-out packet GREEN | focused annotation and label tests plus external artifact validation | manifest-driven packet, configurable predictor, exact case coverage, and resolved fingerprints | packet v2 preserves all 40 case IDs and true character counts; protocol-bound rule and model artifacts each validate 40/40 through the same immutable sample fingerprint; truth is complete at 40/40 with 7 notes |
| Held-out comparison | strict comparison of immutable human snapshot against both frozen predictors | precision/recall, agreement, kappa, prevalence, and intervals without retuning | rule agreement 0.275 (95% CI 0.161--0.428), kappa 0.0546; model agreement 0.450 (0.307--0.602), kappa 0.2685; neither reaches the 0.80 promotion threshold |
| Human-label preservation | source/hash checks plus two external backups | completed private work is recoverable before downstream scoring | canonical CSV, adjacent snapshot, and durable non-Temp backup each contain 40 labels and 7 notes; all 13 bundle files hash-match; scoring imported from the durable snapshot and did not rewrite the source |
| Annotation protocol RED | focused catalogue and annotation tests | explicit decision rules, reproducible prompt, and packet/prediction binding | import error for the absent protocol loader and rejected `annotation_protocol` packet argument |
| Annotation protocol GREEN | same 20 focused tests plus full plugin suite | definitions and precedence are data; guide and judge use one hashed source; manifests cannot drift | 20 focused tests pass; full suite remains green after the annotation UI addition |
| Annotation UI RED/GREEN | focused workspace and HTTP tests | local review without manual CSV edits; no new dependency or private repository artifact | absent module import, then 7 focused UI tests pass for protocol loading, atomic resume, unlabelled-note persistence, stale-write rejection, loopback binding, safe structured transcript rendering including flattened reference points, packaged accessibility hooks, CSRF, and HTTP persistence |
| Note navigation RED/GREEN | focused packaged-client contract, workspace persistence test, and live asset fetch | Back/Next cannot discard a typed note | packaged-client test failed because navigation bypassed persistence; then 7 focused tests pass, JavaScript syntax passes, and the live server returns dirty-note tracking plus save-before-move behavior |
| Human instruction RED/GREEN | protocol catalogue and real review feedback | rater can identify the requested judgment without knowing classifier terminology | Case 1 was not actionable under v1; v2 makes the task question and all five plain-language actions versioned protocol fields shared by guide, UI, and model prompt |
| Real corpus | new baseline command with current Goal session excluded | aggregate report, no raw paths/text | 2,587 files; 1,859 included traces; 728 excluded; 279,780 spans; deterministic report complete |
| Metric coverage RED/GREEN | focused report contract | every catalogue metric gets an explicit per-source state | missing `coverage`, then 25 metrics reported per source: Claude 4 measured/10 unavailable/11 unscored; Codex 3/10/12 |
| Dataset manifest RED/GREEN | extraction/report contracts | source-set fingerprints and closed replay contract | missing fingerprints/argument, then embedded manifest with 2 source hashes, 12 rubric versions, 4 scorer versions, time bounds, populations, split policy, and creation commit |
| Judge abstention RED/GREEN | focused judge contracts | no rubric-label assumption | legacy five-class construction error and native ambiguity misclassification, then 5 judge tests pass with configurable abstention |
| Proposal evidence RED/GREEN | strict proposal review | every trace and named evidence reference resolves to a fingerprinted artifact | missing resolver and launcher, then 6 proposal-report tests pass and strict real review resolves all references |
| Proposal claim RED/GREEN | focused proposal and report contracts | observed rates and populations resolve to exact fingerprinted JSON values; evidence units cannot silently fall back to session counts | ranked P1/P4 serialized zero independent units despite passing, and observed values were hand-copied; then strict JSON-pointer bindings reproduce all observed proposal values and serialize 60/1,799 independent sessions correctly |
| Proposal handoff RED/GREEN | focused generated-Markdown contract | every ranked review tells the operator exactly how to respond while preserving stable proposal IDs | generated report lacked a concrete decision instruction; then 8 focused tests pass and regenerated output asks the reviewer to approve, reject, or revise by proposal ID |
| Rubric-use gate RED/GREEN | focused catalogue, legacy compatibility, proposal, and lifecycle tests | candidate-only guesses cannot rank sessions, appear in effect analysis, or justify unrelated changes | `calibration_only` was inert metadata and legacy guesses still contributed to ranking; then versioned allowed uses drive both legacy output and proposal suppression, label evidence requires rubric provenance, and implemented P8 leaves the active ranking |
| Manifests | JSON parser over both marketplaces and every plugin manifest | zero parse/synchronization errors | 14 JSON files parse; 6 plugin names and all supported version/description fields synchronized |
| Privacy | `sh plugins/p/bin/repo-privacy-audit` | no non-metadata finding | final staged audit: zero path/IP/private-file hits; only the documented accepted commit-metadata email category remains |
| Diff | `git diff --cached --check`; staged-name and targeted semantic review | no whitespace errors or unrelated/private content | passed; population-unit and benchmark-dispatch defects found during review, regression-tested, and fixed |
| Closeout | implementation commits on intended base; clean tracked worktree | zero tracked changes | v1 foundation `bdca96c`; coverage/evidence hardening `1af31e6`; held-out artifact hardening `c18f667`; proposal-claim binding `1cd46e2`; annotation protocol `f4af53f`; local annotation UI `37b1199`; transcript formatting `292d185`; reference-line recovery `8b1c253`; human instruction v2 `6ec6e06`; note navigation `0821fcc`; held-out evaluation `3cd4473`; candidate-only enforcement `d427adc`; stale-prerequisite check `0637ef6`; verified on `main` |
| P3 RED | focused `test_eval_instruction_manifest` | manifest module and pipeline binding absent before implementation | missing-module import, then unsupported `instruction_manifest_path` confirmed |
| P3 GREEN | focused instruction-manifest and pipeline tests | external content-free writer, activation resolution, v2 manifest binding | 10 focused tests passed |
| P3 regression | full retro unittest discovery | no adjacent regression | 151 passed with 2 declared optional-dependency skips |
| P3 coverage RED/GREEN | focused pipeline and manifest tests | a current manifest cannot be assigned retroactively; schema v3 proves every evaluated trace resolved | future activation incorrectly accepted older fixture sessions, then 11 focused tests passed with 2/2 traces resolved and 0 unresolved; historical reports keep provenance absent |
| P1/P4 taxonomy RED | focused catalogue and taxonomy tests | versioned candidate classes, separate denominators, adaptive evidence requirements, and unvalidated scorer state | taxonomy profile and adaptive packet machinery absent; catalogue versions and protocols rejected |
| P1/P4 taxonomy GREEN | focused catalogue, taxonomy, adapter, and packet tests | class-balanced stable splits, immutable fingerprints, explicit support gaps, and external-only evidence | 13 focused tests passed before the bounded-context correction |
| P1/P4 private-evidence RED/GREEN | focused source-evidence packet test | redacted arguments/results join by normalized span ID without entering normalized traces | missing `private_evidence` module, then focused test passed; all 300 real packet rows resolved redacted evidence |
| P1 repeated-context RED/GREEN | focused long-history packet test | annotation context remains reviewable regardless of intervening call count | 200 intervening kinds produced an unbounded list; then count plus eight recent kinds passed with context under 1,000 characters |
| P4 sampling-balance RED/GREEN | focused redacted-evidence hint test and real manifest inspection | provisional selection strata cover all five support classes without becoming decision labels | real structural data placed all 200 failure rows in `unknown`; versioned sampling hints then produced five represented strata in both calibration and held-out packets |
| P1/P4 regression | full retro unittest discovery after source-evidence integration | no adjacent regression | 161 passed with 2 declared optional-dependency skips after bounded context and versioned P4 sampling hints |
| P1/P4 review packets | external `retro-eval-taxonomies sample` and four `assess` checks | 50/50 duplicate-work and 100/100 failure-kind calibration/held-out rows; immutable packet and protocol hashes | 300 rows generated under `RETRO_HOME`; every row has redacted evidence; P1 has 16--17 cases per structural stratum and P4 has 10--23 per sampling-hint stratum in each split; all four manifests resolve and report `needs_more` with zero labels, as required |
| Taxonomy UI label RED/GREEN | focused annotation UI tests and JavaScript syntax check | browser choices come from the bound protocol rather than a stale fixed list | six-label taxonomy initially returned `unsupported label`; then 7 focused tests and JavaScript syntax passed with dynamic canonical labels |
| Scorer-first assessment RED/GREEN | focused annotation packet/UI tests | rule-based proposed diagnosis and reason are immutable; optional review records Correct, Incorrect, or Unsure without required notes | old label-first packet contract failed; then 14 focused tests passed and packet schema version 4 preserved proposal fingerprints while allowing only review fields to change |
| P1/P4 promotion RED/GREEN | focused adaptive-support test, then affected catalog/taxonomy/adapter/scorer suites | no promotion without held-out class support and every preregistered threshold | missing `assess_taxonomy_promotion` import failed; then the focused gate passed and 48 affected tests passed; empty packets remain `unvalidated` with `decision_support=false` |
| P1/P4 optional packets v4 | archived superseded current set, regenerated four external packets, ran four fingerprint assessments | 50/50 P1 and 100/100 P4 rows with reason-coded proposals and current rubric/protocol versions | 300 rows regenerated; all four fingerprints resolve; zero accepted assessments produces `needs_more`, never implicit acceptance or a user blocker |
| P2 lifecycle RED/GREEN | focused Claude adapter and skill scorer tests | content-free explicit starts, ends, chains, outcomes; opportunity and missed triggers remain not observable | lifecycle spans/scorer absent, then focused adapter/scorer tests passed; unmatched-rate regression failed on missing `unmatched_start_rate`, then 2 focused tests passed with separate start and terminal denominators |
| P2 real capability check | rederived `cross-harness-v4` report plus normalized-byte census | precision/recall >= 0.95, unmatched terminals <= 0.02, bytes <= 2% | 756 starts, 260 matched ends, completion 0.3439, unmatched-terminal rate 0.6561, orphan-terminal rate 0, all 260 outcomes `not_observable`; 715,357 added bytes / 164,887,343 = 0.434%; parked because the source lacks deterministic end/outcome emissions for most starts |
| P7 hook RED/GREEN | `python plugins/p/bin/format-e2e` and focused hook lifecycle tests | only owned deterministic wrappers emit content-free opportunity/start/end events | format suite failed 4 telemetry checks at 28/32, then 32/32 passed; 3 lifecycle tests passed |
| P7 controlled capture | two owned hooks through `retro-eval-hook-events` under external `RETRO_HOME` | precision/recall >= 0.95, unmatched <= 0.02, bytes <= 2%, no message content | precision 1.0, recall 1.0, unmatched 0, 1,672 added bytes, normalized share 0.00103%; scope is repository-owned wrapped hooks only and harness-wide coverage is `not_observable` |
| P5 gate RED/GREEN | focused usage-comparability tests | reject missing/mixed pairing metadata and versions; do not run the paired experiment | missing module import failed, then 3 tests passed; duplicate source row regression failed, then 3 tests passed with exactly one row per source and pair enforced; experiment remains `not_executed` |
| Version re-derivation | current adapters and scorers against external `cross-harness-v4` | current dataset/report versions, historical provenance not overstated | 1,863 traces and 282,851 spans; Claude adapter v3, duplicate/tool-failure scorers v3, skill scorer v2, duplicate rubric v3, failure rubric v2; historical instruction provenance remains absent rather than retroactively assigned |
| Next-stage regression | isolated Python full retro discovery | no adjacent regression after P1--P5 and P7 | 174 passed, 2 declared optional-dependency skips; core 9 passed, review 4 passed, statusline 10 passed |
| Next-stage format validation | isolated Python `plugins/p/bin/format-e2e` | hook output unchanged and owned telemetry complete | 32/32 passed |
| Pre-consolidation next-stage closeout checks | JavaScript syntax, `git diff --check`, manifest synchronizer, privacy audit, primary checkout status | no syntax/whitespace/synchronization/private-data defect on the then-current multi-plugin base | JavaScript syntax passed; diff check passed; 6 Claude and 6 Codex catalogue entries synchronized; privacy audit found only the documented commit-metadata email category; primary remained clean at `92f72f0` |
| Consolidation base RED | remote-base audit against `origin/main` | never resurrect deleted plugin layout or merge 32 unrelated local commits | current remote was 12 commits ahead and the evaluation worktree 32 commits ahead; remote had consolidated into `plugins/p` and did not contain the evaluation stack |
| Consolidation port GREEN | fresh worktree at `45c780a`; evaluation-only mechanical import plus focused legacy, catalogue, taxonomy, usage, hook, adapter, and scorer tests | preserve current format/statusline code while restoring evaluation safeguards under `plugins/p` | initial consolidated run exposed 2 legacy safeguard failures, 1 obsolete cross-harness test, and 3 pre-existing Windows POSIX-launch errors; focused safeguards then passed 24/24 and P2/P7 passed 17/17 |
| Scorer-proposal wording RED/GREEN | focused taxonomy packet and annotation UI tests | never describe regex/signature output as an independent agent judgment | old instructions failed the new wording assertion; then 9/9 tests passed with `rule-based scorer proposal` in generated instructions and UI |
| Windows launcher RED/GREEN | baseline and full consolidated unittest discovery | standard Windows launcher executes the POSIX privacy audit without skipping or weakening its assertions | baseline produced 3 `WinError 193` errors; invoking the audit through `sh` on Windows made the focused 3/3 and full 202-test suite pass with 2 declared optional-dependency skips |
| Durable private evidence | verified copy from the task Temp tree to durable external `RETRO_HOME` | no immutable packet or report depends on Temp retention | 9,262 files and 1,188,790,807 bytes copied with zero failures or extras; full relative-path/content fingerprint matched `976820916ce72f2ed884ae17c156eddc75c72c455396eb8effd0f7323247e2cc`; source retained |
| Current-base re-derivation | current `plugins/p` report and four immutable packet assessments | current scorer/catalogue code resolves the copied trace snapshot without regenerating packet identity | 1,863 traces and 282,851 spans re-derived; every packet fingerprint resolved; zero optional assessments left P1/P4 `needs_more` and barred from decision support |
| Current-base P7 capture | two consolidated owned hooks through current `format-ctl` | revalidate lifecycle capture and overhead after remote format changes | precision 1.0; recall 1.0; unmatched 0; 1,673 added bytes; normalized share 0.001015%; coverage remains repository-owned wrapped hooks only |
| Current consolidated format validation | current `plugins/p/bin/format-e2e` | hook output remains byte-exact while telemetry stays content-free | hook metadata first broke payload discovery at 1/3; Windows-aware exact-argument execution plus lifecycle checks then passed 30/30 |
| Taxonomy review skill RED/GREEN | focused review-workflow, annotation UI, and taxonomy packet tests plus the real private packet set | one invocation discovers and resumes calibration without exposing held-out cases or copying machine-specific launcher commands | missing `retro_eval.review_workflow` failed RED; then 20 focused tests passed and `retro-eval-review status` selected immutable P1 calibration at 0/50 with both held-out packets excluded |
| P1 review-evidence RED/GREEN | first four real operator assessments plus focused adapter, packet, catalogue, and UI tests | repeated-call review must show prior purpose/result, relevant intervening evidence, and current purpose/result rather than a signature placeholder | all first 4/4 assessments were Unsure and all 50 rows used the generic signature sentence; RED then failed on missing intent/result joins and stale packet/protocol versions; 38 focused tests passed with external-only evidence joins, packet v5, duplicate-work protocol v3, and structured review sections |
| P1 review-semantics RED/GREEN | operator found evaluator instructions displayed as a `User reply`; focused packet, catalogue, API, and asset tests | captured evidence, display roles, and the review question must be separate and truthfully named | RED reproduced packet v5 placing `Assess whether...` in `user_turn` and hard-coded `User reply`; GREEN uses packet v6 / protocol v4, rejects evaluator language in repeated-call evidence, and exposes protocol-bound evidence labels and question |
| Mixed interpretation review RED/GREEN | operator clarified that useful review means judging the agent's interpretation of a response or situation, not classifying telemetry | plain-language cards state what happened, the interpretation, why, and the expected action; source evidence is optional detail and the operator only gauges accuracy | missing card module, mixed workflow order, rubric, protocol, and UI failed RED; GREEN adds a calibration-only 5 understanding / 5 agent-judgment packet, source-linked evidence, four accuracy choices, and rejection of raw telemetry in visible summaries |
| Stable review URI RED/GREEN | operator rejected changing loopback ports between review restarts | the single review command always opens the same local address unless explicitly overridden | missing stable-port constant failed RED; port 8765 then failed the live Windows bind check; GREEN uses verified port `http://127.0.0.1:8123/` and records it in the review skill |

### Bounds and parking

#### Primary bound

**Sixty Goal turns**

Reaching 60 turns means stopped incomplete, not complete. Five consecutive turns
without new evidence triggers parking with the failed check, remedies attempted,
remaining blocker, and exact resume condition. Missing authority for external
model-judge calls parks only that live-judge row; it does not excuse unfinished
deterministic infrastructure. The Goal ends at `Review needed`, not by silently
accepting its own proposals.
