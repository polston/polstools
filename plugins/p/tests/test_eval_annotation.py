import csv
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch


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

    def test_overlapping_extracts_deduplicate_before_sampling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first.json", root / "second.json"
            self.fixture(first, "shared")
            second.write_bytes(first.read_bytes())
            output, manifest = root / "sample.csv", root / "manifest.json"
            sample_annotations((first,), output, manifest, per_source=2)
            original = output.read_bytes()
            for index, inputs in enumerate(((first, second), (second, first), (first, first))):
                with self.subTest(inputs=tuple(p.name for p in inputs)):
                    output = root / ("sample-%s.csv" % index)
                    manifest = root / ("manifest-%s.json" % index)
                    result = sample_annotations(inputs, output, manifest, per_source=2)
                    self.assertEqual(output.read_bytes(), original)
                    self.assertEqual(result["source_counts"], {"shared": 2})
                    self.assertEqual(len(result["input_artifacts"]), 2)
                    predicted = root / "predictions.jsonl"
                    predict_annotations(output, predicted, manifest_path=manifest,
                        prediction_manifest_path=root / "predictions-manifest.json",
                        allowed_labels=["none"], predictor=lambda row: "none",
                        predictor_id="authored", predictor_config="authored",
                        created_commit="a" * 40)
                    self.assertEqual(len(LabelStore(predicted).read()), 2)

    def test_unknown_or_ambiguous_extract_identity_cannot_be_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mode in ("missing_session", "missing_line", "delimiter_collision"):
                with self.subTest(mode=mode):
                    sessions = [{"session_id": "authored", "messages": [
                        {"line": 1, "role": "user", "excerpt": "Same text."},
                        {"line": 1, "role": "user", "excerpt": "Same text."}]}]
                    if mode == "missing_session":
                        del sessions[0]["session_id"]
                    elif mode == "missing_line":
                        for message in sessions[0]["messages"]:
                            del message["line"]
                    else:
                        sessions = [{"session_id": session, "messages": [
                            {"line": line, "role": "user", "excerpt": "Same text."}]}
                            for session, line in (("a|b", "c"), ("a", "b|c"))]
                    extract = root / (mode + ".json")
                    extract.write_text(json.dumps({"source_system": "authored", "sessions": sessions}))
                    output = root / mode
                    with self.assertRaisesRegex(ValueError, "identity"):
                        sample_annotations((extract,), output / "sample.csv",
                                           output / "manifest.json", per_source=10)
                    self.assertFalse(output.exists())

    def test_conflicting_extract_identity_refuses_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first.json", root / "second.json"
            self.fixture(first, "shared")
            for field, index in (("excerpt", 1), ("excerpt", 0), ("chars", 1)):
                for existing in (False, True):
                    with self.subTest(field=field, index=index, existing=existing):
                        payload = json.loads(first.read_text())
                        payload["sessions"][0]["messages"][index][field] = (
                            999 if field == "chars" else "Conflicting authored content.")
                        second.write_text(json.dumps(payload))
                        target = root / (field + str(index) + str(existing))
                        output, manifest = target / "sample.csv", target / "manifest.json"
                        if existing:
                            sample_annotations((first,), output, manifest, per_source=1)
                        before = {p.name: p.read_bytes() for p in target.iterdir()} if target.exists() else {}
                        expected = "destination already exists" if existing else "conflicting.*case"
                        with self.assertRaisesRegex(ValueError, expected):
                            sample_annotations((first, second), output, manifest, per_source=1)
                        after = {p.name: p.read_bytes() for p in target.iterdir()} if target.exists() else {}
                        self.assertEqual(after, before)
                        self.assertEqual(target.exists(), existing)

    def test_external_extract_hash_matches_parsed_bytes_after_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extract = root / "extract.json"
            self.fixture(extract, "fixture")
            raw = extract.read_bytes()
            text_reader, bytes_reader = Path.read_text, Path.read_bytes
            fired = []
            def capture_then_replace(reader):
                def read(path, *args, **kwargs):
                    result = reader(path, *args, **kwargs)
                    if path.resolve() == extract.resolve() and not fired:
                        replacement = root / "replacement.json"
                        replacement.write_text('{"source_system":"replacement","sessions":[]}')
                        replacement.replace(extract)
                        fired.append(True)
                    return result
                return read
            with patch.object(Path, "read_text", capture_then_replace(text_reader)), \
                    patch.object(Path, "read_bytes", capture_then_replace(bytes_reader)):
                manifest = sample_annotations(
                    (extract,), root / "sample.csv", root / "manifest.json", per_source=1)
            self.assertTrue(fired)
            digest = hashlib.sha256(raw).hexdigest()
            self.assertEqual({"fixture": digest}, manifest["input_fingerprints"])
            self.assertEqual([{"source": "fixture", "sha256": digest}], manifest["input_artifacts"])

    def test_external_extract_provenance_keeps_every_contributing_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second, third = (root / name for name in ("first.json", "second.json", "third.json"))
            self.fixture(first, "shared")
            payload = json.loads(first.read_text())
            payload["sessions"][0]["session_id"] = "another-fixture"
            second.write_text(json.dumps(payload))
            self.fixture(third, "other")
            inputs = (first, second, third)
            manifest = sample_annotations(inputs, root / "sample.csv", root / "manifest.json", per_source=4)
            expected = [{"source": source, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                        for source, path in zip(("shared", "shared", "other"), inputs)]
            self.assertEqual(expected, manifest["input_artifacts"])
            self.assertEqual({"other": expected[2]["sha256"]}, manifest["input_fingerprints"])
            self.assertEqual({"other": 2, "shared": 4}, manifest["source_counts"])

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

    def test_annotation_consumers_use_the_packet_rows_they_verified(self):
        for consumer in ("import", "predict"):
            with self.subTest(consumer=consumer), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, manifest = root / "sample.csv", root / "sample.json"
                fields = ["case_id", "source", "split", "context", "user_turn",
                          "human_label", "notes"]
                row = dict(case_id="case", source="fixture", split="test",
                           context="line one\nline two", user_turn="original",
                           human_label="none", notes="")
                def write_packet(path, value):
                    with path.open("w", newline="") as handle:
                        writer = csv.DictWriter(handle, fieldnames=fields)
                        writer.writeheader()
                        writer.writerow(value)
                write_packet(source, row)
                manifest.write_text(json.dumps(dict(
                    schema_version=1, dataset_id="fixture", rubric_id="turn_friction_legacy",
                    rubric_version=1, split="test", sample_sha256=_packet_fingerprint(source))))
                replacement = root / "replacement.csv"
                write_packet(replacement, dict(row, user_turn="changed", human_label="correction"))
                original_open = Path.open
                fired = []
                @contextlib.contextmanager
                def replace_after_read(path, *args, **kwargs):
                    with original_open(path, *args, **kwargs) as handle:
                        yield handle
                    mode = args[0] if args else kwargs.get("mode", "r")
                    if path.resolve() == source.resolve() and "r" in mode and not fired:
                        replacement.replace(source)
                        fired.append(True)
                target = root / "labels.jsonl"
                with patch.object(Path, "open", replace_after_read):
                    if consumer == "import":
                        result = import_annotations(
                            source, target, manifest_path=manifest,
                            rubric_id="turn_friction_legacy", rubric_version=1,
                            allowed_labels=("none", "correction"))
                    else:
                        result = predict_annotations(
                            source, target, manifest_path=manifest,
                            prediction_manifest_path=root / "predictions.json",
                            allowed_labels=("none", "correction"),
                            predictor=lambda value: "correction" if value["user_turn"] == "changed" else "none",
                            predictor_id="fixture", created_commit="a" * 40,
                            predictor_config="fixture")
                self.assertTrue(fired)
                self.assertEqual("none", LabelStore(target).read()[0].label)
                if consumer == "import":
                    self.assertEqual({"imported": 1, "available_rows": 1}, result)

    def test_predictor_cannot_read_annotations_or_mutate_packet_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extract = root / "source.json"
            self.fixture(extract, "one")
            sample, manifest = root / "sample.csv", root / "sample.json"
            sample_annotations((extract,), sample, manifest, per_source=1)
            with sample.open(newline="") as handle:
                reader = csv.DictReader(handle)
                fields, rows = list(reader.fieldnames), list(reader)
            fields += ["assessment", "feature"]
            rows[0]["feature"] = "immutable input"
            payload = json.loads(manifest.read_text())
            predictions = []
            for annotated in (False, True):
                rows[0].update(human_label="correction" if annotated else "",
                               notes="answer: correction" if annotated else "",
                               assessment="correct" if annotated else "")
                with sample.open("w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
                if not annotated:
                    payload["sample_sha256"] = _packet_fingerprint(sample)
                    manifest.write_text(json.dumps(payload))
                seen = []
                def predictor(row):
                    seen.append(dict(row))
                    label = row.get("human_label") or "none"
                    row["split"] = "calibration"
                    return label
                target = root / ("prediction-%s.jsonl" % annotated)
                predict_annotations(
                    sample, target, manifest_path=manifest,
                    prediction_manifest_path=root / ("prediction-%s.json" % annotated),
                    allowed_labels=("correction", "none"), predictor=predictor,
                    predictor_id="fixture", created_commit="a" * 40,
                    predictor_config="fixture")
                self.assertFalse({"human_label", "notes", "assessment"} & set(seen[0]))
                self.assertEqual("immutable input", seen[0]["feature"])
                self.assertEqual(rows[0]["user_turn"], seen[0]["user_turn"])
                records = LabelStore(target).read()
                self.assertEqual("test", records[0].split)
                predictions.append(records)
            self.assertEqual(predictions[0], predictions[1])
            self.assertEqual("none", predictions[0][0].label)

    def test_comparison_scores_only_the_prediction_capture_it_validates(self):
        from retro_eval.label_cli import main
        for bind_replacement in (False, True):
            with self.subTest(bind_replacement=bind_replacement), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                extract = root / "source.json"
                self.fixture(extract, "one")
                sample, sample_manifest = root / "sample.csv", root / "sample.json"
                sample_annotations((extract,), sample, sample_manifest, per_source=1)
                predictions, manifest = root / "rule.jsonl", root / "rule.json"
                predict_annotations(
                    sample, predictions, manifest_path=sample_manifest,
                    prediction_manifest_path=manifest,
                    allowed_labels=("correction", "question", "none"),
                    predictor=lambda row: "none", predictor_id="fixture-rule-v1",
                    created_commit="b" * 40, predictor_config="fixture:rule")
                records = LabelStore(predictions).read()
                truth = root / "truth.jsonl"
                LabelStore(truth).write([
                    replace(record, annotator_kind="human", annotator_id="rater")
                    for record in records])
                replacement = root / "replacement.jsonl"
                LabelStore(replacement).write([
                    replace(record, label="correction") for record in records])
                if bind_replacement:
                    payload = json.loads(manifest.read_text())
                    payload["predictions_sha256"] = hashlib.sha256(replacement.read_bytes()).hexdigest()
                    manifest.write_text(json.dumps(payload))
                original_text, original_bytes = Path.read_text, Path.read_bytes
                fired = []
                def capture_then_replace(reader):
                    def read(path, *args, **kwargs):
                        data = reader(path, *args, **kwargs)
                        if path.resolve() == predictions.resolve() and not fired:
                            replacement.replace(predictions)
                            fired.append(True)
                        return data
                    return read
                output = root / "report.json"
                output.write_text("prior report")
                with patch.object(Path, "read_text", capture_then_replace(original_text)), \
                        patch.object(Path, "read_bytes", capture_then_replace(original_bytes)), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    code = main([
                        "compare", "--labels", str(truth),
                        "--predictions", "rule=" + str(predictions),
                        "--prediction-manifest", "rule=" + str(manifest),
                        "--sample-manifest", str(sample_manifest),
                        "--output", str(output)])
                self.assertTrue(fired)
                if bind_replacement:
                    self.assertEqual(2, code)
                    self.assertEqual("prior report", output.read_text())
                else:
                    self.assertEqual(0, code)
                    report = json.loads(output.read_text())
                    self.assertEqual(1.0, report["predictors"]["rule"]["test"]["agreement"])

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
