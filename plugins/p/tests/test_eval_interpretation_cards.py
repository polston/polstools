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
    def test_snapshot_rejects_taxonomy_assessments_after_external_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, manifest = root / "cards.csv", root / "cards-manifest.json"
            write_interpretation_review([card()], source, manifest)
            workspace = AnnotationWorkspace(
                source=source, manifest_path=manifest,
                rubrics_path=PLUGIN_ROOT / "rubrics" / "rubrics.json",
                protocols_path=PLUGIN_ROOT / "rubrics" / "annotation-protocols.json")
            with source.open(newline="") as handle:
                reader = csv.DictReader(handle)
                fields, rows = reader.fieldnames, list(reader)
            for assessment in ("correct", "incorrect", "unsupported"):
                with self.subTest(assessment=assessment):
                    rows[0]["assessment"] = assessment
                    with source.open("w", newline="") as handle:
                        writer = csv.DictWriter(handle, fieldnames=fields)
                        writer.writeheader(); writer.writerows(rows)
                    before = source.read_bytes()
                    with self.assertRaisesRegex(ValueError, "assessment"):
                        workspace.snapshot()
                    self.assertEqual(before, source.read_bytes())

    def test_regeneration_preserves_completed_assessments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, manifest = root / "cards.csv", root / "cards-manifest.json"
            write_interpretation_review([card(user_turn="Continue avec cette option.")],
                                        output, manifest)
            with output.open(newline="") as handle:
                reader = csv.DictReader(handle)
                fields, rows = reader.fieldnames, list(reader)
            rows[0].update(assessment="accurate", human_label="accurate", notes="Retain review")
            with output.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            before = output.read_bytes(), manifest.read_bytes()
            with self.assertRaisesRegex(ValueError, "destination already exists"):
                write_interpretation_review([card()], output, manifest)
            self.assertEqual(before, (output.read_bytes(), manifest.read_bytes()))

    def test_invalid_destinations_preserve_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, manifest = root / "cards.csv", root / "manifest.json"
            manifest.write_bytes(b"Existing manifest")
            for source, target in ((output, manifest), (output, output),
                                   (output, root / ".annotation-salt")):
                with self.assertRaises(ValueError):
                    write_interpretation_review([card()], source, target)
            self.assertEqual(b"Existing manifest", manifest.read_bytes())
            self.assertFalse(output.exists())
            self.assertFalse((root / ".annotation-salt").exists())

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
            before = source.read_bytes()
            with self.assertRaisesRegex(ValueError, "assessment"):
                workspace.update(
                    case_id=state["cases"][0]["case_id"], label="wrong",
                    notes="Changed", expected_revision=updated["revision"])
            self.assertEqual(before, source.read_bytes())
            noted = workspace.update(
                case_id=state["cases"][0]["case_id"], label="accurate",
                notes="Explanation", expected_revision=updated["revision"])
            self.assertEqual("accurate", noted["cases"][0]["assessment"])
            self.assertEqual("Explanation", noted["cases"][0]["notes"])

    def test_rejects_raw_tool_wrappers_in_plain_language_fields(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaisesRegex(ValueError, "plain-language"):
                write_interpretation_review(
                    [card(situation_summary="const r = await tools.exec()")],
                    root / "cards.csv", root / "cards-manifest.json")


if __name__ == "__main__":
    unittest.main()
