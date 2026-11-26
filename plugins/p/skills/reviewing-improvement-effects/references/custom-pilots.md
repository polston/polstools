# Custom candidate pilots

Use this path when inspection supports a concrete comparison that the installed
`pilot prepare` replay does not answer. It records a plan and an attributed review
using the existing pilot, analysis and decision record types. It does not run the
intervention, confer execution authority, schedule follow-ups, or calculate an
efficacy verdict. Use a direct repair or no-change disposition when a pilot would
add no useful evidence.

All paths below are explicitly supplied files outside repositories. `<python>`
is `sh <plugin-root>/bin/python-launcher`. `--store` precedes the subcommand.

## Bind the finding and the plan

1. Run `retro-eval-workflow inspect --run RUN --candidate KEY`. Read the packet
   at `inspection_path`, including contrary context and current applicability.
   Keep the returned `artifact_ref`. Each candidate entry includes `case_refs`;
   these are inferred source fragments, not automatically independent tasks.
2. Copy `pilot-registration-template.json` to the private study directory. Set
   `inspection_ref` to that artifact reference and `candidate_key` to the selected
   key. Fill `justification`: the supported interpretation, rationale, supported
   scope, possible harm, and contrary evidence or the limits of its search. Set
   `reviewer_type` to `agent` or `human`; do not attribute an agent judgment to a
   human. Preserve the candidate's evidence selectors in `evidence_refs`.
3. Capture the actual comparator and intervention specifications or code:

   ```text
   <python> <plugin-root>/bin/retro-eval-workflow --store STORE evidence FILE --sha256 SHA256
   ```

   For code in a repository, copy the exact bytes to the private study directory
   and verify the copy's hash against the original before capture. Calculate
   SHA256 from the exact file bytes first. Capture checks that digest,
   reads at most 8 MiB by default (`--max-bytes` overrides), and publishes a
   `private_raw` artifact. It never executes the file. Put its `artifact_ref` in
   the relevant component's `evidence_refs`, with a concrete `description`.
   For a structured claim, add an existing supported `selector` to the reference.
4. Fill the pilot contract before grading. In particular:

   - Preserve `proposal_identity` across revisions of the same proposal. Specify
     owner, scope, eligibility, comparator, intervention, rollback and isolation.
   - List every assigned unit as `{"unit":"patch-a","case_ref":REF,
     "split_group":"GROUP"}`. Use the actual existing case reference and its
     group; `planned_sample` equals the number of units and `split_groups` equals
     their groups. Further explicit cases can use the existing manifest API.
   - Define the assignment mechanism, independence unit, strata, primary measure
     and estimand, direction (`lower` or `higher`), practical threshold and its
     basis. A numerical threshold does not turn a qualitative judgment into a
     measurement. Record quality guardrails as distinct nonempty strings.
   - Freeze `analysis_method` and `analysis_version`, missingness policy,
     activation procedure/evidence, carryover and concurrent changes, resource
     budget, checkpoints, follow-up horizon and truth policy. The budget and
     checkpoints are declared bounds; this recording path does not enforce
     external execution. `registration_before_grading` is a declaration, not
     proof that no prior results were seen.

   Discovery groups from the inspection are retained automatically. Replaying
   those units is useful for regression checking but is not fresh confirmation.
   The generated `code_digest` binds both declared component descriptions and
   evidence references; it does not prove those components executed.
5. Register:

   ```text
   <python> <plugin-root>/bin/retro-eval-workflow --store STORE pilot register --spec REGISTRATION.json
   ```

   Registration verifies the candidate against its completed source operation,
   resolves evidence and checks current dependencies. It rejects substituted
   candidate text, missing selectors, stale cases and invented assignment groups.
   The original hypothesis, inspection, source operation, justification and plan
   are retained together. Identical registration returns the same pilot reference;
   changed plans get new immutable pilot identities. Keep the returned `pilot_ref`.

## Perform and review the comparison

The authorized agent performs the registered comparison with native tools under
its existing authority. Preserve input identities, execution and activation
observations, outcomes, errors, omitted work and guardrail checks. Capture each
relevant evidence artifact using `evidence`; registration and code/configuration
presence alone cannot establish activation.

Copy `pilot-review-template.json` to a private file and fill it:

- Set `pilot_ref` and `expected_revision` (0 for the first decision; thereafter
  the latest decision revision from `status`). The method and method version
  must equal registration. A changed method requires a revised registration.
  `code_version` identifies the actual analysis implementation. Preserve estimates,
  exclusions, limitations and evidence; optional typed record references can link
  existing exposure and outcome records. The analysis automatically binds the
  registered pilot, composition, scope and assigned cases.
- Account for each assigned unit exactly once in `analysis.results`:
  `{"unit":"patch-a","status":"pending","reason":"Follow-up not yet
  observed.","evidence_refs":[]}`. States are `observed`, `unknown`, `pending`
  or `excluded`. Observed rows require resolvable evidence; missing/excluded rows
  require an explanation and stay in the denominator. This boundary checks the
  accounting, not the arithmetic of an arbitrary estimator.
- Set attributed `activation`, `comparison` and `benefit` judgments with status,
  reason and evidence references. Activation is `observed`, `not_observed` or
  `unknown`; comparison is `matched`, `unmatched` or `unknown`; benefit is
  `supported`, `not_supported` or `unknown`. Known judgments need evidence.
  Supported benefit requires observed activation, matched comparison and at least
  one observed outcome. The reviewer must evaluate missingness, relevance to the
  prespecified threshold and comparison validity; those are not established by
  merely populating fields.
- List every registered guardrail, in registration order, in `analysis.guardrails`:
  `{"guardrail":"No harmful fix.","status":"unknown","reason":"Not yet
  checked.","evidence_refs":[]}`. Known `preserved` or `regressed` judgments need
  evidence. Any regression determines the decision's quality status; otherwise
  quality is preserved only when every guardrail is preserved.
- Fill the decision's disposition (`keep`, `revise`, `revert` or
  `insufficient_evidence`), benefit and quality statuses, rationale, evidence,
  contrary evidence and concrete next action with owner, prerequisites and
  acceptance. Benefit must match the analysis; keep requires supported benefit
  and preserved quality. A directly evidenced regression can justify revert even
  when benefit and other outcomes are unknown. A missing comparison can be
  recorded now as insufficient evidence. `due_at` optionally specifies a
  timezone-aware review date; it schedules no job.

```text
<python> <plugin-root>/bin/retro-eval-workflow --store STORE pilot review --spec REVIEW.json
<python> <plugin-root>/bin/retro-eval-workflow --store STORE status
```

Review publishes the analysis and decision atomically. Exact replay is idempotent;
changed decisions require the current revision and preserve earlier reviews.
Submitting a custom review conservatively marks all assigned source families as
discovery exposure for the installed replay, including pending or unknown units.
This prevents a reviewed family from later being treated as fresh blind material;
registration alone does not exclude a family. The exposure survives export/import
and later corrections to analysis, selection or assessment records. Corrections
change current applicability; they cannot make previously seen evidence unseen.
`status.pilot_followups` shows registered pilots, latest decisions, due dates,
current applicability and concrete next actions without collecting more history.
These entries do not silently adopt or suppress retrieval candidates. Use the
existing `disposition` command separately for local attention state when warranted.

All custom decisions carry `judgment_basis:
attributed_review_not_automatic_effect_verification`. The CLI checks bindings,
accounting and declared prerequisites; a reviewer still has to inspect the
substance. A keep is an attributed, scoped decision, not automatic proof of
benefit, independence, causality or user preference. No disposition applies a
change. Existing envelope replay commands and v1 effect imports are unchanged.
