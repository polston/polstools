"""Sampling must preserve existing assessments and its selected identity."""

import contextlib
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval import annotation  # noqa: E402
from retro_eval.schema import SpanKind  # noqa: E402
from retro_eval.storage import JsonlTraceStore  # noqa: E402
from test_eval_input_identity import record  # noqa: E402


class AnnotationPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.extract = self.root / "extract.json"
        self.extract.write_text(json.dumps({"source_system": "authored",
            "sessions": [{"session_id": "authored", "messages": [
                {"line": 1, "role": "user", "excerpt": "Please refine this."}]}]}))
        self.traces = self.root / "traces.jsonl"
        prompt = record(0, SpanKind.PROMPT, actor_kind="human")
        JsonlTraceStore(self.traces).write([prompt])
        self.evidence = {prompt.span_id: {"user_turn": "Please refine this."}}

    def sample(self, source, manifest, *, traces=False):
        if traces:
            return annotation.sample_trace_annotations(self.traces, self.evidence,
                source, manifest, per_source=1)
        return annotation.sample_annotations((self.extract,), source, manifest, per_source=1)

    def test_both_sampling_entrypoints_preserve_completed_annotations(self):
        for traces in (False, True):
            output = self.root / ("packet-trace.csv" if traces else "packet-extract.csv")
            manifest = output.with_suffix(".json")
            result = self.sample(output, manifest, traces=traces)
            self.assertEqual(result["sample_sha256"], annotation._packet_fingerprint(output))
            with output.open(newline="") as handle:
                reader = csv.DictReader(handle)
                fields, rows = reader.fieldnames, list(reader)
            self.assertEqual("0", rows[0]["context_chars"])
            rows[0].update(human_label="correction", notes="Preserve this assessment")
            with output.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            before = (output.read_bytes(), manifest.read_bytes())
            with self.assertRaisesRegex(ValueError, "destination already exists"):
                self.sample(output, manifest, traces=traces)
            self.assertEqual(before, (output.read_bytes(), manifest.read_bytes()))

    def test_collision_alias_and_reserved_salt_reject_before_writing(self):
        manifest = self.root / "existing.json"
        manifest.write_bytes(b"Prior manifest")
        output = self.root / "fresh.csv"
        with self.assertRaisesRegex(ValueError, "destination already exists"):
            self.sample(output, manifest)
        self.assertFalse(output.exists())
        self.assertEqual(b"Prior manifest", manifest.read_bytes())
        for source, target in ((output, output),
                (self.root / ".annotation-salt", output),
                (output, self.root / ".annotation-salt")):
            with self.assertRaises(ValueError):
                self.sample(source, target)
        self.assertFalse(output.exists())
        self.assertFalse((self.root / ".annotation-salt").exists())

    def test_competing_output_is_not_overwritten_after_preflight(self):
        output, manifest = self.root / "sample.csv", self.root / "manifest.json"
        original = Path.open
        fired = []

        def competing_open(path, *args, **kwargs):
            mode = args[0] if args else kwargs.get("mode", "r")
            if path == output and mode in ("w", "x") and not fired:
                fired.append(True)
                with original(path, "wb") as handle:
                    handle.write(b"Competing assessment")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", competing_open):
            with self.assertRaises(FileExistsError):
                self.sample(output, manifest)
        self.assertEqual(b"Competing assessment", output.read_bytes())
        self.assertFalse(manifest.exists())

    def test_competing_salt_cannot_silently_change_selected_identity(self):
        salt_path = self.root / ".annotation-salt"

        def competing_salt(size):
            salt_path.write_bytes(b"w" * size)
            return b"l" * size

        with patch.object(annotation.os, "urandom", side_effect=competing_salt):
            with self.assertRaisesRegex(ValueError, "salt changed"):
                annotation._salt(self.root)
        self.assertEqual(b"w" * 32, salt_path.read_bytes())

    def test_published_manifest_does_not_certify_replaced_output(self):
        output, manifest = self.root / "sample.csv", self.root / "manifest.json"
        original = Path.open
        fired = []

        @contextlib.contextmanager
        def replace_after_write(path, *args, **kwargs):
            with original(path, *args, **kwargs) as handle:
                yield handle
            mode = args[0] if args else kwargs.get("mode", "r")
            if path == output and mode in ("w", "x") and not fired:
                fired.append(True)
                with original(path, newline="") as handle:
                    reader = csv.DictReader(handle)
                    fields, rows = reader.fieldnames, list(reader)
                rows[0]["context"] = "Replaced after publication"
                with original(path, "w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)

        with patch.object(Path, "open", replace_after_write):
            result = self.sample(output, manifest)
        self.assertTrue(fired)
        self.assertNotEqual(result["sample_sha256"], annotation._packet_fingerprint(output))
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            annotation._load_packet(output, manifest,
                rubric_id="turn_friction_legacy", rubric_version=1)


if __name__ == "__main__":
    unittest.main()
