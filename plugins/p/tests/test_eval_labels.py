import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.labels import (  # noqa: E402
    LabelRecord, LabelStore, calibration_report, import_legacy_turn_labels,
    multiclass_agreement, multiclass_calibration_report,
    strict_multiclass_comparison_report,
)


class LabelInfrastructureTests(unittest.TestCase):
    def label(self, case, label, annotator, split="test"):
        return LabelRecord(
            schema_version=1, dataset_id="turn-intent-v1", case_id=case,
            rubric_id="turn_intent", rubric_version=1, split=split,
            label=label, annotator_kind="human", annotator_id=annotator,
            evidence_refs=(f"e-{case}",), created_at="2026-08-22T00:00:00Z",
        )

    def test_label_store_is_external_versioned_and_content_free(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.jsonl"
            store = LabelStore(path)
            labels = [self.label("a", "correction", "rater-a")]
            store.write(labels)
            self.assertEqual(labels, store.read())
            self.assertNotIn("content", path.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            LabelStore(PLUGIN_ROOT / "labels.jsonl")

    def test_multiclass_agreement_uses_shared_cases(self):
        left = [self.label("a", "correction", "a"),
                self.label("b", "question", "a"),
                self.label("c", "question", "a")]
        right = [self.label("a", "correction", "b"),
                 self.label("b", "question", "b"),
                 self.label("c", "correction", "b")]
        result = multiclass_agreement(left, right)
        self.assertEqual(3, result["population"])
        self.assertAlmostEqual(2 / 3, result["agreement"])
        self.assertIsNotNone(result["kappa"])

    def test_calibration_report_keeps_calibration_and_test_separate(self):
        truth = [
            self.label("a", "correction", "human", "calibration"),
            self.label("b", "question", "human", "calibration"),
            self.label("c", "correction", "human", "test"),
            self.label("d", "question", "human", "test"),
        ]
        predicted = [
            self.label("a", "correction", "judge", "calibration"),
            self.label("b", "correction", "judge", "calibration"),
            self.label("c", "correction", "judge", "test"),
            self.label("d", "question", "judge", "test"),
        ]
        report = calibration_report(truth, predicted, positive_label="correction")
        self.assertEqual(2, report["calibration"]["population"])
        self.assertEqual(2, report["test"]["population"])
        self.assertEqual(1.0, report["test"]["precision"])
        self.assertEqual(1.0, report["test"]["recall"])
        self.assertEqual(1.0, report["test"]["agreement"])

    def test_legacy_import_drops_text_and_preserves_sampling_weight(self):
        raw = {
            "id": "case-a", "kind": "turn", "predicted": "question",
            "label": "correction", "said": "private user text",
            "after": "private assistant text", "stratum_population": 40,
            "stratum_sampled": 10,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "legacy.jsonl"
            target = root / "labels.jsonl"
            predictions = root / "predictions.jsonl"
            source.write_text(json.dumps(raw) + "\n", encoding="utf-8")
            result = import_legacy_turn_labels(
                source, target, predictions, predictor=lambda item: "correction")
            serialized = target.read_text(encoding="utf-8")
            predicted = LabelStore(predictions).read()
        self.assertEqual(1, result["imported"])
        self.assertEqual(4.0, result["total_weight"])
        self.assertNotIn("private user text", serialized)
        self.assertNotIn("private assistant text", serialized)
        self.assertEqual("correction", predicted[0].label)

    def test_multiclass_calibration_can_report_calibration_only(self):
        truth = [self.label("a", "correction", "human", "calibration"),
                 self.label("b", "question", "human", "calibration")]
        predicted = [self.label("a", "correction", "rule", "calibration"),
                     self.label("b", "correction", "rule", "calibration")]
        report = multiclass_calibration_report(
            truth, predicted, labels=("correction", "question"),
            splits=("calibration",),
        )
        self.assertEqual(2, report["calibration"]["population"])
        self.assertEqual(.5, report["calibration"]["agreement"])
        self.assertEqual(1.0, report["calibration"]["classes"]["correction"]["recall"])

    def test_calibration_reports_sampling_weighted_point_estimates(self):
        truth = [replace(self.label("a", "correction", "human", "calibration"),
                         sampling_weight=9),
                 self.label("b", "question", "human", "calibration")]
        predicted = [replace(self.label("a", "correction", "rule", "calibration"),
                             sampling_weight=9),
                     self.label("b", "correction", "rule", "calibration")]
        report = calibration_report(
            truth, predicted, positive_label="correction", splits=("calibration",))
        self.assertEqual(.5, report["calibration"]["precision"])
        self.assertEqual(.9, report["calibration"]["weighted_precision"])

    def test_strict_comparison_rejects_partial_or_misaligned_predictions(self):
        truth = [self.label("a", "correction", "human"),
                 self.label("b", "question", "human")]
        partial = [self.label("a", "correction", "rule")]
        with self.assertRaisesRegex(ValueError, "case coverage"):
            strict_multiclass_comparison_report(
                truth, {"rule": partial}, labels=("correction", "question"),
                splits=("test",))
        mismatched = [replace(self.label("a", "correction", "rule"),
                              dataset_id="other"),
                      self.label("b", "question", "rule")]
        with self.assertRaisesRegex(ValueError, "dataset"):
            strict_multiclass_comparison_report(
                truth, {"rule": mismatched}, labels=("correction", "question"),
                splits=("test",))

    def test_strict_comparison_scores_multiple_frozen_predictors(self):
        truth = [self.label("a", "correction", "human"),
                 self.label("b", "question", "human")]
        rule = [self.label("a", "correction", "rule"),
                self.label("b", "correction", "rule")]
        judge = [self.label("a", "correction", "judge"),
                 self.label("b", "question", "judge")]
        report = strict_multiclass_comparison_report(
            truth, {"rule": rule, "judge": judge},
            labels=("correction", "question"), splits=("test",))
        self.assertEqual(2, report["population"])
        self.assertEqual(.5, report["predictors"]["rule"]["test"]["agreement"])
        self.assertEqual(1.0, report["predictors"]["judge"]["test"]["agreement"])


if __name__ == "__main__":
    unittest.main()
