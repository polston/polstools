import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.annotation import (  # noqa: E402
    _packet_fingerprint, import_annotations, predict_annotations,
    render_annotation_guide, sample_annotations, validate_prediction_artifact,
)
from retro_eval.labels import LabelStore  # noqa: E402


class AnnotationTests(unittest.TestCase):
    def fixture(self, path, source):
        payload = {
            "schema_version": 1,
            "source_system": source,
            "sessions": [{
                "session_id": "private-session-id",
                "messages": [
                    {"line": 1, "role": "assistant", "excerpt": "prior answer"},
                    {"line": 2, "role": "user", "chars": 500,
                     "excerpt": "no, change it"},
                    {"line": 3, "role": "assistant", "excerpt": "changed"},
                    {"line": 4, "role": "user", "excerpt": "why?"},
                ],
            }],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_sample_is_stratified_external_and_manifest_is_content_free(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            one, two = root / "one.json", root / "two.json"
            self.fixture(one, "one")
            self.fixture(two, "two")
            sample = root / "heldout.csv"
            manifest = root / "manifest.json"
            result = sample_annotations((one, two), sample, manifest, per_source=2)
            with sample.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            public = manifest.read_text(encoding="utf-8")
        self.assertEqual({"one": 2, "two": 2}, result["source_counts"])
        self.assertEqual(4, len(rows))
        self.assertTrue(all(row["split"] == "test" for row in rows))
        self.assertEqual(2, sum(row["user_turn_chars"] == "500" for row in rows))
        self.assertTrue(all(int(row["context_chars"]) > 0 for row in rows))
        self.assertNotIn("private-session-id", public)
        self.assertNotIn("no, change it", public)
        self.assertTrue(all(row["human_label"] == "" for row in rows))

    def test_packet_and_predictions_are_bound_to_one_annotation_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extract = root / "source.json"
            self.fixture(extract, "one")
            sample = root / "heldout.csv"
            sample_manifest = root / "heldout-manifest.json"
            protocol = {
                "id": "dominant-intent", "version": 2,
                "sha256": "c" * 64,
            }
            sample_annotations(
                (extract,), sample, sample_manifest, per_source=1,
                annotation_protocol=protocol)
            predictions = root / "rule.jsonl"
            prediction_manifest = root / "rule-manifest.json"
            predict_annotations(
                sample, predictions, manifest_path=sample_manifest,
                prediction_manifest_path=prediction_manifest,
                allowed_labels=("correction", "question", "none"),
                predictor=lambda row: "none", predictor_id="fixture-rule-v1",
                created_commit="b" * 40, predictor_config="fixture:rule")
            sample_data = json.loads(sample_manifest.read_text(encoding="utf-8"))
            prediction_data = json.loads(prediction_manifest.read_text(encoding="utf-8"))
        expected = {
            "annotation_protocol_id": "dominant-intent",
            "annotation_protocol_version": 2,
            "annotation_protocol_sha256": "c" * 64,
        }
        self.assertEqual(expected, {key: sample_data[key] for key in expected})
        self.assertEqual(expected, {key: prediction_data[key] for key in expected})

    def test_human_guide_is_rendered_from_the_protocol(self):
        class Protocol:
            id = "dominant-intent"
            version = 2
            sha256 = "d" * 64
            decision_order = ("correction", "none")
            definitions = {"correction": "Prior work changes.",
                           "none": "No prior category applies."}
            human_instruction = "What is the user doing with this reply?"
            label_prompts = {
                "correction": {"action": "Change or fix", "detail": "Prior work changes."},
                "none": {"action": "Do something else",
                         "detail": "No prior category applies."},
            }
            tie_breaks = ("Correction wins over none.",)

        guide = render_annotation_guide(Protocol())
        self.assertIn("dominant-intent v2", guide)
        self.assertIn("`correction`", guide)
        self.assertIn("Correction wins over none.", guide)
        self.assertIn("d" * 64, guide)

    def test_import_requires_allowed_human_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "annotations.csv"
            with source.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=(
                    "case_id", "source", "split", "context", "user_turn",
                    "human_label", "notes"))
                writer.writeheader()
                writer.writerow({"case_id": "a", "source": "one", "split": "test",
                                 "context": "prior", "user_turn": "no", "human_label": "correction",
                                 "notes": ""})
            target = root / "labels.jsonl"
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1, "dataset_id": "custom-heldout-v3",
                "rubric_id": "turn_friction_legacy", "rubric_version": 3,
                "split": "test", "sample_sha256": _packet_fingerprint(source),
            }), encoding="utf-8")
            result = import_annotations(
                source, target, manifest_path=manifest,
                rubric_id="turn_friction_legacy", rubric_version=3,
                allowed_labels=("correction", "question"))
            labels = LabelStore(target).read()
        self.assertEqual(1, result["imported"])
        self.assertEqual("human", labels[0].annotator_kind)
        self.assertEqual("correction", labels[0].label)
        self.assertEqual("custom-heldout-v3", labels[0].dataset_id)

    def test_import_rejects_packet_changed_after_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "annotations.csv"
            source.write_text("case_id,source,split,context,user_turn,human_label,notes\n",
                              encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1, "dataset_id": "heldout-v1",
                "rubric_id": "turn_friction_legacy", "rubric_version": 1,
                "split": "test", "sample_sha256": "0" * 64,
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                import_annotations(
                    source, root / "labels.jsonl", manifest_path=manifest,
                    rubric_id="turn_friction_legacy", rubric_version=1,
                    allowed_labels=("none",))

    def test_prediction_is_injected_and_content_free(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extract = root / "source.json"
            self.fixture(extract, "one")
            sample = root / "heldout.csv"
            sample_manifest = root / "heldout-manifest.json"
            sample_annotations((extract,), sample, sample_manifest, per_source=2,
                               dataset_id="custom-v1", rubric_id="turn_friction_legacy",
                               rubric_version=7, split="calibration")
            predictions = root / "rule.jsonl"
            prediction_manifest = root / "rule-manifest.json"
            result = predict_annotations(
                sample, predictions, manifest_path=sample_manifest,
                prediction_manifest_path=prediction_manifest,
                allowed_labels=("correction", "question", "none"),
                predictor=lambda row: "correction" if "change" in row["user_turn"]
                else "question", predictor_id="fixture-rule-v2",
                created_commit="a" * 40, predictor_config="fixture:rule")
            records = LabelStore(predictions).read()
            public = predictions.read_text(encoding="utf-8")
            generated_manifest = json.loads(prediction_manifest.read_text(encoding="utf-8"))
        self.assertEqual(2, result["predicted"])
        self.assertEqual("custom-v1", records[0].dataset_id)
        self.assertEqual(7, records[0].rubric_version)
        self.assertTrue(all(record.split == "calibration" for record in records))
        self.assertEqual("fixture-rule-v2", generated_manifest["predictor_id"])
        self.assertEqual("a" * 40, generated_manifest["created_commit"])
        self.assertEqual(64, len(generated_manifest["predictor_config_sha256"]))
        self.assertNotIn("no, change it", public)

    def test_prediction_manifest_is_enforced_not_decorative(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extract = root / "source.json"
            self.fixture(extract, "one")
            sample = root / "heldout.csv"
            sample_manifest = root / "heldout-manifest.json"
            sample_annotations((extract,), sample, sample_manifest, per_source=1)
            predictions = root / "rule.jsonl"
            prediction_manifest = root / "rule-manifest.json"
            predict_annotations(
                sample, predictions, manifest_path=sample_manifest,
                prediction_manifest_path=prediction_manifest,
                allowed_labels=("correction", "question", "none"),
                predictor=lambda row: "none", predictor_id="fixture-rule-v1",
                created_commit="b" * 40, predictor_config="fixture:rule")
            self.assertEqual(1, validate_prediction_artifact(
                predictions, prediction_manifest,
                sample_manifest_path=sample_manifest)["population"])
            predictions.write_text(predictions.read_text(encoding="utf-8") + "\n",
                                   encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                validate_prediction_artifact(
                    predictions, prediction_manifest,
                    sample_manifest_path=sample_manifest)


if __name__ == "__main__":
    unittest.main()
