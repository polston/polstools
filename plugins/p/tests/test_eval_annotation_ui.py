import csv
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.annotation import _packet_fingerprint, sample_annotations  # noqa: E402
from retro_eval.annotation_ui import (  # noqa: E402
    AnnotationConflict, AnnotationWorkspace, create_server,
)
from retro_eval.annotation_formatting import evidence_blocks  # noqa: E402
from retro_eval.catalog import (  # noqa: E402
    load_annotation_protocol_catalogue, load_rubric_catalogue,
)


class AnnotationWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        extract = self.root / "source.json"
        extract.write_text(json.dumps({
            "schema_version": 1,
            "source_system": "fixture",
            "sessions": [{
                "session_id": "private-session",
                "messages": [
                    {"line": 1, "role": "assistant", "excerpt": "Prior answer"},
                    {"line": 2, "role": "user", "excerpt": "No, change it"},
                    {"line": 3, "role": "assistant", "excerpt": "Changed"},
                    {"line": 4, "role": "user", "excerpt": "Why?"},
                ],
            }],
        }), encoding="utf-8")
        rubrics = load_rubric_catalogue(PLUGIN_ROOT / "rubrics" / "rubrics.json")
        self.protocol = load_annotation_protocol_catalogue(
            PLUGIN_ROOT / "rubrics" / "annotation-protocols.json", rubrics,
        ).get("turn-friction-dominant-intent", 2)
        self.packet = self.root / "heldout.csv"
        self.manifest = self.root / "heldout-manifest.json"
        sample_annotations(
            (extract,), self.packet, self.manifest, per_source=2,
            annotation_protocol=self.protocol,
        )
        self.workspace = AnnotationWorkspace(
            source=self.packet, manifest_path=self.manifest,
            rubrics_path=PLUGIN_ROOT / "rubrics" / "rubrics.json",
            protocols_path=PLUGIN_ROOT / "rubrics" / "annotation-protocols.json",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_snapshot_exposes_protocol_cases_progress_and_no_paths(self):
        state = self.workspace.snapshot()
        self.assertEqual(2, state["progress"]["total"])
        self.assertEqual(0, state["progress"]["completed"])
        self.assertEqual(self.protocol.id, state["protocol"]["id"])
        self.assertEqual(list(self.protocol.decision_order),
                         state["protocol"]["decision_order"])
        self.assertIn("What is the user doing", state["protocol"]["human_instruction"])
        self.assertEqual("Accept it and continue",
                         state["protocol"]["label_prompts"]["approval"]["action"])
        self.assertEqual({"Prior answer", "Changed"},
                         {item["context"] for item in state["cases"]})
        self.assertNotIn(str(self.root), json.dumps(state))
        self.assertTrue(state["cases"][0]["context_blocks"])

    def test_reference_style_points_are_restored_to_separate_blocks(self):
        blocks = evidence_blocks(
            "ask.1 - skip ask.2 - proceed stdin/out.4.3.6 - good "
            "3.4.2.1.1/2/3 - understood 3.4.2.1.4 - clarify")
        references = [block["reference"] for block in blocks
                      if block["type"] == "reference"]
        self.assertEqual(
            ["ask.1", "ask.2", "stdin/out.4.3.6",
             "3.4.2.1.1/2/3", "3.4.2.1.4"], references)

    def test_update_is_atomic_resumable_and_preserves_packet_identity(self):
        before = json.loads(self.manifest.read_text(encoding="utf-8"))["sample_sha256"]
        state = self.workspace.snapshot()
        case_id = state["cases"][0]["case_id"]
        updated = self.workspace.update(
            case_id=case_id, label="correction", notes="clear replacement",
            expected_revision=state["revision"],
        )
        self.assertEqual(1, updated["progress"]["completed"])
        resumed = AnnotationWorkspace(
            source=self.packet, manifest_path=self.manifest,
            rubrics_path=PLUGIN_ROOT / "rubrics" / "rubrics.json",
            protocols_path=PLUGIN_ROOT / "rubrics" / "annotation-protocols.json",
        ).snapshot()
        self.assertEqual("correction", resumed["cases"][0]["human_label"])
        self.assertEqual(before, json.loads(
            self.manifest.read_text(encoding="utf-8"))["sample_sha256"])

    def test_note_persists_without_forcing_a_label(self):
        state = self.workspace.snapshot()
        case_id = state["cases"][0]["case_id"]
        updated = self.workspace.update(
            case_id=case_id, label="", notes="Needs a second look",
            expected_revision=state["revision"],
        )
        saved_case = next(item for item in updated["cases"]
                          if item["case_id"] == case_id)
        self.assertEqual("Needs a second look", saved_case["notes"])
        self.assertEqual("", saved_case["human_label"])
        self.assertEqual(0, updated["progress"]["completed"])

    def test_agent_first_assessment_accepts_or_corrects_without_notes(self):
        with self.packet.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fields = tuple(reader.fieldnames or ())
        extended = fields[:-2] + (
            "proposed_label", "proposal_reason", "assessment") + fields[-2:]
        for row in rows:
            row.update({"proposed_label": "correction",
                        "proposal_reason": "provisional_reason",
                        "assessment": ""})
        with self.packet.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=extended)
            writer.writeheader()
            writer.writerows(rows)
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["sample_sha256"] = _packet_fingerprint(self.packet)
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        workspace = AnnotationWorkspace(
            source=self.packet, manifest_path=self.manifest,
            rubrics_path=PLUGIN_ROOT / "rubrics" / "rubrics.json",
            protocols_path=PLUGIN_ROOT / "rubrics" / "annotation-protocols.json")
        state = workspace.snapshot()
        updated = workspace.update(
            case_id=state["cases"][0]["case_id"], label="correction",
            assessment="correct", notes="",
            expected_revision=state["revision"])
        self.assertEqual("correct", updated["cases"][0]["assessment"])
        self.assertEqual("correction", updated["cases"][0]["human_label"])
        with self.assertRaisesRegex(ValueError, "assessment"):
            workspace.update(
                case_id=updated["cases"][1]["case_id"], label="approval",
                assessment="correct", notes="",
                expected_revision=updated["revision"])

    def test_stale_revision_and_invalid_label_are_rejected(self):
        state = self.workspace.snapshot()
        case_id = state["cases"][0]["case_id"]
        with self.assertRaisesRegex(ValueError, "unsupported label"):
            self.workspace.update(case_id=case_id, label="invented", notes="",
                                  expected_revision=state["revision"])
        self.workspace.update(case_id=case_id, label="none", notes="",
                              expected_revision=state["revision"])
        with self.assertRaises(AnnotationConflict):
            self.workspace.update(case_id=case_id, label="question", notes="",
                                  expected_revision=state["revision"])

    def test_server_is_loopback_only_and_assets_are_packaged(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            create_server(self.workspace, host="0.0.0.0", port=0)
        server = create_server(self.workspace, host="127.0.0.1", port=0)
        try:
            self.assertEqual("127.0.0.1", server.server_address[0])
        finally:
            server.server_close()
        html = (PLUGIN_ROOT / "ui" / "annotation" / "index.html").read_text(
            encoding="utf-8")
        script = (PLUGIN_ROOT / "ui" / "annotation" / "app.js").read_text(
            encoding="utf-8")
        self.assertIn('aria-live="polite"', html)
        self.assertIn('id="labelStack"', html)
        self.assertNotIn("data-label=", html)
        self.assertIn("const buttons = labels.map", script)
        self.assertIn("button.dataset.label = label", script)
        self.assertIn("index < labels.length", script)
        self.assertIn("proposed_label", script)
        self.assertIn("assessment", script)
        self.assertIn("KeyboardEvent", script)
        self.assertIn("expected_revision", script)
        self.assertIn("label_prompts", script)
        self.assertIn("formatEvidence", script)
        self.assertIn("async function saveNotesBeforeMove", script)
        self.assertIn("elements.notes.addEventListener(\"input\"", script)
        self.assertNotIn("innerHTML", script)
        self.assertIn(".evidence-list", (PLUGIN_ROOT / "ui" / "annotation" /
                                         "styles.css").read_text(encoding="utf-8"))

    def test_http_api_requires_csrf_and_persists_a_label(self):
        server = create_server(self.workspace, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:%d" % server.server_address[1]
        try:
            with urlopen(base + "/api/state") as response:
                state = json.load(response)
            payload = json.dumps({
                "case_id": state["cases"][0]["case_id"],
                "label": "correction", "notes": "",
                "expected_revision": state["revision"],
            }).encode("utf-8")
            forbidden = Request(
                base + "/api/labels", data=payload, method="POST",
                headers={"Content-Type": "application/json"})
            with self.assertRaises(HTTPError) as caught:
                urlopen(forbidden)
            self.assertEqual(403, caught.exception.code)
            request = Request(
                base + "/api/labels", data=payload, method="POST",
                headers={"Content-Type": "application/json",
                         "X-Retro-CSRF": state["csrf_token"]})
            with urlopen(request) as response:
                updated = json.load(response)
            self.assertEqual(1, updated["progress"]["completed"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
