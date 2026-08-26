---
name: adequacy-review
description: Blinded, grounded ensemble review that catches reinvention, over-complex choices, subtle specification mismatches, and documentation drift.
---

# Adequacy review

Before any other action, resolve the plugin root and run
`<python> <plugin-root>/bin/skill-profile-ctl check adequacy-review`. If it exits
1 or 2, stop and report its output.

The mechanism is the deterministic workflow at
`<plugin-root>/workflows/adequacy-review.js`. Do not reproduce its recipe in
prose: invoke that workflow so blinding, ensemble size, agreement filtering,
schemas, and distillation stay identical across harnesses.

1. Parse the invocation arguments: the first token or tokens identify a file,
   directory, or Git range; an optional `spec:` value identifies the sealed
   plan or specification.
2. Invoke the workflow with `target`, optional `spec`, and `repo` as `args`.
   If the run reports `REPLACE_WITH_TARGET`, read the workflow and invoke it as
   an inline script with only those input defaults substituted.
3. Report the workflow result's `distilled` field verbatim, followed by the
   reviewer verdicts. Do not editorialize or re-rank the result.

The optional context-mode plugin lets reviewers execute small traces in an
isolated sandbox. Without it, reviewers fall back to reason-by-reading.
