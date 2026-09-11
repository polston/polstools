---
name: reviewing-improvement-effects
description: Review whether an implemented workflow proposal helped, using activation provenance, comparable work, quality constraints, and follow-up evidence. Use to register a reversible pilot or decide keep, revise, revert, or insufficient evidence.
---

# Reviewing improvement effects

Before any other action, resolve the plugin root from this `SKILL.md` and run
`<python> <plugin-root>/bin/skill-profile-ctl check reviewing-improvement-effects`.
If it exits 1 or 2, stop and report its output.

Follow one existing proposal into use. Shipping a skill, completing a response,
or recording an experiment does not establish that the work improved. This
workflow produces a review and next action; it does not apply its disposition,
schedule jobs, or grant authority to change another system.

Before reading session history or history-derived evidence, run:

```text
<python> <plugin-root>/bin/skill-profile-ctl check-capability local-session-history --component reviewing-improvement-effects
```

If unavailable, use supplied synthetic or static evidence and state the missing
coverage. Keep real records, evidence indexes, instruction manifests, and reports
outside every repository. Use redacted packets for history; do not paste raw
history into the review.

## Register or recover the pilot

Read the existing proposal and its evidence, experiment, success threshold, and
rollback. Preserve its stable ID; do not create a parallel backlog. A proposal
that exists only in prose may receive an ID when represented in the existing
`retro_eval.proposals.Proposal` format; link the original artifact and section.
Do not manufacture an observed rate to make it rank. Preserve its original
evidence and mark implementation separately from demonstrated benefit.

Copy [the record template](references/record-template.json) to the external
proposal work directory. Use [the record format](references/record-format.md)
for evidence-index entries, cohort metadata, and checker fields. Fill it using
the following rules:

- Bind the proposal through a fingerprinted evidence-index entry whose JSON
  pointer selects that exact proposal object. Record the owner, task family,
  intended benefit, quality constraints, review trigger, and rollback.
- `activation` identifies the version actually used and a fingerprinted
  activation observation. For instructions or skills, create and bind an
  external instruction manifest with `bin/retro-eval-instructions`; that
  content hash alone does not prove a session loaded it. Preserve the dispatch
  or execution observation too. An implementation commit is not an activation
  date. Leave activation null if it cannot be recovered.
- Preserve baseline and follow-up cohort artifacts. Each describes task
  family, harness and main/child populations, selection rule, time bounds,
  eligible units, exclusions, and observation method. Include ordinary and
  successful work, not just complaints. Unknown values stay null.
- Select `decision_signals` before reviewing follow-up results: which of the
  recorded observations must be available for this particular benefit claim.
  Define what their values and units mean; do not choose signals after seeing
  which improved. Record quality constraints separately so reduced effort
  cannot hide incorrect or unusable work.
- A review trigger is a date, a bounded number of eligible tasks, or a concrete
  regression. A pilot count is a collection trigger, not a significance claim.
  Record who will collect the next observations and the exact comparison.

## Keep observations distinct

The record carries baseline and follow-up values with evidence references for
each signal, including null for missing observations:

| Signal | Evidence needed |
|---|---|
| `response_completion` | Source emitted a terminal response or completion event |
| `verified_delivery` | Recoverable done contract and checks showing it was satisfied |
| `later_use` | Intended consumer actually used the result for its purpose |
| `reopened_work` | Linked continuation reopened the supposedly finished task |
| `parent_rework` | Parent repeated or repaired delegated work; record whether justified |
| `user_interventions` | Explicit linked user corrections or decisions, with their purpose |

These observations are not interchangeable. Completion cannot fill delivery or
use. An unobserved reopening is not zero; define the follow-up horizon. Parent
verification may be required. Fewer user interventions may mean lost user
control rather than improvement. Define any effort measure and its denominator
inside the cohort artifact, alongside quality evidence.

## Check evidence, then review

Use the existing named evidence index described in
`<plugin-root>/EVALUATION.md`: every referenced artifact has a SHA-256 and may
select a JSON claim with `json_pointer`. The checker resolves references using
`retro_eval.proposal_report.load_evidence`, validates the proposal identity,
and enforces the external-only boundary:

```text
<python> <skill-dir>/scripts/check_record.py \
  --record <external-effect-record.json> \
  --evidence-index <external-evidence-index.json>
```

Exit 0 means the record is mechanically valid with no declared prerequisite
missing; exit 1 means it is valid but has evidence gaps or a rubric restriction;
exit 2 means invalid input or an unverifiable link. Fix invalid input. Preserve
honest gaps. The checker does not establish that a claim is true, that cohorts
are comparable, or that a change caused an effect. Inspect the resolved evidence
and record that reasoning in the review. Never relabel a provisional rubric as
direct observation to bypass its allowed uses.

`retro.py effect --since <date>` is a Claude main-session before/after lens.
Use its output to locate questions, not to establish causality. Match task
families, source populations, selection, observation methods, exposure, and
quality. If populations changed, compare supported matched strata or leave the
effect unknown. Do not pool an unmatched trend or a child/main mixture into an
improvement claim. Use controlled comparisons where feasible; describe residual
confounding and uncertainty even when the comparison is useful.

Write a private review beside the record with its fingerprint, checker result,
evidence references, comparison limits, disposition, and concrete next action:

- **Keep:** the prespecified benefit is supported and quality constraints hold
  in the supported comparison. Limit the conclusion to that scope.
- **Revise:** usable evidence identifies a bounded defect in the change or its
  application and supports a specific amendment plus a fresh comparison.
- **Revert:** a quality constraint is violated or supported evidence meets the
  rollback condition. A directly evidenced regression can justify rollback
  without proving a population-wide causal effect. State that narrower basis.
- **Insufficient evidence:** activation, comparability, follow-up, required
  observations, or evidence eligibility is missing. Name exactly what must be
  collected, by whom, and when to review. Do not keep by default because it
  shipped, or revise merely because the measurement is missing.

Update the record's disposition and next action after review and rerun the
checker. Preserve earlier records so changing the hypothesis cannot erase a
failed pilot. No new work needs to wait weeks: registration and an honest
insufficient-evidence review are a complete present-tense deliverable.

## When to return

Use [the cadence guide](references/cadence.md) to choose the next existing
diagnostic. Run only the skill relevant to the observed question; do not turn
each review into a mandatory suite. Adoption, usefulness to the user, and
priority remain explicit unknowns when artifact evidence cannot answer them.
