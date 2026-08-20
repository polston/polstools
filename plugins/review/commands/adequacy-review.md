---
description: Blinded, grounded, ensemble review that catches rot a surface read hides — reinvention, dumb/over-complex choices, subtle spec-mismatch bugs, doc drift. Backed by a deterministic workflow; distills to a few ensemble-stable findings + a contested bucket.
argument-hint: "<target: file | dir | git-range>  [spec: path to the sealed plan/spec, optional]"
---

# Adequacy review

The mechanism is a **deterministic workflow** at
`${CLAUDE_PLUGIN_ROOT}/workflows/adequacy-review.js` (blinded · grounded · ensemble ·
distilled). Do NOT re-implement the recipe here — **invoke the workflow**, so the blinding,
ensemble size, >=2-agreement filter, schemas, and distill run identically every time regardless
of which model reads this.

1. Parse `$ARGUMENTS`: the first token(s) = the target (a file, a dir, or a git range like
   `main...HEAD`); an optional `spec:` = path to the sealed plan/spec.
2. Run the workflow with `target` (and `spec`, `repo` when known) as inputs. Pass them as `args`
   first. If they do not reach the script — the run reports the target as
   `REPLACE_WITH_TARGET` — fall back to reading the script and running it as an inline
   `script` with those input defaults at the top substituted. Change nothing else either way.
3. Run it via the Workflow tool.
4. Report the result's `distilled` field verbatim, plus the reviewer verdicts. Do not editorialize
   or re-rank — the workflow already did the ensemble filtering and distillation.

## Requires

- **context-mode plugin** — *recommended.* Reviewers run any test traces in its sandbox
  (`ctx_execute`), so code executes isolated, never on the host. Without it they fall back to
  reason-by-reading (still works; just no execution check).
- **Recommended posture: deny host `node -e`/`-p`.** Reviewers are told to use the sandbox, but
  that is an instruction — denying inline host node makes it *structural*: a misbehaving reviewer
  that reaches for host node is blocked instead of running arbitrary code. `node <file>` and
  `node --version` stay fine.
