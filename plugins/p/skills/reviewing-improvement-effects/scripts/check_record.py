#!/usr/bin/env python3
"""Check private effect-record bindings; do not infer effects or apply changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.catalog import ensure_rubric_use, load_rubric_catalogue  # noqa: E402
from retro_eval.instruction_manifest import load_instruction_manifest  # noqa: E402
from retro_eval.proposal_report import (  # noqa: E402
    _inside_repository, _proposal, _validate_evidence_bindings, load_evidence,
)


SIGNALS = frozenset({"response_completion", "verified_delivery", "later_use",
                     "reopened_work", "parent_rework", "user_interventions"})
DISPOSITIONS = {"keep", "revise", "revert", "insufficient_evidence"}


class FieldError(ValueError):
    """A rejection that names the record or index field it concerns, as a
    dotted path such as next_action.owner or evidence[0].artifact."""

    def __init__(self, field, message):
        super().__init__("%s: %s" % (field, message))
        self.field = field


def _external(path):
    if _inside_repository(Path(path)):
        raise ValueError("effect records and evidence must remain outside repositories")


def _object(value, field):
    if not isinstance(value, dict):
        raise FieldError(field, "expected an object")
    return value


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise FieldError(field, "required text is missing")
    return value


def _strings(value, field):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip()
                                          for x in value):
        raise FieldError(field, "expected a list of nonempty strings")
    return value


def check_record(record_path: Path, index_path: Path):
    """Return a mechanically checked record summary and declared evidence gaps."""
    _external(record_path)
    _external(index_path)
    record_bytes = record_path.read_bytes()
    record = _object(json.loads(record_bytes), "record")
    index = _object(json.loads(index_path.read_text(encoding="utf-8")), "evidence index")
    # Check every artifact before loading any. A symlink resolves to its target.
    entries = {}
    for position, item in enumerate(index.get("evidence") or []):
        at = "evidence[%d]" % position
        item = _object(item, at)
        ref = _text(item.get("ref"), at + ".ref")
        artifact = (index_path.parent / _text(item.get("artifact"),
                                              at + ".artifact")).resolve()
        _external(artifact)
        if ref in entries:
            raise FieldError(at + ".ref", "duplicate evidence reference")
        entries[ref] = (artifact, item)
    resolved = load_evidence(index_path=index_path)
    used = set()

    def reference(ref, field):
        ref = _text(ref, field)
        if ref not in entries or ref not in resolved.refs:
            raise FieldError(field, "effect evidence requires a fingerprinted "
                                    "artifact reference")
        used.add(ref)
        return resolved.claims.get(ref)

    if record.get("schema_version") != 1:
        raise FieldError("schema_version", "unsupported effect record schema")
    for field in ("proposal_id", "owner", "task_family", "intended_benefit",
                  "review_trigger", "rollback", "rationale"):
        _text(record.get(field), field)
    if not _strings(record.get("quality_constraints"), "quality_constraints"):
        raise FieldError("quality_constraints", "quality constraints are required")
    next_action = _object(record.get("next_action"), "next_action")
    for field in ("owner", "action", "prerequisites", "acceptance"):
        _text(next_action.get(field), "next_action." + field)
    if record.get("disposition") not in DISPOSITIONS:
        raise FieldError("disposition", "invalid effect disposition")
    proposal = _proposal(_object(reference(record.get("proposal_ref"), "proposal_ref"),
                                 "proposal_ref"))
    if proposal.proposal_id != record["proposal_id"]:
        raise FieldError("proposal_id", "proposal identity mismatch")
    for ref in proposal.evidence_refs:
        reference(ref, "proposal_ref")
    _validate_evidence_bindings([proposal], resolved)

    gaps = []
    activation = record.get("activation")
    if activation is None:
        gaps.append("activation_unknown")
    else:
        activation = _object(activation, "activation")
        _text(activation.get("version"), "activation.version")
        reference(activation.get("evidence_ref"), "activation.evidence_ref")
        manifest_ref = activation.get("instruction_manifest_ref")
        if manifest_ref is not None:
            reference(manifest_ref, "activation.instruction_manifest_ref")
            path, entry = entries[manifest_ref]
            if entry.get("json_pointer", "") != "":
                raise FieldError("activation.instruction_manifest_ref",
                                 "instruction manifest reference must select "
                                 "the whole artifact")
            load_instruction_manifest(path)

    cohorts = {}
    for phase in ("baseline", "followup"):
        ref = record.get(phase + "_ref")
        if ref is None:
            gaps.append(phase + "_missing")
        else:
            cohorts[phase] = reference(ref, phase + "_ref")
    comparison = _object(record.get("comparison"), "comparison")
    if comparison.get("status") not in {"matched", "unmatched", "unknown"}:
        raise FieldError("comparison.status", "invalid comparison status")
    _text(comparison.get("limitations"), "comparison.limitations")
    if comparison.get("basis_ref") is not None:
        reference(comparison["basis_ref"], "comparison.basis_ref")
    if comparison["status"] != "matched" or comparison.get("basis_ref") is None:
        gaps.append("comparison_not_established")
    if len(cohorts) == 2:
        # Metadata equality is necessary, not proof of comparability or causality.
        fields = ("task_family", "source_population", "selection_rule",
                  "observation_method", "unit")
        if any(not isinstance(value, dict) for value in cohorts.values()):
            gaps.append("cohort_metadata_missing")
        elif any(not cohorts["baseline"].get(field)
                 or cohorts["baseline"].get(field) != cohorts["followup"].get(field)
                 for field in fields):
            gaps.append("cohort_metadata_not_matched")
        elif cohorts["baseline"]["task_family"] != record["task_family"]:
            gaps.append("task_family_mismatch")
    for phase, cohort in cohorts.items():
        if not isinstance(cohort, dict):
            continue
        if (not isinstance(cohort.get("count"), int)
                or isinstance(cohort.get("count"), bool) or cohort["count"] <= 0
                or not cohort.get("unit") or not cohort.get("window")
                or not isinstance(cohort.get("exclusions"), list)):
            gaps.append(phase + "_population_or_bounds_missing")
    if (activation is not None and isinstance(cohorts.get("followup"), dict)
            and cohorts["followup"].get("activation_version") != activation["version"]):
        gaps.append("followup_activation_not_bound")

    decision_signals = _strings(record.get("decision_signals"), "decision_signals")
    if not decision_signals or not set(decision_signals).issubset(SIGNALS):
        raise FieldError("decision_signals",
                         "decision signals must select known observations")
    observations = _object(record.get("observations"), "observations")
    if set(observations) != SIGNALS:
        raise FieldError("observations",
                         "record must retain every distinct observation signal")
    for signal, phases in observations.items():
        for phase in ("baseline", "followup"):
            at = "observations.%s.%s" % (signal, phase)
            observation = _object(_object(phases, "observations." + signal).get(phase), at)
            if "value" not in observation:
                raise FieldError(at + ".value",
                                 "observation value must be explicit, including null")
            value = observation["value"]
            if (value is not None and not isinstance(value, (str, int, float, bool))) \
                    or (isinstance(value, float) and not math.isfinite(value)):
                raise FieldError(at + ".value",
                                 "observation value must be a finite scalar or null")
            refs = _strings(observation.get("refs"), at + ".refs")
            for ref in refs:
                reference(ref, at + ".refs")
            if value is not None and not refs:
                raise FieldError(at + ".refs",
                                 "known observations require evidence references")
            if value is None and signal in decision_signals:
                gaps.append("%s_%s_unknown" % (phase, signal))
    for field, states in (("benefit", {"supported", "not_supported", "unknown"}),
                          ("quality", {"preserved", "regressed", "unknown"})):
        judgment = _object(record.get(field), field)
        if judgment.get("status") not in states:
            raise FieldError(field + ".status", "invalid benefit or quality status")
        refs = _strings(judgment.get("refs"), field + ".refs")
        for ref in refs:
            reference(ref, field + ".refs")
        if judgment["status"] == "unknown":
            gaps.append(field + "_unknown")
        elif not refs:
            raise FieldError(field + ".refs",
                             "known judgments require evidence references")

    rubric_ids = set(_strings(record.get("evidence_rubric_ids"), "evidence_rubric_ids"))
    rubric_ids.update(proposal.evidence_rubric_ids)
    if any(ref.startswith("labels:") for ref in used) and not rubric_ids:
        raise FieldError("evidence_rubric_ids", "label evidence requires rubric provenance")
    catalogue = load_rubric_catalogue(PLUGIN_ROOT / "rubrics" / "rubrics.json")
    by_id = {rubric.id: rubric for rubric in catalogue.rubrics}
    for rubric_id in sorted(rubric_ids):
        if rubric_id not in by_id:
            raise FieldError("evidence_rubric_ids", "effect evidence rubric is absent")
        try:
            ensure_rubric_use(by_id[rubric_id], "decision_support")
        except ValueError:
            gaps.append("rubric_not_decision_eligible:" + rubric_id)
    if record["disposition"] == "keep" and (
            record["benefit"]["status"] != "supported"
            or record["quality"]["status"] != "preserved"):
        gaps.append("keep_conflicts_with_benefit_or_quality")
    return {
        "schema_version": 1, "proposal_id": proposal.proposal_id,
        "record_sha256": hashlib.sha256(record_bytes).hexdigest(),
        "evidence_refs_checked": sorted(used), "gaps": sorted(set(gaps)),
        "declared_prerequisites_present": not gaps, "disposition": record["disposition"],
        "scope": "bindings and declared prerequisites only; judgments require review",
        "auto_apply": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--evidence-index", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = check_record(args.record, args.evidence_index)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        message = ("unreadable effect record or evidence" if isinstance(exc, OSError)
                   else "invalid effect input" if not isinstance(exc, ValueError)
                   else str(exc))
        failure = {"status": "cannot_run", "reason": message}
        if isinstance(exc, FieldError):
            failure["field"] = exc.field
        print(json.dumps(failure), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["gaps"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
