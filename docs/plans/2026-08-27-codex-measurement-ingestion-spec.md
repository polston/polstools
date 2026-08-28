# Codex measurement ingestion — spec

2026-08-27. Status: settled — round 1 (four concurrent lenses) and round 2
(fold confirmation) both folded.

Prior art, both read and bound here: `2026-08-19-plan-fix3-extra-roots.md` and
`2026-08-20-plan-extra-roots-rebuild.md` designed multi-root extraction for
this same tool and proved seven defects of a rejected implementation. This
spec adopts the rebuild plan's root-owned row identity and re-states its
missing-root rule rather than re-entering its defect #5.

## Problem

`retro.py extract` walks only the Claude Code transcript root, so every
downstream instrument — friction packs, skill-firing lists, trend tables, the
workflow-rules audit — measures one harness. On the development machine the
Codex sessions directory held 317 rollout transcripts at the time of writing:
54 main sessions, 144 subagent and 17 automation spend transcripts inside a
30-day window, against roughly 460 Claude main sessions in the same window,
plus 3.9GB of bytes against Claude's 1.3GB. The Codex share is a minority of
sessions and a majority of bytes; either way it is invisible today, in a
plugin whose premise is cross-harness operation, and the subagent spend it
mostly consists of is exactly what the `subagents` lens exists to watch.

`retro_eval/adapters/codex.py` parses rollout format, but it emits evaluation
spans, not metrics rows; the counters the measurement pipeline needs live only
in `retro.py`'s reducer. This change teaches the measurement pipeline itself
to read Codex history.

Operator direction, 2026-08-27, recorded as scope: the point of one ledger is
that a session in either harness can see both corpora — where each harness's
sessions go wrong, and, by quoted example, which behaviors the operator
actually likes. Packs therefore quote liked moments (approvals) alongside
frictional ones, from both harnesses, not corrections only.

## Grounding

Verified against the full rollout corpus on the development machine (317
files, ~426,000 records; structure-only probes — record/field/type names and
counts; no transcript content copied anywhere):

1. Top-level record types, by count: `response_item`, `event_msg`,
   `turn_context`, `world_state`, `inter_agent_communication_metadata`,
   `session_meta`, `compacted`. Every file's first record is a
   `session_meta`.
2. `session_meta.payload` carries `session_id`, `cwd`, `cli_version` on every
   meta; `git.branch` on 526 of 681; `thread_source` on 631 of 681 with
   values `user` (75 files), `subagent` (175), `automation` (17), absent
   (50 — all older-CLI files, all outside the 30-day window). 15 files carry
   more than one `session_meta`; `session_id` and `cwd` stay single-valued
   per file; 3 files' metas disagree on `thread_source`. Every subagent
   rollout carries `parent_thread_id`; no other population does.
3. `response_item.payload.type` values: `reasoning`, `message` (roles
   `assistant`, `user`, `developer`), `custom_tool_call`/
   `custom_tool_call_output`, `function_call`/`function_call_output`,
   `agent_message`, and `tool_search_call`/`tool_search_output` — the third
   pair's output name drops `_call`. `exec` is 96.7% of `custom_tool_call`
   names and 74% of all call items.
4. `event_msg.payload.type` includes `token_count` (cumulative
   `info.total_token_usage`: `input_tokens`, `cached_input_tokens`,
   `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`,
   `total_tokens`; the cumulative sequence resets mid-file in 5 of 317
   files), `task_complete`, `turn_aborted` (76 events across 35 files; also
   mirrored as a wrapper tag on 76 messages, so only the event form may be
   counted), `web_search_end` (1,032), `image_generation_end` (18).
5. Of 11,078 user-role messages, 891 open with a harness wrapper tag; 11
   distinct tags were observed, the most common being `task-notification`
   (390), `environment_context` (149), `codex_internal_context` (130),
   `recommended_plugins` (113), `in-app-browser-context` (57), `skill` (29).
   Three of the eleven tags — `codex_internal_context` (130),
   `in-app-browser-context` (57), and `image` (4) — never appear bare; all
   191 of their occurrences carry attributes (`<tag attr…>`), so a
   closed-bracket literal cannot match them. `retro.py`'s `MACHINE_PROMPT_OPENERS` already
   names three of the bare tags.
6. `<skill>` wrapper blocks: 29 across 24 files, every one carrying a
   parsable inner `<name>`. The shipped source profile
   (`plugins/p/profiles/sources.json`) declares `skill_attribution`
   unavailable for the `codex` source; any Codex skill count is therefore a
   heuristic, not an authoritative attribution, and is labelled so.
7. Tool outputs: `custom_tool_call_output.output` is 92% list-of-blocks;
   `function_call_output.output` is 74% plain string. Line-anchored
   `Exit code:` markers appear on 11.8% of outputs; no structured
   success/failure field exists, and a command exit is information, not a
   harness refusal.
8. `retro.parse_ts` accepts all ~426,000 rollout timestamps unmodified.
9. `plugins/p/profiles/usage-accounting.json` is the binding definition of
   both harnesses' token fields: Claude `input_tokens` excludes cache reads;
   Codex `input_tokens` is the total with `cached_input_tokens` a subset. Its
   comparison rule forbids cross-source token statistics outside matched
   pairs.

## Design

### D1. Roots

1. Extraction resolves roots at call time, not import time, so tests inject
   them through the environment: the Claude root is
   `$CLAUDE_CONFIG_DIR/projects` or `~/.claude/projects`; the Codex root is
   `$CODEX_HOME/sessions` or `~/.codex/sessions`. Every Claude-root reader
   in the file — the walk, the moment reader, `cmd_label` and its
   candidate pools — resolves through the same function, and the
   import-time module constant is deleted so a divergent fifth use cannot
   reappear. (This also retires the documented asymmetry in
   `read_records`'s docstring — `stopped-promises` honours the
   config-directory variable and `retro.py` did not.)
2. The missing-root guard moves after root resolution: when every root is
   missing, `extract` exits 2 with a message naming each looked-for path;
   when at least one root exists, absent roots are skipped and named in the
   summary (`codex: root absent`). `cmd_label`'s own Claude-root guard is
   deliberately unchanged. A fixture asserts the exit-2 case.
3. Candidate files are collected from all roots into one map keyed by
   `Path.resolve()`, Claude root first; a file reachable twice is measured
   once under the first root, and the summary prints the duplicate count.
4. Harness detection is structural and asymmetric: a file whose first 20
   parsed records include a top-level `type` from the rollout set
   (`session_meta`, `response_item`, `event_msg`, `turn_context`,
   `world_state`, `compacted`, `inter_agent_communication_metadata`) is a
   rollout — every one of 317 real rollouts is decided by record 1, and no
   Claude transcript's first 20 records contain any of these (0 of 2,362).
   Everything else falls through to `measure()`, whose whole-file
   conversation test keeps deciding NOT_TRANSCRIPT exactly as today —
   Claude transcripts open with a wide spread of sidecar types, and a
   20-record Claude gate would newly drop 4 currently-measured files, which
   this rule deliberately avoids. The evaluation layer selects rollouts by
   filename glob; the two layers may therefore disagree on corpus size, and
   `plugins/p/EVALUATION.md` says so (Lane C).
5. `~/.codex/archived_sessions` is out of scope this changeset, stated here
   so its absence from the numbers is a decision, not an oversight.

### D2. One ledger, labelled rows — schema 7

`SCHEMA_VERSION` bumps 6 → 7; the existing mismatch rule rebuilds the ledger.
New or changed columns:

1. `harness`: `"claude"` or `"codex"`.
2. `population` replaces `is_subagent`: `"main"`, `"subagent"`,
   `"automation"`, or `"unknown"`. Claude rows derive it from the transcript
   path (`subagents/` → subagent, else main) — `automation` is Codex-only,
   so a scheduled Claude session lands in `main`; that asymmetry is accepted
   and stated. Codex rows map `thread_source`: `user` → main, `subagent` →
   subagent, `automation` → automation; when `thread_source` is absent,
   `parent_thread_id` present → subagent, else main; an unrecognised value →
   `unknown`; when `thread_source` is absent, `parent_thread_id` present →
   subagent, else → `unknown` — never `main`, because an unclassified file
   must not enter the population every per-session rate divides by.
   (Measured: all 50 absent-`thread_source` files come from one alpha CLI
   build on a single day, none carrying a parent id; defaulting them to
   `main` would have inflated the Codex all-history main population by two
   thirds.) `unknown` and `automation` rows are excluded from every
   per-session rate and reported as their own spend lines. Rows record how
   their population was decided in `population_source`
   (`"thread_source"` or `"fallback"`), and the `extract` summary prints the
   fallback and `unknown` counts so a new value surfaces instead of being
   absorbed. The first `session_meta` record wins; the summary counts files
   whose metas disagree on `thread_source`.
   The evaluation layer's span schema keeps its own `is_subagent` field on
   purpose — it is a separate versioned contract with its own tests; the two
   are not unified in this changeset. `label_candidates` re-derives the
   subagent fact from the path and is deliberately left doing so.
3. `parent_session_id`: `parent_thread_id` for Codex rows, `""` for Claude
   rows. Needed for self-exclusion (D4.6).
4. `project_key`: stored explicitly — Claude rows keep today's rule (first
   path component of `transcript`); Codex rows use `"cx-"` plus a short
   stable hash of the redacted `cwd`, which groups sessions by workspace
   without naming any path. `project_key()` reads the column.
   `concentration()` is unchanged.
5. `ineligible`: a list of `COUNTERS` names this row's harness cannot
   observe. Codex rows: `tool_errors`, `queued_prompts`,
   `permission_mode_changes` (Grounding 7; no queue or permission-mode
   record exists). Claude rows: empty. This is a new mechanism — the
   existing `eligible` field covers only the seven subagent-lens signals, is
   read only by `cmd_subagents`, and cannot carry these; consumers of
   `ineligible` are specified in D4.1.
6. `compacted`: boolean, true when any `compacted` record is present. Set
   but not otherwise consumed this changeset except a count in the pack
   window header. Whether compaction rewrites or appends history is probed
   during implementation and the answer recorded beside the flag.
7. `transcript` stays relative to its own root. Row identity is
   (harness, transcript); nothing infers harness from the path.
8. Identity fallbacks for Codex rows match Claude's exactly: absent
   `git.branch`/`cwd` → `""`, absent `session_id` → `path.stem`, no parsable
   timestamp → `date=""` and `duration_s=0`; a row with `date == ""` is
   invisible to every windowed command — existing behaviour, kept.

### D3. A parallel reducer, shared classifiers

New `measure_codex(path)` beside `measure()`, sharing the judgment helpers
(`classify_user_turn`, `is_approval`, `signature`, `redact`, `parse_ts`).
Explicit non-goal: no layer that fakes Claude-shaped records from rollout
events; the row schema, not the record stream, is the shared contract. The
evaluation adapters stay untouched and unconsumed here.

Counter mapping:

| Counter | Codex source |
|---|---|
| `turns` | assistant `response_item` messages plus `function_call`, `custom_tool_call`, and `tool_search_call` items — the structural analogue of a Claude assistant record, which carries its tool calls inside itself. `turns` is still never compared across harnesses (D4.2) |
| `user_prompts`, `correction_candidates`, `approval_turns` | user-role `response_item` messages whose first non-whitespace is not a machine-prompt opener and which are non-empty, classified by the shared classifier. The opener list is `MACHINE_PROMPT_OPENERS`, extended with the Codex tags from Grounding 5 — one shared, anchored list for both harnesses, with a test over the union. Tags that only appear with attributes enter the list as bracket-less prefixes (`"<codex_internal_context"`), since a closed-bracket literal matches none of their 191 occurrences; the bare tags keep the existing closed form so a person merely quoting a tag name mid-sentence still counts. `developer`-role messages never count. A message the shared classifier calls an `interrupt` counts as an interrupt, not a user prompt — matching the Claude reducer, though the marker it keys on is Claude-shaped and expected to be absent from rollouts. `reasoning` items do not feed `prior_assistant_chars` (the operator never saw them); assistant messages do |
| `interrupts` | `turn_aborted` events only (the wrapper-tag mirror is excluded to avoid double-counting); 76 corpus events, so the mapping is observed, not assumed |
| `tool_calls` | `function_call` + `custom_tool_call` + `tool_search_call` items, plus `web_search_end` and `image_generation_end` events (tool uses that leave no call item) |
| `repeat_calls` | same (name, argument-signature) — the argument value is `json.loads`-parsed when it is a string (falling back to the raw string) and hashed through `signature()` so key order normalises as on the Claude side; `arguments` preferred over `input`. Polling primitives (`wait`, `wait_agent` — the polls present in the corpus) and empty-argument calls are excluded. Measured: the naive rule flags 14.50% of all-population Codex calls against Claude's 1.05% (main rows, all history; 0.67% over all rows); the exclusions take Codex to 9.88%, so the residual is real workload repetition, not just polling, and the number is recorded here rather than discovered during implementation |
| `tool_errors`, `queued_prompts`, `permission_mode_changes` | ineligible (D2.5) |
| `skill_runs`, `skills_used` | `<skill>` wrapper blocks; the name is the text of the first `<name>` element, trimmed, with `split(":")[-1]` applied as on the Claude side. 29 corpus events — a thin signal, labelled heuristic per Grounding 6 |
| `tokens_in` | last cumulative `input_tokens` minus `cached_input_tokens`, clamped at 0, per the usage-accounting profile (Grounding 9), so the column means non-cache-read input on both harnesses |
| `tokens_out` | `output_tokens + reasoning_output_tokens`, so visible and reasoning output are both spend |
| `cache_read` | `cached_input_tokens` |
| cumulative-reset handling | when a `token_count` total drops below its predecessor, the predecessor is banked and accumulation restarts; the row's value is bank + last (Grounding 4) |
| `ending` | `text` when the last assistant message has prose; `interrupted` on `turn_aborted`; `unanswered` when a call item's `call_id` has no matching output record of its pair's output type — `tool_search_call` matches `tool_search_output` (2 corpus files, 3 unmatched ids); else `silent`. `structured` cannot occur: rollouts have no structured-result tool |
| identity, date, duration | first `session_meta` + first/last record timestamps, fallbacks per D2.8 |
| ignored record types | `reasoning` (except as noted), `turn_context`, `world_state`, `compacted` (except the flag), `inter_agent_communication_metadata`, `agent_message` items |

`measure_codex` writes `eligible = []` — the seven mechanical-failure signals
are Claude harness-refusal markers a rollout never emits — and the `skill_runs`
semantic caveat stands: Claude counts contiguous active-skill stretches, Codex
counts invocation blocks; the per-harness split keeps them apart.

Moments read both harnesses and both polarities. `_moments` gains the
`approval` kind beside `interrupt` and `correction` — an approval's captured
context is the assistant text that earned the "sure/perfect", which is the
operator's liked behavior by example. A parallel `_moments_codex(row)` walks
the rollout under the Codex sessions root: `prior` accumulates from assistant
`response_item` messages and resets on each counted user turn, wrapper-tagged
and empty user messages are skipped with the same shared opener list, `at`
comes from the record `timestamp` or `""`, the same three kinds are captured,
and the same `MOMENTS_PER_SESSION` cap applies. `moments(row)` dispatches on
`row["harness"]` and resolves the root per harness at call time.

### D4. Reporting

1. Per-counter denominators: `totals()` returns a pair of `Counter`s —
   the sums as today, plus per-counter eligible-row counts — and its three
   call sites unpack the pair. `cmd_pack`'s per-session line and
   `cmd_effect`'s per-session column divide each counter by its own
   eligible-row count; when a window contains rows ineligible for a counter,
   the trend table marks that counter's line with the eligible share
   (`N of M rows observable`). Nothing sums a counter over rows that cannot
   observe it into a rate over rows that can.
2. The pack's trend section prints one block per harness (same signal rows,
   that harness's sessions only) plus a single combined `sessions` line.
   Token and turn columns are never summed or compared across harnesses —
   the usage-accounting profile forbids it for tokens and D3's `turns`
   mapping is an analogue, not an identity.
3. Ranking and moment selection are two different acts, and the pack now
   says which is which. `friction_score` — decision-support ranking — is
   unchanged: `permission_mode_changes*3 + tool_errors`, legacy terms
   rubric-gated off (pinned by an existing test), so it ranks Claude
   sessions only; extending it is a rubric-governance change deliberately
   not smuggled in here. Codex sessions instead enter the moments section
   by candidate sampling, which is a use the legacy rubric explicitly
   allows (`candidate_sampling` is in `turn_friction_legacy`'s
   `allowed_uses`): after the Claude ranked list — which prints one line
   naming how many main sessions were not friction-ranked because their
   harness emits no ranking signal — `cmd_pack` selects up to
   the same per-window session cap of Codex main sessions ordered by
   `correction_candidates + interrupts + approval_turns`, skipping zeros,
   under a heading that labels them candidate-sampled, not ranked. Their
   moments quote through `_moments_codex` and the shared redaction. A row
   whose harness the moment reader cannot resolve is skipped rather than
   printed as a headed block with no evidence.
4. `skills` prints fired counts per harness, and per-harness never-fired
   lists: `installed_skills()` gains per-harness roots ($CODEX_HOME `skills/`
   and plugin cache, plus `~/.agents/skills`), each dormant entry names the
   harness(es) it is installed in, and the Codex column carries its
   denominator (files bearing any skill signal) so a near-empty column reads
   as thin evidence, not dormancy.
5. `subagents`: the row filter becomes `population == "subagent"`. The
   mechanical-failure table is restricted to `harness == "claude"` rows and
   prints the excluded Codex row count in its header (their `eligible` is
   empty, so including them would only pad denominators). The endings and
   length tables split by harness — Codex endings draw from a smaller set and
   `silent` means less there (D3), so one mixed denominator would report a
   number belonging to neither — and each per-harness table guards its empty
   case (`quantile` and the length line both raise on an empty list; today's
   single `if not rows` guard no longer covers them).
6. Self-exclusion: `reporting_session_ids` reads `CLAUDE_CODE_SESSION_ID`,
   `CODEX_SESSION_ID`, and `CODEX_THREAD_ID` (the same tuple as
   `plugins/p/bin/format-ctl`; a comment in each names the other), and
   `cmd_subagents` drops rows whose `session_id` or `parent_session_id`
   matches — Codex subagent rows carry the parent's id in
   `parent_session_id`, not `session_id`.
7. `effect`: the comparison defaults to `harness == "claude"`,
   `population == "main"`, because the rule-change dates it is normally
   anchored to come from Claude's config history — and the command cannot
   see where a `--since` date came from (its no-argument mode only prints
   candidate dates for the operator to retype), so the safe default is
   unconditional and `--harness codex|all` opts in to a wider population.
   The output names the restriction. `split_population` returns a dict
   keyed by population value; both call sites (`cmd_pack`, `cmd_effect`)
   are updated; `cmd_effect`'s per-session column divides each counter by
   its own eligible-row count (D4.1 names it as the second consumer) and
   states what it does with `automation` rows (spend line, never in rates).
   The `EFFECT_MIN_SESSIONS` floor now applies to the restricted population.
8. `extract` summary: the existing totals line is unchanged (`unreadable`
   stays a single number — an unopened file has no harness; exit-code rules
   unchanged); added per harness: measured count and sessions-in-ledger,
   plus the root-absent, duplicate-path, population-fallback, `unknown`, and
   meta-disagreement counts from D1/D2.

### D5. Out of scope, stated

1. `stopped-promises.py`: its reader differs from `retro.py`'s materially
   (own root resolution, gzip support, missing-root behaviour) and its
   docstring names no harness; it gains one sentence stating it reads Claude
   transcript format only. Teaching it rollout format is a follow-up.
2. `cache_ttl.py`: a third Claude-only transcript reader; explicitly out of
   scope, same one-sentence docstring note. Nothing here changes
   `parse_ts`'s contract (it already accepts every rollout timestamp).
3. The evaluation layer and its adapters: untouched. Its Codex adapter
   excludes non-`user` threads and takes the last token count where the
   ledger banks across mid-file resets, so eval reports and packs will
   disagree on corpus size by construction and on token totals for the
   handful of reset-carrying sessions; `plugins/p/EVALUATION.md`'s boundary
   note names both (Lane C).
4. Label sampling (`cmd_label`): pools stay Claude-only — the candidate
   classifier's precision/recall was calibrated on Claude turns. The pools
   line gains the suffix `(Claude transcripts only — classifier calibrated
   on Claude turns)`.
5. Ranking Codex sessions (D4.3) and archived rollouts (D1.5): named
   follow-ups, not silent omissions.

### D6. Docs the change makes false (Lane C unless noted)

1. `retro.py`'s own module docstring — harness wording, the exit-2 line, the
   ledger description, the corpus-size figure — is Lane A's, since Lane A
   owns the file.
2. `plugins/p/EVALUATION.md` boundary note (D5.3). (The root README carries
   no corpus wording — checked, nothing to edit there.)
3. `finding-friction-in-recent-sessions/SKILL.md`: the reading-the-signals
   table gains the per-harness eligibility caveat (which counters a Codex
   row cannot observe), named here explicitly so Lane C's sweep reaches it.
4. `auditing-workflow-rules-against-behavior/SKILL.md`: the skills-command
   description reflects per-harness output.
5. D5.1 and D5.2 docstring sentences (file-disjoint from Lane A).

## Verification

1. Claude-side characterisation first: before Lane A's rename lands, Lane B
   pins a synthetic Claude transcript through `measure()` (full row), and
   `split_population`/`totals` over hand-built rows — the existing suite
   exercises none of the code this change rewrites, so "suite stays green"
   is not a regression guard without these.
2. Unit tests over synthetic rollout fixtures — built by extending
   `plugins/p/tests/fixtures.py` (one fixture convention, not a third),
   never copied from real transcripts. Coverage: harness detection
   (including a no-`session_meta` file and a mixed/ambiguous file), meta
   parsing and population mapping including the absent/unknown fallbacks and
   first-meta-wins, wrapper filtering over the extended shared opener list,
   skill-name extraction, every counter mapping including token-reset
   banking and repeat-call exclusions, endings including `unanswered` via an
   unmatched `call_id`, `project_key` hashing, self-exclusion via
   `parent_session_id`, per-counter denominators in `totals`, missing-root
   exit 2 and single-root skip via env-injected roots, and cwd redaction —
   redaction fixtures build their `cwd` from `Path.home()` at runtime and
   assert `~` in the output; no fixture file contains a home-shaped literal.
3. Existing contract stays green: full unittest discovery, `format-e2e`,
   `p-validate`, `repo-privacy-audit`, `git diff --check`.
4. Local-only end-to-end (CI has no transcripts): `extract` with `RETRO_HOME`
   pointed at a scratch directory reports ≥200 Codex transcripts measured
   (317 structural rollouts exist; the measured criterion is at least one
   `response_item` message record) and a nonzero Codex `main` population in
   its per-harness summary — the count alone cannot fail, the population
   split can; `skills --days 30` prints the per-harness split. These are the
   goal's evidence gates.

## Parallelization

`retro.py` is a single file; all its edits are one serial lane.

1. Lane B first, Claude characterisation slice (Verification 1): must land
   before Lane A's rename so the rename has a regression net.
2. Lane A (serial internally): `retro.py` — roots, schema 7, `measure_codex`,
   reporting, module docstring, and the four `is_subagent` occurrences
   across three functions (the row writer, `split_population`,
   `cmd_subagents`). Depends on Lane B's characterisation slice.
3. Lane B second slice: rollout fixtures and Codex-path tests (Verification
   2), written against this spec's row contract; file-disjoint from Lane A
   until integration.
4. Lane C: docs per D6.2-5 — README, EVALUATION.md, two skill files, two
   docstring sentences. File-disjoint from Lane A.
5. Lane D: release metadata — version bump to 1.10.0 in the three files that
   carry one (`plugins/p/.claude-plugin/plugin.json`,
   `plugins/p/.codex-plugin/plugin.json`, `.claude-plugin/marketplace.json`;
   the universal marketplace entry carries none) plus
   `plugins/p/tests/test_universal_plugin.py`, which pins the literal twice
   and in a test method name — that test, not `p-validate`, is the agreement
   gate (`p-validate` checks presence and semver only). File-disjoint from
   the other lanes, including Lane B's new test modules.
