# Effect record format v1

The JSON template is intentionally incomplete. Fill required text and keep
missing evidence null. The checker prints a summary to stdout; redirect it only
to an external location. It never writes, changes proposal state, or applies a
disposition. Save the authored review separately and link its checked record
fingerprint.

Use the existing evidence-index schema:

```json
{
  "schema_version": 1,
  "evidence": [
    {"ref": "proposal:pilot", "artifact": "proposals.json", "sha256": "<file-sha256>", "json_pointer": "/proposals/0"},
    {"ref": "cohort:baseline", "artifact": "baseline.json", "sha256": "<file-sha256>", "json_pointer": ""}
  ]
}
```

Artifact paths are relative to the index, or absolute external paths. Every
reference used by the record and bound proposal must be a fingerprinted entry;
bare names in `evidence_refs` are insufficient. `proposal_ref` must select a
complete existing `Proposal` object with the same `proposal_id`. Preserve
proposal evidence, rubric provenance, and unknown rates. See
`retro_eval/proposals.py` for the existing proposal fields.
Declared proposal field bindings must also resolve and agree with the selected
JSON claims; the checker reuses the existing proposal binding validator.

`activation` is null or an object with `version`, `evidence_ref`, and optional
`instruction_manifest_ref`. The evidence records actual use of that version.
When supplied, the manifest reference selects a whole valid instruction
manifest; its fingerprint is checked using the existing manifest loader.

Baseline and follow-up references select JSON cohort objects containing:

- `task_family`: the same family named in the effect record.
- `source_population`: a nonempty description or list of harness/main/child
  strata; use the same representation on both sides.
- `selection_rule` and `observation_method`: exact collection rules, including
  how ordinary and successful work entered the sample.
- `unit`, positive integer `count`, `window`, and an `exclusions` list.
- `activation_version` on follow-up: the version actually used, matching the
  activation record. Include exposure and instruction-manifest evidence when
  applicable; a matching version string alone does not prove exposure.
- The measured facts or links supporting the record's observation values,
  with units, done contracts, follow-up horizon, and comparison limitations.

Different counts or windows are allowed; metadata equality is only a necessary
check. Different source populations, selection, observation methods, or units yield a
gap. To compare a matched subset, create fingerprinted subset artifacts and
explain the exclusions instead of rewriting the original cohorts.

`comparison.status` is `matched`, `unmatched`, or `unknown`; its `basis_ref`
links the comparison's actual method and evidence. Always describe limitations.
Each of the six observation signals has `baseline` and `followup` objects with
a scalar `value` or null and a `refs` list. A known value requires evidence;
the checker verifies its links, while the reviewer checks whether that evidence
supports the value and interpretation. Do not encode unknown as zero or as a
string to evade an evidence gap.

`decision_signals` selects the observations needed for the prespecified claim.
Missing values for those signals are gaps; retain other unknowns without
pretending they were measured. `benefit.status` is `supported`, `not_supported`,
or `unknown`; `quality.status` is `preserved`, `regressed`, or `unknown`. Both
have evidence `refs`. These are authored judgments, not computed scores.

`evidence_rubric_ids` declares all behavioral-label provenance, including labels
inside cohort artifacts. The checker applies current catalogue decision-use
gates to those IDs and the proposal's IDs. It cannot infer undeclared provenance
from arbitrary prose. The reviewer must inspect origin and must not use an
unvalidated label merely because its reference has a different name.

`disposition` is `keep`, `revise`, `revert`, or `insufficient_evidence`.
`next_action` names `owner`, `action`, `prerequisites`, and `acceptance`.
Checker gaps normally support insufficient evidence. A directly observed quality
violation can still support a narrowly explained revert while broader effects
remain unknown. A zero exit establishes neither causal benefit nor permission
to act; it establishes checked bindings and declared prerequisites only.
