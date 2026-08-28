# Codex measurement ingestion — spec

2026-08-28. Status: draft for one review round.

## Problem

`retro.py extract` walks only the Claude Code transcript root, so every
downstream instrument — friction packs, skill-firing lists, trend tables, the
workflow-rules audit — measures one harness. On the development machine the
Codex sessions directory held 317 rollout transcripts at the time of writing,
215 inside the audit's 30-day window, comparable in volume to the Claude
corpus and entirely invisible. A plugin whose premise is cross-harness
operation cannot ground decisions in half a corpus.

`retro_eval/adapters/codex.py` parses rollout format, but it emits evaluation
spans, not metrics rows: the counters the measurement pipeline needs (turn
classification, repeat signatures, endings, eligibility) live only in
`retro.py`'s reducer. This change teaches the measurement pipeline itself to
read Codex history.

## Grounding

Structure verified against the development machine's rollout corpus (structure
probes only; no transcript content was copied anywhere):

1. Top-level record types: `session_meta`, `response_item`, `event_msg`,
   `turn_context`, `world_state`, `compacted`.
2. `session_meta.payload` carries `session_id`, `cwd`, `cli_version`,
   `git.branch`, and `thread_source` with observed values `user`, `subagent`,
   and `automation`.
3. `response_item.payload.type` values: `message` (roles `user`/`assistant`),
   `reasoning`, `custom_tool_call`/`custom_tool_call_output` (dominant; name
   is almost always `exec`), `function_call`/`function_call_output`.
4. `event_msg.payload.type` includes `token_count` (with cumulative
   `info.total_token_usage`), `task_complete`, `turn_aborted` (defined by the
   evaluation adapter; rare in the sample), `user_message`, `agent_message`.
5. Harness-injected user messages open with a wrapper tag: observed
   `<skill>`, `<environment_context>`, `<recommended_plugins>`. A `<skill>`
   block names the skill in an inner `<name>` element.
6. Tool outputs are usually a list of content blocks; only ~4% of sampled
   outputs carry any exit-code marker, and a marker is a command exit, not a
   harness failure.

## Design

### D1. Roots

`extract` walks the Claude projects directory plus the Codex sessions
directory (`~/.codex/sessions`, or `$CODEX_HOME/sessions` when set). A missing
root is skipped silently — a machine with one harness measures one corpus.
File-format detection stays structural (first parsed records), never by path
shape, matching the existing "record types, not filenames" rule.

### D2. One ledger, labelled rows

Every row gains two fields; `SCHEMA_VERSION` bumps 6 → 7, and the existing
mismatch rule rebuilds the whole ledger on first run.

1. `harness`: `"claude"` or `"codex"`.
2. `population`: `"main"`, `"subagent"`, or `"automation"`, replacing
   `is_subagent`. Claude rows derive it from the transcript path as today;
   Codex rows from `thread_source`. Automation rows (scheduled, no human) are
   excluded from per-session rates, reported as their own spend line beside
   subagents — folding them into main sessions would poison every "a human
   had to intervene" rate with sessions that contain no human.

Claude rows' `transcript` stays relative to the projects directory; Codex
rows' are relative to the sessions directory and prefixed `codex/` so the two
namespaces cannot collide. `state.json` keys stay absolute paths, unchanged.

### D3. A parallel reducer, shared classifiers

New `measure_codex(path)` beside `measure()`, sharing the judgment helpers
(`classify_user_turn`, `is_approval`, `signature`, `redact`, `parse_ts`,
moment capture). Explicit non-goal: no layer that fakes Claude-shaped records
from rollout events. The two formats disagree on which signals exist at all,
and a normalization layer would have to invent records for the difference;
the row schema, not the record stream, is the shared contract. The evaluation
adapters stay untouched and unconsumed here — they serve a different schema,
and coupling the two pipelines makes each hostage to the other's changes.

Counter mapping, with eligibility resolved by the existing per-row
`eligible` mechanism:

| Counter | Codex source |
|---|---|
| `turns` | `response_item` assistant messages |
| `user_prompts`, `correction_candidates`, `approval_turns` | plain-text user `response_item` messages — wrapper-tagged and empty ones excluded — classified by the shared classifier with the same prior-assistant-chars accumulation |
| `interrupts` | `turn_aborted` events |
| `tool_calls`, `repeat_calls` | `function_call` + `custom_tool_call`; repeat = same (name, argument-signature) |
| `tool_errors` | ineligible: rollouts carry no harness-refusal marker, and a nonzero exec exit is information, per the existing rule |
| `queued_prompts`, `permission_mode_changes` | ineligible: no equivalent record type |
| `skill_runs`, `skills_used` | `<skill>` wrapper blocks; name from the inner `<name>` element |
| tokens, `cache_read` | last `token_count.info.total_token_usage`; `cached_input_tokens` maps to `cache_read` |
| identity, date, duration | `session_meta` (`session_id`, redacted `cwd`, `git.branch`, `cli_version`) plus first/last record timestamps |
| `ending` | `text` when the last assistant message has prose, `interrupted` on caller abort, else `silent`; `structured` and `unanswered` need signals rollouts lack and never occur for Codex rows |

`skill_runs` carries a documented semantic caveat: Claude counts contiguous
active-skill stretches, Codex counts invocation blocks. The per-harness split
in reporting (D4) keeps the two from being read as one number.

### D4. Reporting

1. `extract` summary prints measured counts per harness.
2. `skills` prints fired counts per harness (one combined table with
   per-harness columns) and one never-fired list against installed skills.
3. `pack` trend tables report combined totals with a one-line per-harness
   split beneath; per-session rates stay population `main` only. Codex
   moments quote through the same redaction path as Claude moments.
4. `subagents` treats Codex subagent rows as spend rows; its transcript-deep
   lens stays Claude-only this changeset and says so when Codex rows are
   present.

### D5. Out of scope, stated

1. `stopped-promises.py`: its reader is duplicated on purpose and documented
   as Claude-only; teaching it rollout format is a follow-up with its own
   verification. Its docstring gains one line naming the asymmetry.
2. The evaluation layer and its adapters: already cross-harness by design.
3. Label sampling (`cmd_label`): pools stay Claude-only this changeset — the
   candidate classifier's precision/recall figures were calibrated on Claude
   turns and do not transfer unmeasured. The command says so when asked.

## Verification

1. Unit tests over synthetic rollout fixtures — constructed by hand, never
   copied from real transcripts — covering: meta parsing and population
   mapping, wrapper filtering, skill-name extraction, each counter mapping,
   endings, cwd redaction, mixed-corpus `skills`/`pack` output shape.
2. Existing contract stays green: full unittest discovery, `format-e2e`,
   `p-validate`, `repo-privacy-audit`, `git diff --check`.
3. Local-only end-to-end (CI has no transcripts): `extract` with `RETRO_HOME`
   pointed at a scratch directory reports ≥200 Codex transcripts measured;
   `skills --days 30` prints the per-harness split. These are the goal's
   evidence gates.

## Parallelization

`retro.py` is a single file; reducer and reporting edits conflict and run as
one serial lane. Independent lanes, file-disjoint, dispatchable together:

1. Lane A (serial internally): `retro.py` — roots, row schema, reducer,
   reporting.
2. Lane B: new test module(s) plus rollout fixture builders under
   `plugins/p/tests/` — written against this spec's row contract, mergeable
   with Lane A's output.
3. Lane C: docs made false by the change — README corpus wording,
   EVALUATION.md boundary note, the friction/audit skill texts that describe
   what the corpus covers, `stopped-promises.py` docstring line.
