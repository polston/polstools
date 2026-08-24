"""One stateful entrypoint starts the next taxonomy review packet."""

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.annotation import _packet_fingerprint  # noqa: E402
from retro_eval.review_workflow import (  # noqa: E402
    DEFAULT_REVIEW_PORT, review_status,
)
from retro_eval.taxonomy_packets import TAXONOMY_FIELDS  # noqa: E402


class ReviewWorkflowTests(unittest.TestCase):
    def test_review_uri_uses_one_stable_loopback_port(self):
        self.assertEqual(8123, DEFAULT_REVIEW_PORT)
        skill = (PLUGIN_ROOT / "skills" / "reviewing-evaluation-taxonomies"
                 / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("http://127.0.0.1:8123/", skill)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def packet(self, stem, *, rubric, split, assessments=("", ""), round_number=1):
        source = self.root / (stem + ".csv")
        rows = []
        for index, assessment in enumerate(assessments):
            rows.append({
                "case_id": "%s-%d" % (stem, index), "source": "fixture",
                "split": split, "context_chars": "1", "user_turn_chars": "1",
                "context": "before", "user_turn": "after",
                "proposed_label": "polling" if rubric == "duplicate_work"
                else "execution_failure",
                "proposal_reason": "fixture", "assessment": assessment,
                "human_label": "polling" if assessment == "correct" else "",
                "notes": "",
            })
        with source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=TAXONOMY_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        manifest = {
            "schema_version": 1, "dataset_id": "fixture",
            "rubric_id": rubric,
            "rubric_version": 3 if rubric == "duplicate_work" else 2,
            "split": split, "sample_sha256": _packet_fingerprint(source),
            "adaptive_sampling": {"round": round_number},
        }
        path = self.root / (stem + "-manifest.json")
        path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_default_selects_first_incomplete_calibration_and_hides_heldout(self):
        self.packet("duplicate-work-calibration-round1", rubric="duplicate_work",
                    split="calibration", assessments=("correct", ""))
        self.packet("duplicate-work-test-round1", rubric="duplicate_work",
                    split="test")
        self.packet("tool-failure-calibration-round1", rubric="tool_failure_kind",
                    split="calibration")

        status = review_status(self.root)

        self.assertEqual("duplicate-work-calibration-round1.csv",
                         status["next_packet"]["source_name"])
        self.assertEqual({"completed": 1, "total": 2},
                         status["next_packet"]["progress"])
        self.assertEqual(2, status["packet_count"])

    def test_mixed_interpretation_calibration_precedes_taxonomy_packets(self):
        source = self.root / "mixed-interpretation-calibration-round1.csv"
        fields = TAXONOMY_FIELDS + (
            "review_kind", "situation_summary", "interpretation",
            "rationale", "expected_action")
        row = {field: "" for field in fields}
        row.update({"case_id": "mixed-1", "source": "fixture",
                    "split": "calibration", "context": "before",
                    "user_turn": "after", "review_kind": "user_understanding",
                    "situation_summary": "The agent asked a question.",
                    "interpretation": "The user corrected it.",
                    "rationale": "The response replaces the prior premise.",
                    "expected_action": "Correct the work.",
                    "proposed_label": "", "proposal_reason": ""})
        with source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerow(row)
        manifest = {
            "schema_version": 1, "dataset_id": "mixed-fixture",
            "rubric_id": "interpretation_grounding", "rubric_version": 1,
            "split": "calibration", "sample_sha256": _packet_fingerprint(source),
            "review_round": 1,
        }
        (self.root / "mixed-interpretation-calibration-round1-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")
        self.packet("duplicate-work-calibration-round1", rubric="duplicate_work",
                    split="calibration")

        status = review_status(self.root)

        self.assertEqual("mixed-interpretation-calibration-round1.csv",
                         status["next_packet"]["source_name"])

    def test_heldout_requires_explicit_phase_and_resumes_first_open_case(self):
        self.packet("duplicate-work-test-round1", rubric="duplicate_work",
                    split="test", assessments=("unsure", ""))
        self.packet("tool-failure-test-round1", rubric="tool_failure_kind",
                    split="test")

        status = review_status(self.root, phase="heldout")

        self.assertEqual("duplicate-work-test-round1.csv",
                         status["next_packet"]["source_name"])
        self.assertEqual(1, status["remaining_cases"])

    def test_complete_phase_reports_no_next_packet(self):
        self.packet("duplicate-work-calibration-round1", rubric="duplicate_work",
                    split="calibration", assessments=("correct", "unsure"))

        status = review_status(self.root)

        self.assertIsNone(status["next_packet"])
        self.assertEqual(0, status["remaining_cases"])

    def test_packet_fingerprint_drift_is_rejected(self):
        self.packet("duplicate-work-calibration-round1", rubric="duplicate_work",
                    split="calibration")
        source = self.root / "duplicate-work-calibration-round1.csv"
        source.write_text(source.read_text(encoding="utf-8") + "changed", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "fingerprint"):
            review_status(self.root)

    def test_skill_routes_the_whole_process_through_one_entrypoint(self):
        skill = (PLUGIN_ROOT / "skills" / "reviewing-evaluation-taxonomies"
                 / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("retro-eval-review status", skill)
        self.assertIn("retro-eval-review serve-next", skill)
        self.assertIn("mixed interpretation", skill.lower())
        self.assertIn("Accurate", skill)
        self.assertIn("--phase heldout", skill)
        self.assertNotIn("retro-eval-labels serve", skill)


if __name__ == "__main__":
    unittest.main()
