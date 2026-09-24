# Antigravity history ingestion

Extend the Retro measurement and evidence-pack pipeline to read Antigravity
CLI conversation exports alongside Claude and Codex. Preserve the Codex PR's
population boundaries and main's restrictions on unvalidated behavioral labels.

## Source and row contract

- Discover `brain/*/.system_generated/logs/` under the CLI data directory,
  defaulting to `~/.gemini/antigravity-cli`. `RETRO_ANTIGRAVITY_HOME` overrides
  that directory for ingestion; it is not advertised as a harness setting.
- Prefer `transcript_full.jsonl`; fall back to `transcript.jsonl`. Do not read
  both exports, prompt-history files, or conversation databases as transcripts.
  A full export appearing later replaces the earlier shortened-export row.
- Recognize structured steps using `step_index`, `type`, and `source`. Ignore
  malformed lines and non-record JSON. For repeated indices, the latest
  snapshot wins. An unreadable selected file is reported and retried.
- Count `USER_INPUT` from `USER_EXPLICIT` as prompts and `PLANNER_RESPONSE`
  from `MODEL` as assistant steps. `tool_calls` contains named calls with
  `args`. Tool output, system records, and thinking do not become user or
  assistant prose. Repeat calls mean identical name and argument signature;
  they are not necessarily wasteful and never affect ranking.
- Classify user reply candidates with the same provisional helpers as other
  sources, preserving the rubric gate. Quotes include preceding assistant
  prose and pass through the existing redactor before leaving the reader.
- The export provides no authoritative parent/main classification. Use
  `population: unknown` and `population_source: not_observable`. Sample
  Antigravity moments from these rows without adding them to main rates.
- Token accounting, interrupts, tool-error markers, permission-mode changes,
  queued prompts and skill attribution remain ineligible. Zero placeholders
  are storage compatibility, not evidence of absence. Project identity is
  unavailable; do not infer it from tool paths or message content.
- Keep schema 7: existing source definitions are unchanged, and per-row
  ineligibility already expresses unavailable fields. New extraction discovers
  the added root even when existing rows remain incrementally cached.

## Evidence

Synthetic regression tests cover source detection, transcript preference and
incremental replacement, partial lines, step snapshots, exact supported
counters, unavailable accounting, population exclusion, moment context,
missing roots, unreadable files, and skill-report disclosure. Existing Codex
and Claude tests must continue to pass.

Run the repository's documented unit suite, `format-e2e`, `p-validate`, privacy
audit, and `git diff --check`. Also run `stopped-promises.py --selftest` because
the PR touches its coverage documentation. Validate extraction and pack
creation against available local corpora with all generated evidence outside
Git; do not copy real records, identifiers, counts, or quotes into fixtures or
this document. Keep tests synthetic and stdlib-only.

## Boundaries

This change does not add an Antigravity evaluation adapter, estimate missing
usage, claim candidate labels are validated, or modify retention settings.
Reports group by session start date; live sessions remain snapshots. A pack
is evidence for review, not proof that the user's goal was achieved.
