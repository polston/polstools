---
name: adequacy-review
description: Blinded, grounded ensemble review that catches reinvention, over-complex choices, subtle specification mismatches, and documentation drift.
---

# Adequacy review

Before any other action, resolve the plugin root and run
`<python> <plugin-root>/bin/skill-profile-ctl check adequacy-review`. If it exits
1 or 2, stop and report its output.

The canonical policy is `<skill-root>/contract-v1.json`; the deterministic
renderer and distiller is `<skill-root>/scripts/adequacy_review.py`. Do not
restate either in an adapter or improvise their behavior.

1. Parse the invocation arguments: the first token or tokens identify a file,
   directory, or Git range; an optional `spec:` value identifies the sealed
   requirements-only specification; zero or more `exclude:` values identify
   author-only context. Exclude author rationale, progress, prior reviews, and
   remediation history from the target itself and from repository grounding.
   For a Git range, append matching pathspec exclusions before packet rendering.
   Never rely only on keeping the sealed spec clean.
2. Identify the active harness from session context. Read exactly one adapter:
   - Claude Code: `references/claude-code.md`
   - Codex: `references/codex.md`
3. Follow that adapter with the filtered `target`, optional `spec`, inferred
   repository root, every exclusion, and optional reviewer count. The skill
   explicitly authorizes the adapter's native parallel subagent calls.
4. Report the helper result's `distilled` field verbatim. It already contains
   reviewer verdicts. Do not editorialize or re-rank the result.

The helper never executes reviewed code. Reviewers may execute a small trace
only through an isolated sandbox exposed by the active harness; otherwise they
must reason by reading and disclose the unchecked behavior.
