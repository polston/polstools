import json
import hashlib
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

    def test_label_digest_binds_exact_utf8_and_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.jsonl"
            label = self.label("é", "correction", "rater")
            raw = (json.dumps(label.to_dict(), ensure_ascii=False) + "\r\n").encode("utf-8")
            path.write_bytes(raw)
            store = LabelStore(path)
            records, digest = store.read_with_digest()
            self.assertEqual([label], records)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            self.assertEqual([label], store.read())
            self.assertEqual(([label], digest), store.read_with_digest(expected_digest=digest))
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                store.read_with_digest(expected_digest="0" * 64)
            path.write_bytes(b"")
            self.assertEqual(([], hashlib.sha256(b"").hexdigest()), store.read_with_digest())
            path.write_bytes(b"\n")
            with self.assertRaisesRegex(ValueError, "invalid label"):
                store.read()

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

    def test_label_counts_and_weights_do_not_establish_population_intervals(self):
        for copies in (1, 100):
            truth = [replace(self.label("%d-%d" % (cluster, i),
                             "correction" if cluster < 5 else "question", "human"),
                             sampling_weight=9 if cluster < 5 else 1)
                     for cluster in range(10) for i in range(copies)]
            predicted = [replace(item, label="correction", annotator_kind="deterministic",
                                 annotator_id="rule") for item in truth]
            binary = calibration_report(truth, predicted, positive_label="correction",
                                        splits=("test",))["test"]
            multi = multiclass_calibration_report(truth, predicted,
                labels=("correction", "question"), splits=("test",))["test"]
            strict = strict_multiclass_comparison_report(truth, {"rule": predicted},
                labels=("correction", "question"), splits=("test",))
            with self.subTest(copies=copies):
                self.assertEqual(.5, binary["precision"])
                self.assertEqual(.9, binary["weighted_precision"])
                self.assertEqual(10 * copies, binary["population"])
                self.assertEqual(0, multi["kappa"])
                for result in (binary, multi, strict["predictors"]["rule"]["test"]):
                    self.assertIsNone(result["agreement_interval"])
                    self.assertEqual(2, result["analysis_version"])
                    self.assertEqual("sampling_design_not_established",
                                     result["uncertainty"]["reason"])
                for result in (binary, multi["classes"]["correction"],
                               multi["classes"]["question"]):
                    self.assertIsNone(result["precision_interval"])
                    self.assertIsNone(result["recall_interval"])
                self.assertEqual(2, strict["schema_version"])

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

    def test_legacy_import_rejects_weight_overflow_before_overwriting_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            source.write_text("".join(json.dumps({
                "kind": "turn", "id": case, "label": "correction",
                "stratum_sampled": 1, "stratum_population": 10 ** 308,
            }) + "\n" for case in ("a", "b")))
            labels = root / "labels.jsonl"
            predictions = root / "predictions.jsonl"
            prior = [self.label("prior", "question", "human")]
            LabelStore(labels).write(prior)
            LabelStore(predictions).write(prior)
            before = (labels.read_bytes(), predictions.read_bytes())
            with self.assertRaisesRegex(ValueError, "weight total"):
                import_legacy_turn_labels(source, labels, predictions)
            self.assertEqual(before, (labels.read_bytes(), predictions.read_bytes()))

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

    def test_calibration_rejects_incomplete_and_misaligned_selected_cases(self):
        truth = [self.label("a", "correction", "human"),
                 self.label("b", "question", "human")]
        complete = [self.label("a", "correction", "rule"),
                    self.label("b", "question", "rule")]
        variants = [complete[:1], complete + [self.label("c", "question", "rule")]]
        for field, value in (("dataset_id", "other"), ("rubric_id", "other"),
                             ("rubric_version", 2), ("split", "calibration"),
                             ("sampling_weight", 2)):
            variants.append([complete[0], replace(complete[1], **{field: value})])
        for predicted in variants:
            with self.subTest(predicted=predicted):
                with self.assertRaises(ValueError):
                    calibration_report(truth, predicted, positive_label="correction",
                                       splits=("test",))
                with self.assertRaises(ValueError):
                    multiclass_calibration_report(truth, predicted,
                        labels=("correction", "question"), splits=("test",))
        excluded = self.label("excluded", "question", "human", "calibration")
        result = calibration_report(truth + [excluded], complete,
                                    positive_label="correction", splits=("test",))
        self.assertEqual(result["test"]["population"], 2)
        self.assertEqual(result["test"]["agreement"], 1)

    def test_sampling_weights_must_be_finite_positive_numbers(self):
        for weight in (float("nan"), float("inf"), float("-inf"), True,
                       False, 0, -1, "1", 10 ** 400):
            with self.subTest(weight=str(weight)), self.assertRaises(ValueError):
                replace(self.label("a", "correction", "human"), sampling_weight=weight)
        truth = [replace(self.label(case, "correction", "human"), sampling_weight=1e308)
                 for case in ("a", "b")]
        with self.assertRaisesRegex(ValueError, "weight total"):
            calibration_report(truth, truth, positive_label="correction", splits=("test",))

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
