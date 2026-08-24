"""Versioned, content-free human and judge label datasets."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from .statistics import confusion_metrics, wilson_interval


@dataclass(frozen=True)
class LabelRecord:
    schema_version: int
    dataset_id: str
    case_id: str
    rubric_id: str
    rubric_version: int
    split: str
    label: str
    annotator_kind: str
    annotator_id: str
    evidence_refs: tuple[str, ...]
    created_at: str
    sampling_weight: float = 1.0

    def __post_init__(self):
        if self.schema_version != 1:
            raise ValueError("unsupported label schema")
        if self.split not in {"calibration", "test"}:
            raise ValueError("label split must be calibration or test")
        if self.annotator_kind not in {"human", "model_judge", "deterministic"}:
            raise ValueError("unsupported annotator kind")
        if not self.evidence_refs:
            raise ValueError("labels require evidence references")
        if self.sampling_weight <= 0:
            raise ValueError("sampling weight must be positive")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, payload):
        values = dict(payload)
        values["evidence_refs"] = tuple(values.get("evidence_refs") or ())
        return cls(**values)


class LabelStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        if any((parent / ".git").exists()
               for parent in (self.path.parent, *self.path.parent.parents)):
            raise ValueError("label datasets must remain outside repositories")

    def write(self, labels):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                for label in labels:
                    handle.write(json.dumps(label.to_dict(), sort_keys=True) + "\n")
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def read(self):
        labels = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError("label dataset is unreadable") from exc
        for line in lines:
            try:
                labels.append(LabelRecord.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError("invalid label record") from exc
        return labels


def _case_map(labels):
    result = {}
    for item in labels:
        if item.case_id in result:
            raise ValueError("duplicate case for annotator")
        result[item.case_id] = item
    return result


def multiclass_agreement(left, right):
    left = _case_map(left)
    right = _case_map(right)
    shared = sorted(set(left) & set(right))
    if not shared:
        raise ValueError("agreement requires shared cases")
    pairs = [(left[case].label, right[case].label) for case in shared]
    observed = sum(a == b for a, b in pairs) / len(pairs)
    left_counts = Counter(a for a, _ in pairs)
    right_counts = Counter(b for _, b in pairs)
    labels = set(left_counts) | set(right_counts)
    expected = sum(left_counts[label] * right_counts[label] for label in labels) / len(pairs) ** 2
    kappa = None if expected == 1 else (observed - expected) / (1 - expected)
    low, high = wilson_interval(sum(a == b for a, b in pairs), len(pairs))
    return {"population": len(pairs), "agreement": observed, "kappa": kappa,
            "agreement_interval": (low, high)}


def calibration_report(truth, predicted, *, positive_label,
                       splits=("calibration", "test")):
    truth_map = _case_map(truth)
    predicted_map = _case_map(predicted)
    report = {}
    for split in splits:
        cases = sorted(case for case in set(truth_map) & set(predicted_map)
                       if truth_map[case].split == predicted_map[case].split == split)
        if not cases:
            raise ValueError("both calibration and test require shared cases")
        actual = [truth_map[case].label == positive_label for case in cases]
        guesses = [predicted_map[case].label == positive_label for case in cases]
        metrics = confusion_metrics(actual, guesses)
        weighted = Counter()
        for case, is_positive, guessed_positive in zip(cases, actual, guesses):
            weight = truth_map[case].sampling_weight
            if abs(weight - predicted_map[case].sampling_weight) > 1e-9:
                raise ValueError("paired labels must use identical sampling weights")
            key = ("tp" if is_positive and guessed_positive else
                   "fn" if is_positive else
                   "fp" if guessed_positive else "tn")
            weighted[key] += weight
        weighted_total = sum(weighted.values())
        metrics.update({
            "weighted_population": weighted_total,
            "weighted_precision": _weighted_ratio(
                weighted["tp"], weighted["tp"] + weighted["fp"]),
            "weighted_recall": _weighted_ratio(
                weighted["tp"], weighted["tp"] + weighted["fn"]),
            "weighted_agreement": _weighted_ratio(
                weighted["tp"] + weighted["tn"], weighted_total),
        })
        agreement = multiclass_agreement(
            [truth_map[case] for case in cases], [predicted_map[case] for case in cases])
        metrics.update({"agreement": agreement["agreement"], "kappa": agreement["kappa"],
                        "agreement_interval": agreement["agreement_interval"]})
        if metrics["tp"] + metrics["fp"]:
            metrics["precision_interval"] = wilson_interval(
                metrics["tp"], metrics["tp"] + metrics["fp"])
        if metrics["tp"] + metrics["fn"]:
            metrics["recall_interval"] = wilson_interval(
                metrics["tp"], metrics["tp"] + metrics["fn"])
        report[split] = metrics
    return report


def _weighted_ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def multiclass_calibration_report(truth, predicted, *, labels,
                                  splits=("calibration", "test")):
    truth = tuple(truth)
    predicted = tuple(predicted)
    class_reports = {
        label: calibration_report(
            truth, predicted, positive_label=label, splits=splits)
        for label in labels
    }
    truth_map = _case_map(truth)
    predicted_map = _case_map(predicted)
    report = {}
    for split in splits:
        cases = sorted(case for case in set(truth_map) & set(predicted_map)
                       if truth_map[case].split == predicted_map[case].split == split)
        agreement = multiclass_agreement(
            [truth_map[case] for case in cases], [predicted_map[case] for case in cases])
        report[split] = {
            **agreement,
            "classes": {label: class_reports[label][split] for label in labels},
        }
    return report


def _validate_prediction_alignment(truth, predicted, *, name, labels, splits):
    truth_map = _case_map(truth)
    predicted_map = _case_map(predicted)
    selected_truth = {
        case: item for case, item in truth_map.items() if item.split in splits
    }
    selected_predicted = {
        case: item for case, item in predicted_map.items() if item.split in splits
    }
    if set(selected_truth) != set(selected_predicted):
        missing = len(set(selected_truth) - set(selected_predicted))
        extra = len(set(selected_predicted) - set(selected_truth))
        raise ValueError(
            "%s case coverage mismatch: %d missing, %d extra" % (name, missing, extra))
    allowed = frozenset(labels)
    for case, actual in selected_truth.items():
        guessed = selected_predicted[case]
        if actual.dataset_id != guessed.dataset_id:
            raise ValueError("%s dataset mismatch" % name)
        if (actual.rubric_id != guessed.rubric_id
                or actual.rubric_version != guessed.rubric_version):
            raise ValueError("%s rubric mismatch" % name)
        if actual.split != guessed.split:
            raise ValueError("%s split mismatch" % name)
        if abs(actual.sampling_weight - guessed.sampling_weight) > 1e-9:
            raise ValueError("%s sampling-weight mismatch" % name)
        if actual.label not in allowed or guessed.label not in allowed:
            raise ValueError("%s contains a label outside the rubric" % name)
    return selected_truth, selected_predicted


def strict_multiclass_comparison_report(truth, predictions, *, labels,
                                        splits=("calibration", "test")):
    """Compare frozen predictors only when their evaluation sets match exactly."""
    truth = tuple(truth)
    if not truth:
        raise ValueError("comparison requires human truth")
    if not predictions:
        raise ValueError("comparison requires at least one predictor")
    selected_population = sum(item.split in splits for item in truth)
    reports = {}
    for name, predicted in predictions.items():
        predicted = tuple(predicted)
        _validate_prediction_alignment(
            truth, predicted, name=name, labels=labels, splits=splits)
        reports[name] = multiclass_calibration_report(
            truth, predicted, labels=labels, splits=splits)
    return {
        "schema_version": 1,
        "population": selected_population,
        "splits": list(splits),
        "labels": list(labels),
        "predictors": reports,
    }


def import_legacy_turn_labels(source: Path, target: Path,
                              prediction_target: Path | None = None,
                              predictor=None):
    """Import prior human marks without carrying redacted transcript text."""
    salt_path = target.parent / ".label-id-salt"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        salt = salt_path.read_bytes()
    except OSError:
        salt = os.urandom(32)
        descriptor = os.open(salt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(salt)
    human = []
    predicted = []
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError("legacy label source is unreadable") from exc
    for line in lines:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if raw.get("kind") != "turn" or not str(raw.get("label") or "").strip():
            continue
        case_id = hmac.new(salt, str(raw.get("id") or "").encode(), hashlib.sha256).hexdigest()[:24]
        sampled = int(raw.get("stratum_sampled") or 0)
        population = int(raw.get("stratum_population") or 0)
        weight = population / sampled if sampled and population else 1.0
        common = dict(
            schema_version=1, dataset_id="turn-friction-legacy-v1",
            case_id=case_id, rubric_id="turn_friction_legacy", rubric_version=1,
            split="calibration", evidence_refs=("legacy:" + case_id,),
            created_at="legacy-import", sampling_weight=weight,
        )
        human.append(LabelRecord(
            **common, label=str(raw["label"]), annotator_kind="human",
            annotator_id="human-rater-v1",
        ))
        if prediction_target is not None:
            predicted.append(LabelRecord(
                **common, label=str((predictor(raw) if predictor else raw.get("predicted"))
                                    or "none"),
                annotator_kind="deterministic", annotator_id="legacy-rule-v1",
            ))
    LabelStore(target).write(human)
    if prediction_target is not None:
        LabelStore(prediction_target).write(predicted)
    return {"imported": len(human),
            "total_weight": sum(item.sampling_weight for item in human)}
