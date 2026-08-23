"""Validate and render evidence-backed proposal candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from .catalog import ensure_rubric_use, load_rubric_catalogue
from .proposals import Proposal, load_proposal_policy, proposal_review


@dataclass(frozen=True)
class ResolvedEvidence:
    refs: frozenset[str]
    claims: dict[str, object]


def _proposal(raw):
    values = dict(raw)
    for field_name in ("evidence_refs", "evidence_rubric_ids", "dependencies", "risks"):
        if field_name in values:
            values[field_name] = tuple(values[field_name])
    return Proposal(**values)


def _json_pointer(payload, pointer: str):
    if pointer == "":
        return payload
    if not pointer.startswith("/"):
        raise ValueError("evidence JSON pointer must be absolute")
    current = payload
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(current, list):
                if not token.isdigit():
                    raise ValueError
                current = current[int(token)]
            else:
                current = current[token]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("evidence JSON pointer cannot be resolved") from exc
    return current


def load_evidence(trace_path: Path | None = None,
                  index_path: Path | None = None):
    refs = set()
    claims = {}
    if trace_path is not None:
        try:
            with trace_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    refs.update(str(row[key]) for key in ("trace_id", "span_id")
                                if row.get(key))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise ValueError("invalid trace evidence index") from exc
    if index_path is not None:
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != 1:
                raise ValueError
            refs.update(str(value) for value in payload.get("evidence_refs") or ())
            for item in payload.get("evidence") or ():
                ref = str(item["ref"])
                artifact = (index_path.parent / str(item["artifact"])).resolve()
                expected = str(item["sha256"])
                actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
                if actual != expected:
                    raise ValueError("evidence artifact fingerprint mismatch")
                if ref in refs:
                    raise ValueError("duplicate evidence reference")
                refs.add(ref)
                if "json_pointer" in item:
                    try:
                        artifact_payload = json.loads(artifact.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as exc:
                        raise ValueError("evidence claim artifact is not JSON") from exc
                    claims[ref] = _json_pointer(
                        artifact_payload, str(item["json_pointer"]))
        except (OSError, KeyError, json.JSONDecodeError, TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and "fingerprint mismatch" in str(exc):
                raise
            raise ValueError("invalid named evidence index") from exc
    return ResolvedEvidence(frozenset(refs), claims)


def load_evidence_refs(trace_path: Path | None = None,
                       index_path: Path | None = None):
    return set(load_evidence(trace_path, index_path).refs)


def _same_claim(expected, actual):
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(expected), float(actual), rel_tol=1e-12, abs_tol=1e-12)
    return expected == actual


def _validate_evidence_bindings(proposals, resolved: ResolvedEvidence):
    for proposal in proposals:
        binding = proposal.evidence_binding
        if proposal.observed_rate is not None and not binding:
            raise ValueError("observed proposal lacks evidence binding")
        if not binding:
            continue
        if not isinstance(binding, dict):
            raise ValueError("proposal evidence binding is invalid")
        fields = binding.get("fields") or {}
        if not isinstance(fields, dict):
            raise ValueError("proposal evidence binding fields are invalid")
        if proposal.observed_rate is not None \
                and not {"population", "observed_rate"}.issubset(fields):
            raise ValueError("observed proposal binding is incomplete")
        for proposal_field, source in fields.items():
            if not hasattr(proposal, proposal_field):
                raise ValueError("proposal evidence binding names an unknown field")
            if not isinstance(source, dict):
                raise ValueError("proposal evidence binding source is invalid")
            ref = str(source.get("ref") or "")
            pointer = str(source.get("pointer") or "")
            if ref not in proposal.evidence_refs or ref not in resolved.claims:
                raise ValueError("proposal evidence binding cannot be resolved")
            actual = _json_pointer(resolved.claims[ref], pointer)
            if not _same_claim(getattr(proposal, proposal_field), actual):
                raise ValueError("proposal evidence claim mismatch: %s" % proposal_field)


def _apply_rubric_use_gates(proposals, rubrics_path: Path) -> None:
    catalogue = load_rubric_catalogue(rubrics_path)
    by_id = {rubric.id: rubric for rubric in catalogue.rubrics}
    for proposal in proposals:
        label_evidence = any(ref.startswith("labels:")
                             for ref in proposal.evidence_refs)
        if label_evidence and not proposal.evidence_rubric_ids:
            raise ValueError("label evidence requires rubric provenance")
        for rubric_id in proposal.evidence_rubric_ids:
            if rubric_id not in by_id:
                raise ValueError("proposal evidence rubric is absent")
            use = ("scorer_validation"
                   if proposal.target_kind == "scorer"
                   and proposal.target_ref == rubric_id
                   else "decision_support")
            try:
                ensure_rubric_use(by_id[rubric_id], use)
            except ValueError:
                proposal.policy_suppression_reason = (
                    "candidate-sampler-only rubric cannot support %s" % use)
                break


def build_proposal_review(candidate_path: Path, policy_path: Path | None = None,
                          known_evidence_refs=None, resolved_evidence=None,
                          rubrics_path: Path | None = None):
    try:
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid proposal candidates") from exc
    proposals = [_proposal(raw) for raw in payload.get("proposals") or ()]
    _apply_rubric_use_gates(
        proposals, rubrics_path or Path(__file__).resolve().parents[1] /
        "rubrics" / "rubrics.json")
    if resolved_evidence is not None:
        known_evidence_refs = resolved_evidence.refs
    if known_evidence_refs is not None:
        unresolved = {
            ref for proposal in proposals for ref in proposal.evidence_refs
            if ref not in known_evidence_refs
        }
        if unresolved:
            raise ValueError(
                "unresolved proposal evidence: %d reference(s)" % len(unresolved))
    if resolved_evidence is not None:
        _validate_evidence_bindings(proposals, resolved_evidence)
    review = proposal_review(proposals, load_proposal_policy(policy_path))
    lines = [
        "# Ranked proposals", "",
        "Recommendation: review the ranked experiments; no proposal changes state.", "",
        "Ask: approve, reject, or revise proposals by their stable proposal ID.", "",
        "| Rank | Proposal | Target | Population | Confidence | Avoidable events |",
        "|---:|---|---|---:|---:|---:|",
    ]
    for item in review["ranked"]:
        lines.append("| {rank} | {proposal_id} | {target_kind}: `{target_ref}` | "
                     "{population} | {confidence:.2f} | {avoidable_cost:g} |".format(**item))
    for item in review["ranked"]:
        observed = ("not_observable" if item["observed_rate"] is None
                    else "%.4f" % item["observed_rate"])
        lines += [
            "", "### {rank}. {proposal_id}".format(**item), "",
            "| Field | Evidence-backed value |", "|---|---|",
            "| Population | {population} {population_unit}(s); "
            "{session_count} independent session(s) |".format(**item),
            "| Independent evidence | {independent_evidence_count} "
            "{evidence_unit}(s) |".format(**item),
            "| Evidence | {0} |".format(", ".join(item["evidence_refs"])),
            "| Evidence rubrics | {0} |".format(
                ", ".join(item["evidence_rubric_ids"]) or "not_applicable"),
            "| Evidence binding | {0} |".format(", ".join(sorted({
                str(source.get("ref"))
                for source in item["evidence_binding"].get("fields", {}).values()
                if isinstance(source, dict) and source.get("ref")
            })) or "not_applicable"),
            "| Observed rate | %s |" % observed,
            "| Uncertainty | {uncertainty} |".format(**item),
            "| Comparison | {comparison} |".format(**item),
            "| Effect size | {effect_size} |".format(**item),
            "| Expected impact | {expected_impact} |".format(**item),
            "| Exact change | {exact_change} |".format(**item),
            "| Experiment | {experiment} |".format(**item),
            "| Success threshold | {success_threshold} |".format(**item),
            "| Rollback | {rollback} |".format(**item),
            "| Dependencies | {0} |".format(", ".join(item["dependencies"])),
            "| Risks | {0} |".format(", ".join(item["risks"])),
        ]
    if review["suppressed"]:
        lines += ["", "## Suppressed candidates", "",
                  "| Proposal | Reason |", "|---|---|"]
        for item in review["suppressed"]:
            lines.append("| {proposal_id} | {suppression_reason} |".format(**item))
    if review["resolved"]:
        lines += ["", "## Resolved proposals", "",
                  "| Proposal | Status | Exact change |", "|---|---|---|"]
        for item in review["resolved"]:
            lines.append("| {proposal_id} | {status} | {exact_change} |".format(**item))
    lines += ["", "No proposal is auto-applied.", ""]
    return review, "\n".join(lines)


def _inside_repository(path: Path):
    resolved = path.resolve()
    return any((parent / ".git").exists() for parent in (resolved, *resolved.parents))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    parser.add_argument("--trace-evidence", type=Path)
    parser.add_argument("--evidence-index", type=Path)
    parser.add_argument(
        "--rubrics", type=Path,
        default=Path(__file__).resolve().parents[1] / "rubrics" / "rubrics.json")
    args = parser.parse_args(argv)
    if _inside_repository(args.json_output.parent) or _inside_repository(args.markdown_output.parent):
        raise ValueError("proposal reviews must remain outside repositories")
    resolved_evidence = None
    if args.trace_evidence is not None or args.evidence_index is not None:
        resolved_evidence = load_evidence(
            args.trace_evidence, args.evidence_index)
    review, markdown = build_proposal_review(
        args.candidates, args.policy, resolved_evidence=resolved_evidence,
        rubrics_path=args.rubrics)
    for path in (args.json_output, args.markdown_output):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(review, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
    args.markdown_output.write_text(markdown, encoding="utf-8")
    print(json.dumps({"ranked": len(review["ranked"]),
                      "resolved": len(review["resolved"]),
                      "suppressed": len(review["suppressed"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
