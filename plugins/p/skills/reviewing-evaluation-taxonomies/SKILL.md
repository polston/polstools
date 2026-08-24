---
name: reviewing-evaluation-taxonomies
description: Use when starting, resuming, or checking the local mixed interpretation and proposal review against private RETRO_HOME evidence.
---

# Reviewing agent interpretations

Use the plugin's `bin/retro-eval-review` as the single entrypoint. Resolve the
plugin root from this skill's location, `PLUGIN_ROOT`, or
`CLAUDE_PLUGIN_ROOT`. Resolve Python by trying `python3`, `python`, `py -3`,
then `uv run --no-project python`.

## Default run

Run `retro-eval-review status` and then `retro-eval-review serve-next`. The
default phase is calibration. The command discovers the current review set
from `RETRO_HOME`; when that variable is absent, it uses the sole discoverable
review set under the local `.retro` directory. It resumes the first case without
an assessment and opens a loopback-only browser workspace.
The default review URI is always `http://127.0.0.1:8123/`. Do not choose a
random port. Use `--port` only when the operator explicitly requests another
stable URI.

The first incomplete mixed interpretation packet comes before taxonomy packets.
Its cards alternate between user-understanding checks and agent judgments. Each
card states the situation, the agent's interpretation, its reason, and the
expected action in plain language; raw source evidence stays collapsed. The
operator chooses Accurate, Partly accurate, Wrong, or Not enough context.
Notes are optional. Never ask the operator to derive or explain the diagnosis.

After mixed calibration is complete, the same entrypoint advances to any
remaining proposal-first taxonomy packets. Those retain their versioned
Correct, Incorrect, and Unsure contract.

## Preserve the evaluation boundary

1. Finish and assess calibration before exposing held-out cases.
2. Inspect calibration disagreements. If they justify a scorer, rubric,
   protocol, or taxonomy change, increment its version and re-derive affected
   evidence before held-out review.
3. Start held-out review only with the explicit `--phase heldout` option after
   calibration decisions are frozen.
4. Run the registered promotion gate over held-out truth and the already-frozen
   proposals. Never tune on held-out labels.
5. If class support is short, create a new adaptive round using prior packet
   manifests. Never regenerate, replace, or implicitly accept an existing
   immutable packet.

Keep packets, labels, notes, reports, identities, and machine paths outside Git
under `RETRO_HOME`. Do not call an external judge unless separately authorized.

## Terminal states

- `Review needed`: the loopback workspace is open or an incomplete packet is
  ready for operator assessment.
- `Continuing`: calibration analysis, versioned re-derivation, or adaptive
  sampling remains actionable without operator judgment.
- `Blocked`: packet fingerprints do not resolve or required source evidence is
  unavailable; report the exact failed check.
- `Complete`: held-out class support and every preregistered promotion threshold
  pass, or the scorer is explicitly retained as unvalidated and barred from
  decision support after the registered maximum rounds.
