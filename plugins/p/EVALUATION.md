# Local agent evaluation

Use this layer when a seven-day Retro pack is too narrow: cross-harness
comparison, scorer calibration, held-out labels, cost analysis, capability
gaps, or reversible improvement proposals. The original `retro.py` commands
remain the dependency-free Claude-oriented workflow.

## Architecture

| Boundary | Versioned definition | Implementation shipped in v1 |
|---|---|---|
| Sources | `profiles/sources.json` | Claude and Codex adapters |
| Trace projection | `profiles/semconv.json` | OpenTelemetry GenAI and OpenInference attributes |
| Storage | injected trace-store and benchmark registries | canonical JSONL; optional regenerable Parquet cache |
| Metrics and rubrics | `rubrics/metrics.json`, `rubrics/rubrics.json` | provisional catalogues with explicit denominators and abstention |
| Scorers | `rubrics/scorers.json` | repetition, outcomes, tool failure, and input cost |
| Evaluation policy | `rubrics/policy.json` | splits, evidence gates, ranking order, and evidence limits |
| Labels | external JSONL plus manifest | human, deterministic, and model-judge records; loopback review UI |
| Coverage | metric catalogue joined to source capabilities and scorer results | every metric is measured, insufficient, unavailable, or unscored per source |
| Instruction provenance | external versioned manifest | generic source kinds, activation boundary, privacy class, and content hashes; reports carry only the manifest SHA-256 |

The seam is intentionally small. Source syntax stays in its adapter; mutable
policy stays in JSON. A new adapter is one profile entry plus one adapter class.
A new scorer is one profile entry plus one class. Neither change edits pipeline
or reporting call sites. A new storage implementation is injected into
`EvaluationPipeline`; a new benchmark backend is one registry entry, not a
dispatch edit. Namespaced span kinds and catalogue annotations round-trip
without changing the core enum or loader.

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

Instruction-source manifests are also external. Create one at each rule
activation boundary, then bind the exact manifest while extracting. The writer
hashes files or directory trees and never persists their paths or content.
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

The command writes deterministic, class-balanced calibration and held-out
packets plus protocol-bound manifests. Each row starts with a reason-coded
rule-based scorer proposal. Optional review asks only Correct, Incorrect, or Unsure; an
Incorrect assessment selects a different canonical label and requires no prose.
Failure packets use versioned redacted-evidence hints only to balance selection.
Later adaptive rounds must name the earlier manifests and reuse the same trace
fingerprint; completed packets are never regenerated or implicitly accepted.
Empty or partial review is valid but cannot promote a taxonomy. Promotion
requires every preregistered held-out class minimum plus all precision, recall,
agreement, polling false-positive, and unknown-rate gates. Any failure keeps the
taxonomy unvalidated and barred from decision support.

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

Repository-owned format hooks can write content-free lifecycle events when
`RETRO_HOME` is set. Only the two hooks declared in `plugins/p/hooks` are
accepted by `retro-eval-hook-events`; its coverage label is
`repository_owned_wrapped_hooks_only`, while harness-wide opportunity coverage
is `not_observable`.

Cross-source usage comparisons must first pass
`retro_eval.usage_comparability.validate_usage_comparison`. The versioned gate
requires exactly one row per source and paired case, matching task family,
difficulty, cache treatment, accounting profile, and source accounting version.
It does not run or authorize a paired experiment.

## Privacy boundary

Set `RETRO_HOME` to a directory outside every Git repository. Normalized traces
contain keyed local IDs, counts, timestamps, tool kinds, and call signatures;
they do not contain message text, tool input/output, source paths, or session
IDs. Annotation CSV files contain redacted excerpts and therefore also stay
outside Git. The work-root, label-store, annotation, report, benchmark, and
Parquet-cache writers reject repository paths.

No command applies a proposal. Reports set `auto_apply` to false; a human must
approve every instruction, skill, hook, orchestration, or configuration change.

Rubric use is also policy, not prose. `rubrics.json` declares allowed uses for
every rubric. Provisional rubrics may sample candidates, support human
annotation, and validate their own scorers; they may not provide decision
support. Proposal candidates that cite label evidence must declare its rubric
provenance. The review generator suppresses an unrelated proposal when that
rubric has not earned `decision_support`, while scorer-validation proposals
remain visible. Resolved proposal statuses remain recorded but leave the active
ranking.

## Reproducible commands

Resolve a Python 3 interpreter and the plugin root first. The examples use
`<python>`, `<plugin-root>`, and external roots supplied by the operator.

```text
<python> <plugin-root>/bin/retro-eval-extract \
  --work-dir <RETRO_HOME>/cross-harness-v1 \
  --root claude=<claude-session-root> \
  --root codex=<codex-session-root> \
  --instruction-manifest <RETRO_HOME>/instructions/<version>.json \
  --exclude-session-id <active-session-id>

<python> <plugin-root>/bin/retro-eval-report \
  --work-dir <RETRO_HOME>/cross-harness-v1 \
  --output <RETRO_HOME>/cross-harness-v1/deterministic-report.json \
  --created-commit <commit> \
  --dataset-id <dataset-id>
```

Install optional analytics into an isolated environment from
`requirements-eval.txt`; do not add them to the plugin's stdlib runtime. Then
benchmark the same trace file through `jsonl`, `duckdb-json`, and
`duckdb-parquet`:

```text
<python-with-eval-deps> <plugin-root>/bin/retro-eval-benchmark \
  --input <RETRO_HOME>/cross-harness-v1/traces.jsonl \
  --backend duckdb-parquet \
  --work-dir <RETRO_HOME>/bench-parquet \
  --output <RETRO_HOME>/bench-parquet.json \
  --runs 5
```

Import existing human marks as calibration-only, draw a fresh cross-harness
test set, and import it after the human fills `human_label`:

```text
<python> <plugin-root>/bin/retro-eval-labels import-legacy \
  --source <existing-labels.jsonl> \
  --labels <RETRO_HOME>/labels/human-calibration.jsonl \
  --predictions <RETRO_HOME>/labels/rule-calibration.jsonl

<python> <plugin-root>/bin/retro-eval-labels sample \
  --extract <claude-extract.json> --extract <codex-extract.json> \
  --output <RETRO_HOME>/labels/heldout.csv \
  --manifest <RETRO_HOME>/labels/heldout-manifest.json \
  --per-source 20

<python> <plugin-root>/bin/retro-eval-labels predict-annotations \
  --source <RETRO_HOME>/labels/heldout.csv \
  --manifest <RETRO_HOME>/labels/heldout-manifest.json \
  --predictions <RETRO_HOME>/labels/rule-test.jsonl \
  --prediction-manifest <RETRO_HOME>/labels/rule-test-manifest.json \
  --created-commit <commit>

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

Freeze both prediction sets before importing truth. The comparison command then
requires exact case coverage and verifies every prediction fingerprint before it
computes per-class precision/recall intervals, agreement, and kappa.

Review packets through the local annotation UI instead of editing CSV by hand.
The server binds only to loopback, validates the packet's rubric and
annotation-protocol hash before serving, rejects cross-origin writes, and saves
each assessment atomically back to the external CSV. Taxonomy packets show the
frozen rule-based proposal and offer Correct, Incorrect, and Unsure. Choosing
Incorrect reveals the current protocol's canonical alternatives. Arrow keys
move between cases; reopening resumes at the first unassessed case.

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
or remotely hosted annotation service.

Generate proposal reviews in strict mode so every trace reference resolves in
the selected normalized snapshot and every named aggregate resolves through a
fingerprinted external evidence index:

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
