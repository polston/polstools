"""Taxonomy support assessments use the validated packet snapshot."""

import contextlib
import csv
import io
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval import taxonomy_packets_cli as cli  # noqa: E402
from retro_eval.annotation import _packet_fingerprint  # noqa: E402
from retro_eval.catalog import load_rubric_catalogue, load_annotation_protocol_catalogue  # noqa: E402
from retro_eval.taxonomy_packets import TAXONOMY_FIELDS  # noqa: E402


class TaxonomyAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "packet.csv"
        self.manifest = self.root / "packet-manifest.json"
        self.rubric = next(rubric for rubric in load_rubric_catalogue(
            PLUGIN_ROOT / "rubrics" / "rubrics.json").rubrics
            if rubric.id == "duplicate_work")

    def write_rows(self, labels):
        with self.source.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=TAXONOMY_FIELDS)
            writer.writeheader()
            writer.writerows({"case_id": "case-%s" % index,
                              "context": "Observed context é",
                              "human_label": label}
                             for index, label in enumerate(labels))

    def packet(self, labels, round_number=1):
        self.write_rows(labels)
        self.manifest.write_text(json.dumps({
            "schema_version": 1, "dataset_id": "authored-test",
            "rubric_id": self.rubric.id, "rubric_version": self.rubric.version,
            "sample_sha256": _packet_fingerprint(self.source),
            "adaptive_sampling": {"round": round_number}}))

    def assess(self, priors=(), criteria=False):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            args = ["assess", "--source", str(self.source), "--manifest", str(self.manifest)]
            if criteria:
                args.append("--criteria")
            for source, manifest in priors:
                args.extend(["--prior-packet", str(source), str(manifest)])
            code = cli.main(args)
        return code, json.loads(output.getvalue()) if output.getvalue() else None, error.getvalue()

    def supported_labels(self):
        return [label for label in self.rubric.extensions[
            "adaptive_sampling"]["support_labels"] for _ in range(20)]

    def cumulative_pair(self):
        protocol = next(item for item in load_annotation_protocol_catalogue(
            PLUGIN_ROOT / "rubrics" / "annotation-protocols.json",
            load_rubric_catalogue(PLUGIN_ROOT / "rubrics" / "rubrics.json")).protocols
            if item.rubric_id == self.rubric.id and item.rubric_version == self.rubric.version)
        labels = [label for label in self.rubric.extensions["adaptive_sampling"]["support_labels"]
                  for _ in range(10)]
        prior = (self.root / "prior.csv", self.root / "prior-manifest.json")
        for number in (1, 2):
            self.packet(labels, number)
            with self.source.open(newline="") as handle:
                reader = csv.DictReader(handle)
                fields, rows = reader.fieldnames, list(reader)
            for index, row in enumerate(rows):
                row.update(split="test", case_id="round-%s-case-%s" % (number, index))
            with self.source.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader(); writer.writerows(rows)
            manifest = json.loads(self.manifest.read_text())
            manifest.update(split="test", split_policy="source_trace_v1",
                candidate_taxonomy_version=6,
                annotation_protocol_id=protocol.id, annotation_protocol_version=protocol.version,
                annotation_protocol_sha256=protocol.sha256,
                input_fingerprints={"normalized_traces": "a" * 64},
                candidate_sha256=[hashlib.sha256(("%s:%s" % (number, i)).encode()).hexdigest()
                                  for i in range(len(rows))],
                sample_sha256=_packet_fingerprint(self.source))
            self.manifest.write_text(json.dumps(manifest))
            if number == 1:
                prior[0].write_bytes(self.source.read_bytes())
                prior[1].write_bytes(self.manifest.read_bytes())
        return prior

    def bind_proposals(self, source, manifest):
        with source.open(newline="") as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        for row in rows:
            row["proposed_label"] = row["human_label"]
        with source.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)
        data = json.loads(manifest.read_text())
        data["sample_sha256"] = _packet_fingerprint(source)
        manifest.write_text(json.dumps(data))

    def test_packet_criteria_are_actionable_but_never_promotion(self):
        prior = self.cumulative_pair()
        for pair in (prior, (self.source, self.manifest)):
            self.bind_proposals(*pair)
        code, result, error = self.assess([prior], criteria=True)
        self.assertEqual(0, code, error)
        self.assertTrue(result["criteria_met"])
        self.assertFalse(result["decision_support"])
        self.assertEqual(100, result["labelled"])
        self.assertEqual("test", result["split"])
        self.assertEqual(1, result["criteria"]["precision"])
        self.assertTrue(result["next_action"])
        self.assertEqual([1, 2], [item["round"] for item in result["packet_bindings"]])
        # Enough labeled support does not excuse an additional unreviewed case.
        with self.source.open(newline="") as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        rows.append({**rows[0], "case_id": "extra", "human_label": ""})
        with self.source.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)
        data = json.loads(self.manifest.read_text())
        data["candidate_sha256"].append(hashlib.sha256(b"extra").hexdigest())
        data["sample_sha256"] = _packet_fingerprint(self.source)
        self.manifest.write_text(json.dumps(data))
        code, result, error = self.assess([prior], criteria=True)
        self.assertEqual(1, code, error)
        self.assertFalse(result["criteria_met"])
        self.assertEqual(1, result["unlabelled"])
        self.assertTrue(result["criteria"]["thresholds_met"])
        self.assertFalse(result["decision_support"])

    def test_criteria_rejects_unbound_and_unsupported_proposals(self):
        self.packet(self.supported_labels())
        self.assertEqual(2, self.assess(criteria=True)[0])
        prior = self.cumulative_pair()
        for pair in (prior, (self.source, self.manifest)):
            self.bind_proposals(*pair)
        self.assertEqual(2, self.assess(criteria=True)[0])  # missing prior round
        with self.source.open(newline="") as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        rows[0]["proposed_label"] = "unsupported"
        with self.source.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)
        self.assertEqual(2, self.assess([prior], criteria=True)[0])  # changed fingerprint
        data = json.loads(self.manifest.read_text())
        data["sample_sha256"] = _packet_fingerprint(self.source)
        self.manifest.write_text(json.dumps(data))
        code, _, error = self.assess([prior], criteria=True)
        self.assertEqual(2, code)
        self.assertIn("proposed", error)

    def test_criteria_next_action_respects_support_and_sampling_bound(self):
        prior = self.cumulative_pair()
        template = json.loads(prior[1].read_text())
        labels = self.rubric.extensions["adaptive_sampling"]["support_labels"]
        maximum = self.rubric.extensions["adaptive_sampling"]["maximum_rounds"]
        packets = []
        for number in range(1, maximum + 1):
            self.source = self.root / ("small-%d.csv" % number)
            self.manifest = self.root / ("small-%d-manifest.json" % number)
            self.write_rows(labels)
            with self.source.open(newline="") as handle:
                reader = csv.DictReader(handle)
                fields, rows = reader.fieldnames, list(reader)
            for index, row in enumerate(rows):
                row.update(split="test", case_id="%d-%d" % (number, index),
                           proposed_label=row["human_label"])
            with self.source.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader(); writer.writerows(rows)
            data = {**template, "adaptive_sampling": {"round": number},
                    "candidate_sha256": [hashlib.sha256(row["case_id"].encode()).hexdigest()
                                         for row in rows],
                    "sample_sha256": _packet_fingerprint(self.source)}
            self.manifest.write_text(json.dumps(data))
            code, result, error = self.assess(packets, criteria=True)
            self.assertEqual(1, code, error)
            self.assertEqual(1, result["criteria"]["precision"])
            if number < maximum:
                self.assertEqual("needs_more", result["support_status"])
                self.assertIn("next preregistered round", result["next_action"]["action"])
            else:
                self.assertEqual("insufficient_evidence", result["support_status"])
                self.assertIn("insufficient evidence", result["next_action"]["action"])
            self.assertNotIn("failed confirmation", result["next_action"]["action"])
            packets.append((self.source, self.manifest))

    def test_criteria_uses_captured_rows_after_external_replacement(self):
        prior = self.cumulative_pair()
        for pair in (prior, (self.source, self.manifest)):
            self.bind_proposals(*pair)
        expected_hash = _packet_fingerprint(self.source)
        original = cli._load_packet

        def replace_after_capture(source, *args, **kwargs):
            captured = original(source, *args, **kwargs)
            if source == self.source:
                self.source.write_text("replaced after capture\n")
            return captured

        with patch.object(cli, "_load_packet", side_effect=replace_after_capture):
            code, result, error = self.assess([prior], criteria=True)
        self.assertEqual(0, code, error)
        self.assertEqual(100, result["labelled"])
        self.assertEqual(expected_hash, result["packet_bindings"][1]["sample_sha256"])
        self.assertFalse(result["decision_support"])

    def test_cumulative_support_reaches_threshold_without_promoting(self):
        prior = self.cumulative_pair()
        paths = (*prior, self.source, self.manifest)
        before = {path: path.read_bytes() for path in paths}
        code, single, _ = self.assess()
        self.assertEqual(1, code)
        self.assertEqual("packet", single["assessment_scope"])
        code, cumulative, _ = self.assess([prior])
        self.assertEqual(0, code)
        self.assertEqual("ready", cumulative["status"])
        self.assertEqual("cumulative", cumulative["assessment_scope"])
        self.assertEqual(100, cumulative["labelled"])
        self.assertEqual([1, 2], cumulative["rounds_included"])
        self.assertEqual(0, cumulative["unlabelled"])
        self.assertFalse(cumulative["decision_support"])
        self.assertEqual(before, {path: path.read_bytes() for path in paths})

    def test_cumulative_support_rejects_mixed_or_reused_evidence(self):
        prior = self.cumulative_pair()
        original = json.loads(prior[1].read_text())
        current = json.loads(self.manifest.read_text())
        for key, value in (("split", "calibration"), ("split_policy", None),
                ("candidate_taxonomy_version", 5), ("annotation_protocol_sha256", "b" * 64),
                ("input_fingerprints", {"normalized_traces": "b" * 64}),
                ("adaptive_sampling", {"round": 2}),
                ("candidate_sha256", current["candidate_sha256"])):
            with self.subTest(key=key):
                changed = dict(original); changed[key] = value
                prior[1].write_text(json.dumps(changed))
                code, result, _ = self.assess([prior])
                self.assertEqual(2, code)
                self.assertIsNone(result)
        prior[1].write_text(json.dumps(original))
        self.assertEqual(2, self.assess([prior, prior])[0])
        current["adaptive_sampling"]["round"] = 3
        self.manifest.write_text(json.dumps(current))
        self.assertEqual(2, self.assess([prior])[0])

    def test_cumulative_support_uses_captured_prior_rows(self):
        prior = self.cumulative_pair()
        original = cli._load_packet
        def replace_after_capture(source, *args, **kwargs):
            captured = original(source, *args, **kwargs)
            if source == prior[0]:
                source.write_text("case_id,human_label\nreplacement,\n")
            return captured
        with patch.object(cli, "_load_packet", side_effect=replace_after_capture):
            code, result, _ = self.assess([prior])
        self.assertEqual(0, code)
        self.assertEqual(100, result["labelled"])

    def test_supported_partial_and_invalid_assessments(self):
        self.packet(self.supported_labels())
        code, result, _ = self.assess()
        self.assertEqual(0, code)
        self.assertEqual("ready", result["status"])
        self.packet([""])
        code, result, _ = self.assess()
        self.assertEqual(1, code)
        self.assertEqual("needs_more", result["status"])
        self.packet(self.supported_labels() + ["unregistered-label"])
        code, result, error = self.assess()
        self.assertEqual(2, code)
        self.assertIsNone(result)
        self.assertIn("unsupported taxonomy labels", error)
        for value in (True, "1", 0, 5):
            self.packet([""], value)
            self.assertEqual(2, self.assess()[0])

    def test_assessment_does_not_reopen_validated_rows_or_round(self):
        self.packet([""])
        original = cli._load_packet

        def replace_after_capture(*args, **kwargs):
            captured = original(*args, **kwargs)
            self.packet(self.supported_labels(), 4)
            return captured

        with patch.object(cli, "_load_packet", side_effect=replace_after_capture):
            code, result, _ = self.assess()
        self.assertEqual(1, code)
        self.assertEqual(0, result["labelled"])
        self.assertEqual(1, result["round"])
        self.assertEqual("needs_more", result["status"])

    def test_captured_manifest_controls_round_after_initial_rubric_lookup(self):
        self.packet([""])
        original = cli._load_packet

        def replace_before_capture(*args, **kwargs):
            self.packet([""], 4)
            return original(*args, **kwargs)

        with patch.object(cli, "_load_packet", side_effect=replace_before_capture):
            code, result, _ = self.assess()
        self.assertEqual(1, code)
        self.assertEqual(4, result["round"])
        self.assertEqual("insufficient_evidence", result["status"])


if __name__ == "__main__":
    unittest.main()
