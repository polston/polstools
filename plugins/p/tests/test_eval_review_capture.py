"""Review displays and routing consume their validated packet capture."""

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval import review_dashboard, review_workflow  # noqa: E402
from retro_eval.annotation import _packet_fingerprint  # noqa: E402
from retro_eval.taxonomy_packets import TAXONOMY_FIELDS  # noqa: E402


class ReviewCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "packet.csv"
        self.manifest = self.root / "packet-manifest.json"
        self.row = {"case_id": "original", "context": "Original é evidence",
                    "assessment": "", "human_label": ""}
        self.write_rows([self.row])
        self.metadata = {
            "schema_version": 1, "dataset_id": "authored-test",
            "rubric_id": "duplicate_work", "rubric_version": 2,
            "split": "calibration", "adaptive_sampling": {"round": 1},
            "sample_sha256": _packet_fingerprint(self.source)}
        self.manifest.write_text(json.dumps(self.metadata))

    def write_rows(self, rows):
        with self.source.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=TAXONOMY_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def substitute(self, module):
        original = module._load_packet

        def replace(*args, **kwargs):
            captured = original(*args, **kwargs)
            self.write_rows([{**self.row, "case_id": "replacement-%s" % index,
                              "context": "Unvalidated", "assessment": "correct"}
                             for index in range(2)])
            self.manifest.write_text(json.dumps({**self.metadata, "split": "test"}))
            return captured
        return patch.object(module, "_load_packet", side_effect=replace)

    def test_dashboard_uses_captured_manifest_and_rows(self):
        with self.substitute(review_dashboard):
            manifest, rows = review_dashboard._read_packet(self.source, self.manifest)
        self.assertEqual("calibration", manifest["split"])
        self.assertEqual(["original"], [row["case_id"] for row in rows])

    def test_workflow_does_not_complete_from_replacement_rows(self):
        with self.substitute(review_workflow):
            state = review_workflow.review_status(self.root, include_taxonomies=True)
        self.assertFalse(state["phase_completed"])
        self.assertEqual(1, state["remaining_cases"])
        self.assertEqual({"completed": 0, "total": 1},
                         state["next_packet"]["progress"])

    def test_workflow_phase_uses_validated_manifest(self):
        original = review_workflow._load_packet

        def replace_before_capture(*args, **kwargs):
            self.manifest.write_text(json.dumps({**self.metadata, "split": "test"}))
            return original(*args, **kwargs)

        with patch.object(review_workflow, "_load_packet", side_effect=replace_before_capture):
            self.assertEqual([], review_workflow._packet_states(self.root, "calibration"))


if __name__ == "__main__":
    unittest.main()
