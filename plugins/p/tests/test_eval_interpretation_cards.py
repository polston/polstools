"""Plain-language mixed review cards stay external and calibration-only."""

import csv
import json
import sys
import tempfile
import unittest
import subprocess
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.annotation_ui import AnnotationWorkspace  # noqa: E402
from retro_eval.interpretation_cards import (  # noqa: E402
    INTERPRETATION_FIELDS, write_interpretation_review,
)


def card(kind="user_understanding", **overrides):
    value = {
        "stable_key": "case-one", "source": "fixture", "review_kind": kind,
        "context": "The agent offered two configuration choices.",
        "user_turn": "Use the first one and continue.",
        "situation_summary": "The agent asked which configuration to use.",
        "interpretation": "You selected the first option and approved continuing.",
        "rationale": "The reply chooses an option and adds no new requirement.",
        "expected_action": "Apply the first option and continue without another question.",
    }
    value.update(overrides)
    return value


class InterpretationCardTests(unittest.TestCase):
    def test_cli_builds_external_packet_from_authored_json(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            evidence = root / "evidence.csv"
            with evidence.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=(
                    "case_id", "source", "context", "user_turn"))
                writer.writeheader(); writer.writerow({
                    "case_id": "source-one", "source": "fixture",
                    "context": "The agent offered two configuration choices.",
                    "user_turn": "Use the first one and continue."})
            authored = root / "cards.json"
            value = card()
            value.pop("context"); value.pop("user_turn"); value.pop("source")
            value.update({"evidence_source": str(evidence),
                          "evidence_case_id": "source-one"})
            authored.write_text(json.dumps({"cards": [value]}), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(PLUGIN_ROOT / "bin" / "retro-eval-interpretations"),
                "build", "--cards", str(authored), "--output-dir", str(root / "out")],
                text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((root / "out" /
                             "mixed-interpretation-calibration-round1.csv").exists())
            with (root / "out" /
                  "mixed-interpretation-calibration-round1.csv").open(
                      "r", encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual("Use the first one and continue.", row["user_turn"])

    def test_writes_protocol_bound_mixed_packet_and_accepts_an_assessment(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "mixed-interpretation-calibration-round1.csv"
            manifest = root / "mixed-interpretation-calibration-round1-manifest.json"
            write_interpretation_review(
                [card(), card("agent_judgment", stable_key="case-two",
                              user_turn="The same test passed after an edit.",
                              situation_summary="A test failed, code changed, and the test passed.",
                              interpretation="The rerun was legitimate verification.",
                              rationale="A relevant mutation occurred between the two runs.",
                              expected_action="Classify the repeat as verification after a change.")],
                source, manifest)
            with source.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
                self.assertEqual(INTERPRETATION_FIELDS,
                                 tuple(handle and rows[0].keys()))
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual("interpretation_grounding", metadata["rubric_id"])
            self.assertEqual("passed", metadata["review_quality"]["status"])
            self.assertEqual({"agent_judgment": 1, "user_understanding": 1},
                             metadata["review_kind_counts"])

            workspace = AnnotationWorkspace(
                source=source, manifest_path=manifest,
                rubrics_path=PLUGIN_ROOT / "rubrics" / "rubrics.json",
                protocols_path=PLUGIN_ROOT / "rubrics" / "annotation-protocols.json")
            state = workspace.snapshot()
            self.assertEqual("user_understanding",
                             state["cases"][0]["review_kind"])
            updated = workspace.update(
                case_id=state["cases"][0]["case_id"], label="accurate",
                assessment="accurate", notes="",
                expected_revision=state["revision"])
            self.assertEqual("accurate", updated["cases"][0]["assessment"])

    def test_rejects_raw_tool_wrappers_in_plain_language_fields(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaisesRegex(ValueError, "plain-language"):
                write_interpretation_review(
                    [card(situation_summary="const r = await tools.exec()")],
                    root / "cards.csv", root / "cards-manifest.json")


if __name__ == "__main__":
    unittest.main()
