# Portable adequacy review specification

Date: 2026-08-25. This document is the sealed requirements-only reference for
reviewing the target change.

## Outcome

Claude Code and Codex execute equivalent blinded ensemble review from one
plugin-owned contract while using their native subagent transports.

## Requirements

1. `contract-v1.json` is the single versioned source for inputs, defaults,
   prompts, schemas, agreement, ranking, caps, and rendered-output policy.
2. A standard-library Python helper validates the complete supported contract,
   renders canonical reviewer and semantic-distiller prompts, validates every
   returned finding reference exactly once, and deterministically distills the
   result.
3. Claude Code and Codex adapters contain only native spawn, wait, model,
   result-transfer, semantic-distiller, and temporary-file mechanics. They do
   not restate review policy.
4. The default run uses four cold reviewers. One explicit reviewer count binds
   packet rendering, result collection, semantic distillation, and final
   distillation; a partial or excess result set is rejected.
5. Reviewers receive no conversation history or author rationale. The target
   itself and repository grounding exclude evolving plans, progress, prior
   reviews, and remediation history through explicit path exclusions.
6. Every reviewer must disclose unchecked runtime, integration, or
   specification behavior, including an explicit empty array when none exists.
7. Semantic clustering matches root-cause-equivalent paraphrases without
   treating reviewer-authored keys as authoritative. Every finding belongs to
   exactly one cluster.
8. Two reviewers establish ensemble agreement. Stable findings are ranked by
   critical, important, then suggestion; the visible stable list is capped at
   five and any omitted count is visible in the rendered output.
9. Contested findings, unchecked behavior, and reviewer verdicts remain visible.
   A clean line appears only when no important or critical stable finding exists.
10. Rendered headings, finding lines, empty-section text, clean-blocking
    severities, and section order are versioned contract policy, not helper
    literals. Required format placeholders are validated before rendering.
11. The helper never executes reviewed code. A reviewer may run a small trace
   only in an isolated harness sandbox and otherwise discloses the unchecked
   behavior.
12. The legacy Claude-only workflow is removed after equivalent portable
    behavior and package-drift checks pass.
13. Source, Claude-installed, and Codex-installed package validation agree, and
    plugin metadata remains synchronized at version 1.9.0.

## Acceptance evidence

1. Focused tests demonstrate equivalent canonical packets and deterministic
   results for both adapters, semantic paraphrase handling, multibyte safety,
   mandatory disclosure, visible cap truncation, and contract-drift rejection.
2. The full unit suite, format checks, package validation, privacy audit, and
   Git diff hygiene pass from the isolated task branch.
3. A cold Codex-path adequacy review reports no unresolved ensemble-stable
   important or critical defect.
4. The branch is committed and clean before local integration is offered.

## Protected scope

Preserve skill activation, Claude command compatibility, dependency-free
runtime, privacy protections, and unrelated plugin behavior. Do not push,
publish, tag, release, install live, add dependencies, weaken tests, execute
reviewed code on the host, or include private or identifying project data.
