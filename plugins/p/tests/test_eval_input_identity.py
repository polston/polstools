"""Input identities must name the bytes consumed, even after replacement."""

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.annotation import sample_trace_annotations
from retro_eval.catalog import load_rubric_catalogue
from retro_eval.reporting import run_deterministic_report
from retro_eval.schema import TraceRecord, SpanKind
from retro_eval.storage import JsonlTraceStore
from retro_eval.taxonomy_packets import write_taxonomy_review_packets


def record(index, kind=SpanKind.TRACE, **values):
    fields = dict(schema_version=1, trace_id="authored", span_id=str(index),
                  parent_span_id=None, source="authored", adapter_version=1,
                  source_version="1", span_kind=kind, sequence=index,
                  started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    fields.update(values)
    return TraceRecord(**fields)


class InputIdentityTests(unittest.TestCase):
    def test_read_with_digest_preserves_exact_bytes_and_read_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traces.jsonl"
            original = record(0, attributes={"authored": "caf\u00e9"})
            data = ("\r\n" + json.dumps(original.to_dict(), ensure_ascii=False)
                    + "\r\n \r\n").encode("utf-8")
            path.write_bytes(data)
            store = JsonlTraceStore(path)
            records, digest = store.read_with_digest()
            self.assertEqual(records, [original])
            self.assertEqual(store.read(), records)
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
            self.assertEqual(path.read_bytes(), data)

    def test_report_hash_identifies_consumed_rows_after_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JsonlTraceStore(root / "traces.jsonl")
            store.write([record(0), record(1, SpanKind.LLM)])
            before = hashlib.sha256(store.path.read_bytes()).hexdigest()
            (root / "extraction.json").write_text(json.dumps({
                "schema_version": 1, "sources": {"authored": {}}}))

            class ReplacementRegistry:
                def score(self, records, **kwargs):
                    store.write([record(2)])
                    return ()

            report = run_deterministic_report(root, registry=ReplacementRegistry())
            self.assertEqual(report["manifest"]["spans"], 2)
            self.assertEqual(report["manifest"]["trace_sha256"], before)
            self.assertNotEqual(before, hashlib.sha256(store.path.read_bytes()).hexdigest())

    def test_annotation_hash_identifies_sampled_rows_after_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JsonlTraceStore(root / "traces.jsonl")
            store.write([record(0, SpanKind.PROMPT, actor_kind="human")])
            before = hashlib.sha256(store.path.read_bytes()).hexdigest()

            class ReplacementEvidence(dict):
                def get(self, key, default=None):
                    store.write([record(1)])
                    return {"user_turn": "authored question", "user_turn_chars": 17}

            result = sample_trace_annotations(
                store.path, ReplacementEvidence(), root / "sample.csv",
                root / "manifest.json", per_source=1)
            self.assertEqual(result["trace_sha256"], before)
            self.assertEqual(result["source_counts"], {"authored": 1})
            self.assertNotEqual(before, hashlib.sha256(store.path.read_bytes()).hexdigest())

    def test_taxonomy_hash_identifies_candidates_after_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JsonlTraceStore(root / "traces.jsonl")
            store.write([
                record(0, SpanKind.TOOL, call_signature="same", tool_kind="Read"),
                record(1, SpanKind.TOOL, call_signature="same", tool_kind="Read"),
                record(2, "retro.tool_result", tool_kind="Read", status="error"),
            ])
            before = hashlib.sha256(store.path.read_bytes()).hexdigest()

            def replace_after_read(path):
                store.write([record(3)])
                return load_rubric_catalogue(path)

            with patch("retro_eval.taxonomy_packets.load_rubric_catalogue",
                       side_effect=replace_after_read):
                result = write_taxonomy_review_packets(
                    store.path, root / "packets", size_overrides={
                        "duplicate_work": {"calibration": 1, "test": 1},
                        "tool_failure_kind": {"calibration": 1, "test": 1},
                    })
            self.assertEqual(len(result["packets"]), 4)
            for packet in result["packets"]:
                manifest = json.loads(Path(packet["manifest"]).read_text())
                self.assertEqual(manifest["input_fingerprints"]["normalized_traces"], before)
            self.assertNotEqual(before, hashlib.sha256(store.path.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
