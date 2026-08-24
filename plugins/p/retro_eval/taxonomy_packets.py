"""Adaptive external annotation packets for tool taxonomies."""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .annotation import FIELDS, _external, _packet_fingerprint, _salt
from .catalog import (load_annotation_protocol_catalogue,
                      load_rubric_catalogue)
from .schema import SpanKind
from .storage import JsonlTraceStore
from .taxonomies import (classify_failure_evidence, load_tool_taxonomy,
                         repeated_call_candidates)


FAILURE_SAMPLING_HINT_VERSION = 1
TAXONOMY_FIELDS = FIELDS[:-2] + (
    "proposed_label", "proposal_reason", "assessment") + FIELDS[-2:]
ANNOTATION_PACKET_VERSION = 6


@dataclass(frozen=True)
class AdaptiveSamplingPlan:
    initial_calibration: int
    initial_heldout: int
    minimum_heldout_per_label: int
    support_labels: tuple[str, ...]
    maximum_rounds: int
    maximum_total: int
    maximum_unknown_rate: float | None = None

    def __post_init__(self):
        counts = (self.initial_calibration, self.initial_heldout,
                  self.minimum_heldout_per_label, self.maximum_rounds,
                  self.maximum_total)
        if any(value < 1 for value in counts):
            raise ValueError("adaptive sampling counts must be positive")
        if not self.support_labels:
            raise ValueError("adaptive sampling requires support labels")
        if (self.maximum_unknown_rate is not None
                and not 0 <= self.maximum_unknown_rate <= 1):
            raise ValueError("maximum unknown rate must be between zero and one")

    @classmethod
    def from_rubric(cls, rubric):
        try:
            raw = rubric.extensions["adaptive_sampling"]
            return cls(
                initial_calibration=int(raw["initial_calibration"]),
                initial_heldout=int(raw["initial_heldout"]),
                minimum_heldout_per_label=int(raw["minimum_heldout_per_label"]),
                support_labels=tuple(str(item) for item in raw["support_labels"]),
                maximum_rounds=int(raw["maximum_rounds"]),
                maximum_total=int(raw["maximum_total"]),
                maximum_unknown_rate=(
                    float(raw["maximum_unknown_rate"])
                    if "maximum_unknown_rate" in raw else None),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid adaptive sampling plan") from exc

    def to_dict(self, *, round_number):
        result = {
            "round": round_number,
            "initial_calibration": self.initial_calibration,
            "initial_heldout": self.initial_heldout,
            "minimum_heldout_per_label": self.minimum_heldout_per_label,
            "support_labels": list(self.support_labels),
            "maximum_rounds": self.maximum_rounds,
            "maximum_total": self.maximum_total,
        }
        if self.maximum_unknown_rate is not None:
            result["maximum_unknown_rate"] = self.maximum_unknown_rate
        return result


def assess_label_support(labels, plan: AdaptiveSamplingPlan, *, round_number):
    counts = Counter(str(label) for label in labels)
    gaps = {label: max(0, plan.minimum_heldout_per_label - counts[label])
            for label in plan.support_labels}
    gaps = {label: gap for label, gap in gaps.items() if gap}
    unknown_rate = ((counts["unknown"] / sum(counts.values()))
                    if counts and plan.maximum_unknown_rate is not None else None)
    unknown_exceeded = (unknown_rate is not None
                        and unknown_rate > plan.maximum_unknown_rate)
    if not gaps and not unknown_exceeded:
        status = "ready"
    elif round_number >= plan.maximum_rounds:
        status = "insufficient_evidence"
    else:
        status = "needs_more"
    result = {"status": status, "counts": dict(sorted(counts.items())),
              "support_gaps": gaps, "round": round_number}
    if unknown_rate is not None:
        result["unknown_rate"] = unknown_rate
        result["maximum_unknown_rate"] = plan.maximum_unknown_rate
    return result


def assess_taxonomy_promotion(truth, predicted, rubric):
    """Apply the preregistered held-out gate without treating absence as proof."""
    truth = tuple(str(label) for label in truth)
    predicted = tuple(str(label) for label in predicted)
    if len(truth) != len(predicted):
        raise ValueError("truth and predicted labels must have equal length")
    invalid = sorted((set(truth) | set(predicted)) - set(rubric.labels))
    if invalid:
        raise ValueError("unsupported taxonomy label: %s" % invalid[0])

    plan = AdaptiveSamplingPlan.from_rubric(rubric)
    support_counts = Counter(truth)
    support_gaps = {
        label: max(0, plan.minimum_heldout_per_label - support_counts[label])
        for label in plan.support_labels
    }
    support_gaps = {label: gap for label, gap in support_gaps.items() if gap}
    promotion = rubric.extensions.get("promotion") or {}
    positive = set(str(label) for label in promotion.get("positive_labels") or ())
    if not positive or not positive.issubset(set(rubric.labels)):
        raise ValueError("invalid taxonomy promotion labels")

    true_positive = sum(actual in positive and guess in positive
                        for actual, guess in zip(truth, predicted))
    false_positive = sum(actual not in positive and guess in positive
                         for actual, guess in zip(truth, predicted))
    false_negative = sum(actual in positive and guess not in positive
                         for actual, guess in zip(truth, predicted))
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    precision = (true_positive / precision_denominator
                 if precision_denominator else 0.0)
    recall = (true_positive / recall_denominator
              if recall_denominator else 0.0)
    agreement = (sum(actual == guess for actual, guess in zip(truth, predicted))
                 / len(truth) if truth else 0.0)
    unknown_rate = (predicted.count("unknown") / len(predicted)
                    if predicted and "unknown" in rubric.labels else None)

    checks = {
        "adequate_class_support": not support_gaps,
        "precision": precision >= rubric.target_precision,
        "recall": recall >= rubric.target_recall,
        "agreement": agreement >= rubric.target_agreement,
    }
    result = {
        "status": "unvalidated",
        "decision_support": False,
        "sample_size": len(truth),
        "support_counts": dict(sorted(support_counts.items())),
        "support_gaps": support_gaps,
        "precision": precision,
        "recall": recall,
        "agreement": agreement,
        "checks": checks,
    }
    if plan.maximum_unknown_rate is not None:
        checks["unknown_rate"] = bool(
            unknown_rate is not None
            and unknown_rate <= plan.maximum_unknown_rate)
        result["unknown_rate"] = unknown_rate
        result["maximum_unknown_rate"] = plan.maximum_unknown_rate
    if "maximum_polling_false_positive_rate" in promotion:
        non_polling = sum(actual != "polling" for actual in truth)
        polling_false_positives = sum(
            actual != "polling" and guess == "polling"
            for actual, guess in zip(truth, predicted))
        polling_rate = (polling_false_positives / non_polling
                        if non_polling else 0.0)
        maximum = float(promotion["maximum_polling_false_positive_rate"])
        checks["polling_false_positive_rate"] = polling_rate <= maximum
        result["polling_false_positive_rate"] = polling_rate
        result["maximum_polling_false_positive_rate"] = maximum
    if truth and all(checks.values()):
        result["status"] = "promoted"
        result["decision_support"] = True
    return result


@dataclass(frozen=True)
class _Candidate:
    stable_key: str
    source: str
    stratum: str
    context: str
    user_turn: str
    proposed_label: str
    proposal_reason: str


def _kind(record):
    return record.span_kind.value if isinstance(record.span_kind, SpanKind) \
        else record.span_kind


def _failure_sampling_stratum(evidence):
    """Return a reproducible sampling hint, never a decision label."""
    evidence = evidence or {}
    return classify_failure_evidence(
        evidence.get("tool_input"), evidence.get("tool_result")).kind


def _shown(value, limit=600):
    value = str(value or "").strip()
    return value[:limit] if value else "unavailable"


def _repeat_evidence(item, private_evidence):
    previous = private_evidence.get(item.previous.span_id, {})
    current = private_evidence.get(item.current.span_id, {})
    tool = current.get("tool_kind") or previous.get("tool_kind") \
        or item.current.tool_kind or "unknown"
    parts = [
        "### Prior call",
        "- Source: %s" % item.current.source,
        "- Tool: %s" % tool,
        "- Prior call purpose: %s" % _shown(previous.get("intent_context")),
        "- Prior call input: %s" % _shown(previous.get("tool_input")),
        "- Prior result: %s" % _shown(previous.get("tool_result")),
    ]
    intervening = []
    for record in item.intervening_records[-4:]:
        evidence = private_evidence.get(record.span_id, {})
        intervening.append(
            "%s purpose=%s input=%s result=%s" % (
                evidence.get("tool_kind") or record.tool_kind or "unknown",
                _shown(evidence.get("intent_context"), 240),
                _shown(evidence.get("tool_input"), 240),
                _shown(evidence.get("tool_result"), 240)))
    parts.extend([
        "### Intervening operations",
        "- Count: %d" % len(item.intervening_records),
        "- Recent evidence: %s" % (
            " | ".join(intervening) if intervening else "none"),
    ])
    current_parts = [
        "### Current repeated call",
        "- Source: %s" % item.current.source,
        "- Tool: %s" % tool,
        "- Current call purpose: %s" % _shown(current.get("intent_context")),
        "- Current call input: %s" % _shown(current.get("tool_input")),
        "- Current result: %s" % _shown(current.get("tool_result")),
        "- Structural observation: %s" % item.reason_code,
    ]
    return "\n".join(parts), "\n".join(current_parts)


def _validate_candidate_evidence(candidate, rubric_id):
    if rubric_id != "duplicate_work":
        return
    required_context = ("### Prior call", "### Intervening operations",
                        "Prior call purpose:", "Prior result:")
    required_focal = ("### Current repeated call", "Current call purpose:",
                      "Current call input:")
    if not all(value in candidate.context for value in required_context):
        raise ValueError("repeat review case lacks structured prior evidence")
    if not all(value in candidate.user_turn for value in required_focal):
        raise ValueError("repeat review case lacks structured current evidence")
    if "### Current" in candidate.context:
        raise ValueError("repeat review case mixes current and prior evidence")
    evaluator_phrases = ("assess whether", "choose one", "classify why",
                         "proposed diagnosis")
    if any(value in candidate.user_turn.lower() for value in evaluator_phrases):
        raise ValueError("repeat review evidence contains evaluator instructions")


def _candidates(records, rubric_id, private_evidence=None):
    taxonomy = load_tool_taxonomy()
    private_evidence = private_evidence or {}
    if rubric_id == "duplicate_work":
        candidates = []
        for item in repeated_call_candidates(records, taxonomy):
            context, current = _repeat_evidence(item, private_evidence)
            candidate = _Candidate(
                stable_key=item.current.span_id,
                source=item.current.source,
                stratum=item.candidate_class,
                context=context,
                user_turn=current,
                proposed_label={
                    "polling": "polling",
                    "post_state_change": "post_state_change_verification",
                    "candidate_waste": "wasteful_duplicate",
                }[item.candidate_class],
                proposal_reason=item.reason_code,
            )
            _validate_candidate_evidence(candidate, rubric_id)
            candidates.append(candidate)
        return tuple(candidates)
    if rubric_id == "tool_failure_kind":
        candidates = []
        for record in records:
            if (_kind(record) != "retro.tool_result"
                    or record.status != "error"):
                continue
            evidence = private_evidence.get(record.span_id, {})
            diagnosis = classify_failure_evidence(
                evidence.get("tool_input"), evidence.get("tool_result"))
            candidates.append(_Candidate(
                stable_key=record.span_id,
                source=record.source,
                stratum=diagnosis.kind,
                context=("Source: %s. Tool: %s. Status: %s. Structural kind: %s."
                         % (record.source, record.tool_kind or "unknown",
                            record.status, taxonomy.failure_kind(
                                record.attributes.get("error_kind")))),
                user_turn=(
                    "Redacted input: %s. Redacted result: %s."
                    % (evidence.get("tool_input") or "unavailable",
                       evidence.get("tool_result") or "unavailable")),
                proposed_label=diagnosis.kind,
                proposal_reason=diagnosis.reason_code,
            ))
        return tuple(candidates)
    raise ValueError("unsupported taxonomy rubric")


def _assigned_split(candidate, rubric_id):
    digest = hashlib.sha256(
        (rubric_id + "|" + candidate.stable_key).encode("utf-8")).digest()
    return "calibration" if digest[0] < 128 else "test"


def _balanced(candidates, count, rubric_id, split):
    pools = defaultdict(list)
    for candidate in candidates:
        if _assigned_split(candidate, rubric_id) != split:
            continue
        rank = hashlib.sha256(
            ("rank|" + rubric_id + "|" + split + "|" +
             candidate.stable_key).encode("utf-8")).hexdigest()
        pools[candidate.stratum].append((rank, candidate))
    for values in pools.values():
        values.sort(key=lambda item: item[0])
    selected = []
    strata = sorted(pools)
    while len(selected) < count and strata:
        next_strata = []
        for stratum in strata:
            if pools[stratum] and len(selected) < count:
                selected.append(pools[stratum].pop(0)[1])
            if pools[stratum]:
                next_strata.append(stratum)
        strata = next_strata
    return selected


def _candidate_fingerprint(rubric_id, stable_key):
    return hashlib.sha256(
        (rubric_id + "|" + stable_key).encode("utf-8")).hexdigest()


def _write_packet(path, manifest_path, candidates, *, rubric, protocol,
                  dataset_id, split, plan, input_sha256, round_number,
                  private_evidence_sha256=""):
    _external(path)
    _external(manifest_path)
    salt = _salt(path.parent)
    rows = []
    for candidate in candidates:
        raw = "%s|%s" % (dataset_id, candidate.stable_key)
        case_id = hmac.new(salt, raw.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
        rows.append({
            "case_id": case_id, "source": candidate.source, "split": split,
            "context_chars": len(candidate.context),
            "user_turn_chars": len(candidate.user_turn),
            "context": candidate.context, "user_turn": candidate.user_turn,
            "proposed_label": candidate.proposed_label,
            "proposal_reason": candidate.proposal_reason,
            "assessment": "", "human_label": "", "notes": "",
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TAXONOMY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "schema_version": 1,
        "annotation_packet_version": ANNOTATION_PACKET_VERSION,
        "dataset_id": dataset_id,
        "rubric_id": rubric.id,
        "rubric_version": rubric.version,
        "split": split,
        "selection": "stable split then deterministic round-robin by candidate stratum",
        "source_counts": dict(sorted(Counter(row["source"] for row in rows).items())),
        "candidate_strata": dict(sorted(Counter(
            candidate.stratum for candidate in candidates).items())),
        "input_fingerprints": {"normalized_traces": input_sha256},
        "sample_sha256": _packet_fingerprint(path),
        "candidate_sha256": [
            _candidate_fingerprint(rubric.id, candidate.stable_key)
            for candidate in candidates],
        "annotation_protocol_id": protocol.id,
        "annotation_protocol_version": protocol.version,
        "annotation_protocol_sha256": protocol.sha256,
        "adaptive_sampling": plan.to_dict(round_number=round_number),
        "review_quality": ({
            "status": "passed",
            "case_count": len(rows),
            "evidence_role_contract": "validated before write",
        } if rubric.id == "duplicate_work" else {
            "status": "not_evaluated",
            "case_count": len(rows),
            "evidence_role_contract": "not_defined",
        }),
    }
    if rubric.id == "tool_failure_kind":
        manifest["sampling_hint_version"] = FAILURE_SAMPLING_HINT_VERSION
    if private_evidence_sha256:
        manifest["input_fingerprints"]["private_evidence"] = (
            private_evidence_sha256)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    return manifest


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _instructions(output_dir, packets):
    launcher = Path(__file__).resolve().parents[1] / "bin" / "retro-eval-labels"
    lines = [
        "# 01 - Optional taxonomy assessment review",
        "",
        "Each case starts with the rule-based scorer's proposed diagnosis and "
        "reason. Mark "
        "it Correct, Incorrect, or Unsure; when Incorrect, choose the better "
        "label. No written explanation is required. This review is optional: "
        "without it the scorer remains unvalidated and barred from decision "
        "support.",
        "",
    ]
    for index, packet in enumerate(packets, 1):
        command = subprocess.list2cmdline([
            sys.executable, str(launcher), "serve", "--source", packet["source"],
            "--manifest", packet["manifest"], "--no-open",
        ])
        lines.extend([
            "## %d. %s %s" % (index, packet["rubric_id"], packet["split"]),
            "",
            "```text", command, "```", "",
        ])
    lines.extend([
        "If you choose to review, done means every case has an assessment. The "
        "Goal never treats an empty or partial packet as implicit acceptance.", "",
    ])
    path = output_dir / "01-review-instructions.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_taxonomy_review_packets(trace_path: Path, output_dir: Path, *,
                                  size_overrides=None, round_number=1,
                                  prior_manifests=(), private_evidence=None):
    trace_path = Path(trace_path)
    output_dir = Path(output_dir)
    _external(output_dir)
    records = JsonlTraceStore(trace_path).read()
    plugin_root = Path(__file__).resolve().parents[1]
    rubrics = load_rubric_catalogue(plugin_root / "rubrics" / "rubrics.json")
    protocols = load_annotation_protocol_catalogue(
        plugin_root / "rubrics" / "annotation-protocols.json", rubrics)
    rubric_index = {item.id: item for item in rubrics.rubrics}
    specs = (
        ("duplicate_work", "duplicate-work-taxonomy", 4, "duplicate-work"),
        ("tool_failure_kind", "tool-failure-taxonomy", 2, "tool-failure"),
    )
    packets = []
    input_sha256 = _sha256(trace_path)
    private_evidence = dict(private_evidence or {})
    private_evidence_sha256 = (hashlib.sha256(json.dumps(
        private_evidence, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
        if private_evidence else "")
    size_overrides = dict(size_overrides or {})
    if round_number < 1:
        raise ValueError("round number must be positive")
    prior_by_rubric = defaultdict(set)
    for prior_path in prior_manifests:
        try:
            prior = json.loads(Path(prior_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid prior taxonomy packet manifest") from exc
        if prior.get("input_fingerprints", {}).get("normalized_traces") != input_sha256:
            raise ValueError("adaptive rounds require the same trace snapshot")
        prior_by_rubric[str(prior.get("rubric_id") or "")].update(
            str(value) for value in prior.get("candidate_sha256") or ())
    for rubric_id, protocol_id, protocol_version, stem in specs:
        rubric = rubric_index[rubric_id]
        protocol = protocols.get(protocol_id, protocol_version)
        plan = AdaptiveSamplingPlan.from_rubric(rubric)
        candidates = tuple(candidate for candidate in _candidates(
                               records, rubric_id, private_evidence)
                           if _candidate_fingerprint(
                               rubric_id, candidate.stable_key)
                           not in prior_by_rubric[rubric_id])
        if round_number > plan.maximum_rounds:
            raise ValueError("round number exceeds adaptive sampling plan")
        for split, default_size in (("calibration", plan.initial_calibration),
                                    ("test", plan.initial_heldout)):
            requested = int(size_overrides.get(rubric_id, {}).get(split,
                                                                  default_size))
            selected = _balanced(candidates, requested, rubric_id, split)
            source = (output_dir / ("%s-%s-round%s.csv"
                                    % (stem, split, round_number))).resolve()
            manifest_path = (output_dir /
                             ("%s-%s-round%s-manifest.json"
                              % (stem, split, round_number))).resolve()
            dataset_id = "%s-v%s-%s-round%s" % (
                stem, rubric.version, split, round_number)
            _write_packet(
                source, manifest_path, selected, rubric=rubric,
                protocol=protocol, dataset_id=dataset_id, split=split,
                plan=plan, input_sha256=input_sha256,
                round_number=round_number,
                private_evidence_sha256=private_evidence_sha256)
            packets.append({
                "rubric_id": rubric_id, "split": split,
                "source": str(source), "manifest": str(manifest_path),
                "population": len(selected),
            })
    instructions = _instructions(output_dir, packets)
    return {"packets": packets, "instructions": str(instructions.resolve())}
