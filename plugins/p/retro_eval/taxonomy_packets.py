"""Adaptive external annotation packets for tool taxonomies."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .annotation import (FIELDS, _external, _salt, _check_packet_destinations,
                         _create_packet_csv)
from .catalog import (load_annotation_protocol_catalogue,
                      load_rubric_catalogue)
from .schema import SpanKind
from .storage import JsonlTraceStore
from .text_rules import evidence_excerpt
from .taxonomies import (classify_failure_evidence, load_tool_taxonomy,
                         repeated_call_candidates)


FAILURE_SAMPLING_HINT_VERSION = 1
TAXONOMY_FIELDS = FIELDS[:-2] + (
    "proposed_label", "proposal_reason", "assessment") + FIELDS[-2:]
ANNOTATION_PACKET_VERSION = 7
_NONTERMINAL_POLLING_RESULT = re.compile(
    r"\b(?:still\s+)?running\b|\btimed?\s*out\b|\bqueued\b|"
    r"\bin\s+progress\b|\bnot\s+(?:yet\s+)?(?:complete|finished|resolved)\b",
    re.I,
)


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



def assess_packet_support(packets, rubric, *, require_bindings=False):
    """Assess captured current/prior packets; support is not promotion."""
    if not packets:
        raise ValueError("assessment requires a packet")
    plan = AdaptiveSamplingPlan.from_rubric(rubric)
    current = packets[0][0]
    rounds = []
    for manifest, rows in packets:
        if (manifest.get("rubric_id") != rubric.id
                or type(manifest.get("rubric_version")) is not int
                or manifest["rubric_version"] != rubric.version):
            raise ValueError("assessment packet rubric differs")
        sampling = manifest.get("adaptive_sampling")
        number = sampling.get("round") if isinstance(sampling, dict) else None
        if type(number) is not int or not 1 <= number <= plan.maximum_rounds:
            raise ValueError("invalid adaptive taxonomy packet round")
        rounds.append(number)
    if len(packets) > 1 or require_bindings:
        if sorted(rounds) != list(range(1, rounds[0] + 1)):
            raise ValueError("cumulative assessment requires each earlier round exactly once")
        if current.get("split") not in ("calibration", "test"):
            raise ValueError("cumulative assessment requires a valid split")
        if current.get("split_policy") != SPLIT_POLICY:
            raise ValueError("cumulative assessment requires the current split policy")
        protocol_version = current.get("annotation_protocol_version")
        candidate_version = current.get("candidate_taxonomy_version")
        if (type(protocol_version) is not int or type(candidate_version) is not int
                or candidate_version < 1):
            raise ValueError("cumulative assessment requires versioned method bindings")
        catalogue_root = Path(__file__).resolve().parents[1] / "rubrics"
        protocols = load_annotation_protocol_catalogue(
            catalogue_root / "annotation-protocols.json",
            load_rubric_catalogue(catalogue_root / "rubrics.json"))
        protocol = protocols.get(current.get("annotation_protocol_id"), protocol_version)
        if (protocol.rubric_id != rubric.id or protocol.rubric_version != rubric.version
                or current.get("annotation_protocol_sha256") != protocol.sha256):
            raise ValueError("cumulative assessment protocol binding differs")
        inputs = current.get("input_fingerprints")
        if (not isinstance(inputs, dict) or not isinstance(inputs.get("normalized_traces"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", inputs["normalized_traces"])):
            raise ValueError("cumulative assessment requires trace identity")
        keys = ("rubric_id", "rubric_version", "split", "split_policy",
                "candidate_taxonomy_version", "annotation_protocol_id",
                "annotation_protocol_version", "annotation_protocol_sha256", "input_fingerprints")
        seen = set()
        for manifest, rows in packets:
            if any(type(manifest.get(key)) is not type(current.get(key))
                   or manifest.get(key) != current.get(key) for key in keys):
                raise ValueError("cumulative assessment packet bindings differ")
            identities = manifest.get("candidate_sha256")
            if (not isinstance(identities, list) or len(identities) != len(rows)
                    or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                           for value in identities)
                    or len(set(identities)) != len(identities) or seen.intersection(identities)):
                raise ValueError("cumulative assessment requires unique candidate identities")
            seen.update(identities)
            if any(row.get("split") != current["split"] for row in rows):
                raise ValueError("cumulative assessment row split differs")
        if sum(len(rows) for _, rows in packets) > plan.maximum_total:
            raise ValueError("cumulative assessment exceeds case budget")
    rows = [row for _, packet_rows in packets for row in packet_rows]
    labels = [str(row.get("human_label") or "").strip() for row in rows
              if str(row.get("human_label") or "").strip()]
    if set(labels) - set(rubric.labels):
        raise ValueError("annotation packet contains unsupported taxonomy labels")
    result = assess_label_support(labels, plan, round_number=rounds[0])
    result.update(labelled=len(labels), available_rows=len(rows),
                  unlabelled=len(rows) - len(labels), rounds_included=sorted(rounds),
                  assessment_scope="cumulative" if len(packets) > 1 else "packet",
                  decision_support=False)
    return result


def assess_taxonomy_promotion(truth, predicted, rubric):
    """Historical API name: assess numerical criteria, never authorize promotion.

    Bare label pairs cannot establish held-out provenance or intended-use validity.
    """
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
                 if precision_denominator else None)
    recall = (true_positive / recall_denominator
              if recall_denominator else None)
    agreement = (sum(actual == guess for actual, guess in zip(truth, predicted))
                 / len(truth) if truth else None)
    unknown_rate = (predicted.count("unknown") / len(predicted)
                    if predicted and "unknown" in rubric.labels else None)

    checks = {
        "adequate_class_support": not support_gaps,
        "precision": precision is not None and precision >= rubric.target_precision,
        "recall": recall is not None and recall >= rubric.target_recall,
        "agreement": agreement is not None and agreement >= rubric.target_agreement,
    }
    result = {
        "status": "unvalidated",
        "decision_support": False,
        "assessment_version": 2,
        "assessment_scope": "observed_label_pairs",
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
                        if non_polling else None)
        maximum = float(promotion["maximum_polling_false_positive_rate"])
        checks["polling_false_positive_rate"] = polling_rate is not None and polling_rate <= maximum
        result["polling_false_positive_rate"] = polling_rate
        result["maximum_polling_false_positive_rate"] = maximum
    result["thresholds_met"] = bool(truth and all(checks.values()))
    if result["thresholds_met"]:
        result["status"] = "thresholds_met"
    return result


def assess_packet_criteria(packets, rubric):
    """Assess frozen proposals against captured labels without promoting a scorer."""
    result = assess_packet_support(packets, rubric, require_bindings=True)
    rows = [row for _, packet_rows in packets for row in packet_rows]
    if any(row.get("proposed_label") not in rubric.labels for row in rows):
        raise ValueError("annotation packet contains unsupported proposed labels")
    labelled = [row for row in rows if str(row.get("human_label") or "").strip()]
    criteria = assess_taxonomy_promotion(
        [str(row["human_label"]).strip() for row in labelled],
        [row["proposed_label"] for row in labelled], rubric)
    met = criteria["thresholds_met"] and result["unlabelled"] == 0
    split = packets[0][0]["split"]
    if result["unlabelled"]:
        owner, action = "human_reviewer", "Complete the remaining packet labels before evaluating all selected cases."
    elif result["status"] == "needs_more":
        owner, action = "investigating_agent", (
            "Collect the next preregistered round to address class-support gaps; "
            "keep the method frozen and retain every previous packet.")
    elif result["status"] == "insufficient_evidence":
        owner, action = "investigating_agent", (
            "Record insufficient evidence at the sampling bound; retain the scorer "
            "as unvalidated without extending this sampling plan.")
    elif not met:
        owner = "investigating_agent"
        action = ("Inspect calibration errors and revise using development evidence."
                  if split == "calibration" else
                  "Record the failed confirmation; any revision needs separate development evidence and new held-out confirmation.")
    else:
        owner, action = "investigating_agent", (
            "Review predictor-freeze and exposure history, target population, sampling design "
            "and intended-use evidence before proposing any promotion.")
    result.update(
        support_status=result["status"],
        status="criteria_met" if met else "criteria_not_met",
        assessment_version=2, split=split, criteria=criteria, criteria_met=met,
        rubric_id=rubric.id, rubric_version=rubric.version,
        candidate_taxonomy_version=packets[0][0]["candidate_taxonomy_version"],
        packet_bindings=[{
            "round": manifest["adaptive_sampling"]["round"],
            "sample_sha256": manifest.get("sample_sha256"),
            "annotation_protocol_id": manifest["annotation_protocol_id"],
            "annotation_protocol_version": manifest["annotation_protocol_version"],
            "annotation_protocol_sha256": manifest["annotation_protocol_sha256"],
            "normalized_traces_sha256": manifest["input_fingerprints"]["normalized_traces"],
        } for manifest, _ in sorted(packets, key=lambda item: item[0]["adaptive_sampling"]["round"])],
        decision_support=False,
        next_action={"owner": owner, "action": action,
                     "prerequisites": "Retain the validated packets and the comparison protocol; do not tune on held-out labels.",
                     "acceptance": "Document a scoped validation decision with supporting evidence; passing counts alone is insufficient."})
    return result


@dataclass(frozen=True)
class _Candidate:
    trace_id: str
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
    return evidence_excerpt(value, limit) if value else "unavailable"


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


def _polling_proposal(item, private_evidence):
    """Avoid treating a polling tool name as proof that the repeat was useful."""
    observations = (item.previous,) + item.intervening_records + (item.current,)
    nonterminal = sum(
        bool(_NONTERMINAL_POLLING_RESULT.search(str(
            private_evidence.get(record.span_id, {}).get("tool_result") or "")))
        for record in observations
    )
    if nonterminal >= 3:
        return "wasteful_duplicate", "repeated_polling_overhead"
    return "ambiguous", "polling_cost_unobservable"


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


def _candidates(records, rubric_id, private_evidence=None, taxonomy=None):
    taxonomy = taxonomy or load_tool_taxonomy()
    private_evidence = private_evidence or {}
    if rubric_id == "duplicate_work":
        candidates = []
        for item in repeated_call_candidates(records, taxonomy):
            context, current = _repeat_evidence(item, private_evidence)
            if item.candidate_class == "polling":
                proposed_label, proposal_reason = _polling_proposal(
                    item, private_evidence)
            else:
                proposed_label = {
                    "post_state_change": "post_state_change_verification",
                    "candidate_waste": "wasteful_duplicate",
                }[item.candidate_class]
                proposal_reason = item.reason_code
            candidate = _Candidate(
                trace_id=item.current.trace_id,
                stable_key=item.current.span_id,
                source=item.current.source,
                stratum=item.candidate_class,
                context=context,
                user_turn=current,
                proposed_label=proposed_label,
                proposal_reason=proposal_reason,
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
                trace_id=record.trace_id,
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


SPLIT_POLICY = "source_trace_v1"


def _group_fingerprint(candidate):
    # The same normalized trace stays in one phase across both rubrics.
    # This is a grouping boundary, not a claim that traces are independent.
    raw = json.dumps([candidate.source, candidate.trace_id], separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _assigned_split(candidate, rubric_id):
    digest = hashlib.sha256(
        (SPLIT_POLICY + "|" + _group_fingerprint(candidate)).encode("utf-8")).digest()
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
                  candidate_taxonomy_version, private_evidence_sha256=""):
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
    sample_sha256 = _create_packet_csv(path, TAXONOMY_FIELDS, rows)
    manifest = {
        "schema_version": 1,
        "annotation_packet_version": ANNOTATION_PACKET_VERSION,
        "dataset_id": dataset_id,
        "rubric_id": rubric.id,
        "rubric_version": rubric.version,
        "candidate_taxonomy_version": candidate_taxonomy_version,
        "split": split,
        "selection": "source/trace split across rubrics, then deterministic round-robin by candidate stratum",
        "split_policy": SPLIT_POLICY,
        "candidate_group_sha256": [_group_fingerprint(candidate) for candidate in candidates],
        "source_counts": dict(sorted(Counter(row["source"] for row in rows).items())),
        "candidate_strata": dict(sorted(Counter(
            candidate.stratum for candidate in candidates).items())),
        "input_fingerprints": {"normalized_traces": input_sha256},
        "sample_sha256": sample_sha256,
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
    with manifest_path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def _instructions(path, packets):
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
    with path.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return path


def write_taxonomy_review_packets(trace_path: Path, output_dir: Path, *,
                                  size_overrides=None, round_number=1,
                                  prior_manifests=(), private_evidence=None):
    trace_path = Path(trace_path)
    output_dir = Path(output_dir)
    _external(output_dir)
    records, input_sha256 = JsonlTraceStore(trace_path).read_with_digest()
    plugin_root = Path(__file__).resolve().parents[1]
    rubrics = load_rubric_catalogue(plugin_root / "rubrics" / "rubrics.json")
    protocols = load_annotation_protocol_catalogue(
        plugin_root / "rubrics" / "annotation-protocols.json", rubrics)
    rubric_index = {item.id: item for item in rubrics.rubrics}
    taxonomy = load_tool_taxonomy()
    candidate_versions = {"duplicate_work": taxonomy.repeated_call_version,
                          "tool_failure_kind": taxonomy.tool_failure_version}
    specs = (
        ("duplicate_work", "duplicate-work-taxonomy", 5, "duplicate-work"),
        ("tool_failure_kind", "tool-failure-taxonomy", 2, "tool-failure"),
    )
    packets = []
    private_evidence = dict(private_evidence or {})
    private_evidence_sha256 = (hashlib.sha256(json.dumps(
        private_evidence, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
        if private_evidence else "")
    size_overrides = dict(size_overrides or {})
    if type(round_number) is not int or round_number < 1:
        raise ValueError("round number must be positive")
    plans = {rubric_id: AdaptiveSamplingPlan.from_rubric(rubric_index[rubric_id])
             for rubric_id, _, _, _ in specs}
    if any(round_number > plan.maximum_rounds for plan in plans.values()):
        raise ValueError("round number exceeds adaptive sampling plan")
    prior_by_rubric = defaultdict(set)
    prior_slots = set()
    for prior_path in prior_manifests:
        try:
            prior = json.loads(Path(prior_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid prior taxonomy packet manifest") from exc
        if not isinstance(prior, dict) or not isinstance(prior.get("input_fingerprints"), dict):
            raise ValueError("invalid prior taxonomy packet manifest")
        if prior["input_fingerprints"].get("normalized_traces") != input_sha256:
            raise ValueError("adaptive rounds require the same trace snapshot")
        prior_rubric = prior.get("rubric_id")
        if (not isinstance(prior_rubric, str) or prior_rubric not in candidate_versions
                or type(prior.get("candidate_taxonomy_version")) is not int
                or prior["candidate_taxonomy_version"] != candidate_versions[prior_rubric]):
            raise ValueError("prior packet candidate taxonomy differs or is unknown; "
                             "start a separate review set")
        if prior.get("split_policy") != SPLIT_POLICY:
            raise ValueError("prior packet split policy differs or is unknown; "
                             "start a separate review set")
        if prior["input_fingerprints"].get("private_evidence", "") != private_evidence_sha256:
            raise ValueError("adaptive rounds require the same private evidence")
        rubric = rubric_index[prior_rubric]
        _, protocol_id, protocol_version, _ = next(
            spec for spec in specs if spec[0] == prior_rubric)
        protocol = protocols.get(protocol_id, protocol_version)
        if (prior.get("rubric_version") != rubric.version
                or prior.get("annotation_protocol_id") != protocol.id
                or prior.get("annotation_protocol_version") != protocol.version
                or prior.get("annotation_protocol_sha256") != protocol.sha256):
            raise ValueError("prior packet rubric or protocol differs; start a separate review set")
        sampling = prior.get("adaptive_sampling")
        prior_round = sampling.get("round") if isinstance(sampling, dict) else None
        split = prior.get("split")
        if (type(prior_round) is not int or not 1 <= prior_round < round_number
                or split not in ("calibration", "test")):
            raise ValueError("prior packet must name an earlier round and valid split")
        slot = (prior_rubric, prior_round, split)
        if slot in prior_slots:
            raise ValueError("duplicate prior taxonomy packet")
        fingerprints = prior.get("candidate_sha256")
        if (not isinstance(fingerprints, list)
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in fingerprints)
                or len(set(fingerprints)) != len(fingerprints)
                or prior_by_rubric[prior_rubric].intersection(fingerprints)):
            raise ValueError("invalid or repeated prior candidate identities")
        prior_slots.add(slot)
        prior_by_rubric[prior_rubric].update(fingerprints)
    expected_slots = {(rubric_id, number, split)
                      for rubric_id in plans for number in range(1, round_number)
                      for split in ("calibration", "test")}
    if prior_slots != expected_slots:
        raise ValueError("adaptive rounds require all earlier rubric and split manifests")
    instructions_path = output_dir / (
        "01-review-instructions.md" if round_number == 1
        else "01-review-instructions-round%s.md" % round_number)
    if instructions_path.exists() or instructions_path.is_symlink():
        raise ValueError("taxonomy packet destination already exists; "
                         "use a fresh round or review-set directory")
    instructions_path = instructions_path.resolve()
    destinations = {}
    for rubric_id, _, _, stem in specs:
        for split in ("calibration", "test"):
            paths = (output_dir / ("%s-%s-round%s.csv" % (stem, split, round_number)),
                     output_dir / ("%s-%s-round%s-manifest.json" %
                                   (stem, split, round_number)))
            _check_packet_destinations(*paths)
            destinations[rubric_id, split] = tuple(path.resolve() for path in paths)
    prepared = []
    for rubric_id, protocol_id, protocol_version, stem in specs:
        rubric = rubric_index[rubric_id]
        protocol = protocols.get(protocol_id, protocol_version)
        plan = plans[rubric_id]
        candidates = tuple(candidate for candidate in _candidates(
                               records, rubric_id, private_evidence, taxonomy)
                           if _candidate_fingerprint(
                               rubric_id, candidate.stable_key)
                           not in prior_by_rubric[rubric_id])
        selected_count = len(prior_by_rubric[rubric_id])
        for split, default_size in (("calibration", plan.initial_calibration),
                                    ("test", plan.initial_heldout)):
            requested = size_overrides.get(rubric_id, {}).get(split, default_size)
            if type(requested) is not int or requested < 0:
                raise ValueError("sample sizes must be nonnegative integers")
            selected = _balanced(candidates, requested, rubric_id, split)
            selected_count += len(selected)
            if selected_count > plan.maximum_total:
                raise ValueError("selected cases exceed the adaptive sampling maximum_total")
            prepared.append((rubric_id, protocol, stem, plan, split, selected))
    for rubric_id, protocol, stem, plan, split, selected in prepared:
        rubric = rubric_index[rubric_id]
        source, manifest_path = destinations[rubric_id, split]
        dataset_id = "%s-v%s-%s-round%s" % (
            stem, rubric.version, split, round_number)
        _write_packet(
            source, manifest_path, selected, rubric=rubric,
            protocol=protocol, dataset_id=dataset_id, split=split,
            plan=plan, input_sha256=input_sha256,
            round_number=round_number,
            candidate_taxonomy_version=candidate_versions[rubric_id],
            private_evidence_sha256=private_evidence_sha256)
        packets.append({
            "rubric_id": rubric_id, "split": split,
            "source": str(source), "manifest": str(manifest_path),
            "population": len(selected),
        })
    instructions = _instructions(instructions_path, packets)
    return {"packets": packets, "instructions": str(instructions.resolve())}
