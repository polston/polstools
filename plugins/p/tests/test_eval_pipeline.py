"""End-to-end extraction and privacy tests for the evaluation pipeline."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from retro_eval.pipeline import EvaluationPipeline, WorkStore  # noqa: E402
from retro_eval.instruction_manifest import (  # noqa: E402
    InstructionSource, write_instruction_manifest,
)
from retro_eval.reporting import run_deterministic_report  # noqa: E402
from retro_eval.storage import JsonlTraceStore  # noqa: E402

from test_eval_adapters import write_jsonl  # noqa: E402


class WorkStoreTests(unittest.TestCase):
    def test_work_store_refuses_a_directory_inside_the_repository(self):
        with self.assertRaises(ValueError):
            WorkStore(PLUGIN_ROOT / "forbidden-output")

    def test_salt_is_created_once_and_never_returned_in_public_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkStore(Path(tmp) / "work")
            first = store.id_salt()
            again = WorkStore(Path(tmp) / "work").id_salt()
            self.assertEqual(first, again)
            self.assertEqual(len(first), 32)
            self.assertNotIn(first.hex(), repr(store.public_metadata()))


class TraceStoreTests(unittest.TestCase):
    def test_jsonl_round_trip_rejects_mixed_schema_versions(self):
        from datetime import datetime, timezone
        from retro_eval.schema import SpanKind, TraceRecord

        record = TraceRecord(
            schema_version=1, trace_id="a" * 24, span_id="b" * 24,
            parent_span_id=None, source="codex", adapter_version=1,
            source_version="1", span_kind=SpanKind.PROMPT,
            started_at=datetime(2026, 8, 1, tzinfo=timezone.utc), sequence=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonlTraceStore(Path(tmp) / "traces.jsonl")
            store.write([record])
            self.assertEqual(store.read(), [record])
            payload = record.to_dict()
            payload["schema_version"] = 2
            with (Path(tmp) / "traces.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")
            with self.assertRaises(ValueError):
                store.read()


class PipelineTests(unittest.TestCase):
    def build_sources(self, root):
        claude = root / "claude"
        codex = root / "codex"
        write_jsonl(claude / "project" / "main.jsonl", [
            {"type": "user", "sessionId": "claude-private", "timestamp": "2026-08-01T12:00:00Z",
             "promptSource": "typed", "message": {"role": "user", "content": "private claude prompt"}},
            {"type": "assistant", "sessionId": "claude-private", "timestamp": "2026-08-01T12:00:01Z",
             "attributionSkill": "example-skill", "message": {"role": "assistant", "content": "done"}},
        ])
        write_jsonl(claude / "project" / "system.jsonl", [
            {"type": "user", "sessionId": "system-private", "promptSource": "system",
             "message": {"role": "user", "content": "private injection"}},
        ])
        user_rows = [
            {"type": "session_meta", "timestamp": "2026-08-01T12:00:00Z", "payload": {
                "id": "codex-private", "thread_source": "user", "cli_version": "1"}},
            {"type": "response_item", "timestamp": "2026-08-01T12:00:01Z", "payload": {
                "type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "private codex prompt"}]}},
            {"type": "response_item", "timestamp": "2026-08-01T12:00:02Z", "payload": {
                "type": "message", "role": "assistant", "phase": "final_answer", "content": [
                    {"type": "output_text", "text": "private codex result"}]}},
        ]
        write_jsonl(codex / "2026" / "rollout-user.jsonl", user_rows)
        automated = json.loads(json.dumps(user_rows))
        automated[0]["payload"]["thread_source"] = "automation"
        write_jsonl(codex / "2026" / "rollout-auto.jsonl", automated)
        return claude, codex

    def test_extract_writes_only_normalized_content_free_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude, codex = self.build_sources(root)
            pipeline = EvaluationPipeline(root / "work")
            summary = pipeline.extract(claude_root=claude, codex_root=codex)
            public = summary.to_public_dict()
            self.assertEqual(public["included_traces"], 2)
            self.assertEqual(public["excluded_traces"], 2)
            self.assertEqual(public["sources"]["claude"]["included"], 1)
            self.assertEqual(public["sources"]["codex"]["included"], 1)
            self.assertEqual(public["sources"]["codex"]["skill_attribution"]["state"],
                             "unavailable")
            self.assertEqual(public["sources"]["claude"]["skill_attribution"]["state"],
                             "version_floor")
            self.assertRegex(
                public["sources"]["claude"]["source_set_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(
                public["sources"]["codex"]["source_set_sha256"], r"^[0-9a-f]{64}$")
            serialized = (root / "work" / "traces.jsonl").read_text(encoding="utf-8")
            for private in ("claude-private", "codex-private", "private claude prompt",
                            "private codex prompt", "private codex result", str(claude), str(codex)):
                self.assertNotIn(private, serialized)
            self.assertNotIn("path", serialized.lower())

    def test_public_summary_has_counts_and_reasons_but_no_local_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude, codex = self.build_sources(root)
            summary = EvaluationPipeline(root / "work").extract(
                claude_root=claude, codex_root=codex)
            text = json.dumps(summary.to_public_dict(), sort_keys=True)
            self.assertNotIn(str(root), text)
            self.assertNotIn("main.jsonl", text)
            self.assertEqual(summary.to_public_dict()["exclusion_reasons"], {
                "no direct-human prompt": 1,
                "thread_source:automation": 1,
            })

    def test_instruction_manifest_binds_extraction_and_dataset_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude, codex = self.build_sources(root)
            manifest_path = root / "instructions" / "active.json"
            manifest = write_instruction_manifest(
                manifest_path,
                (InstructionSource(
                    source_kind="standing_instructions",
                    content_sha256="a" * 64,
                    version="aligned-v1"),),
                activated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            )
            pipeline = EvaluationPipeline(
                root / "work", instruction_manifest_path=manifest_path)
            summary = pipeline.extract(claude_root=claude, codex_root=codex)
            report = run_deterministic_report(
                root / "work", created_commit="abc123")

        self.assertEqual(
            manifest.manifest_sha256,
            summary.to_public_dict()["instruction_manifest_sha256"])
        self.assertEqual(
            manifest.manifest_sha256,
            report["manifest"]["instruction_manifest_sha256"])
        self.assertEqual(
            manifest.manifest_sha256,
            report["dataset_manifest"]["instruction_manifest_sha256"])
        self.assertEqual(
            {"population": 2, "resolved": 2, "unresolved": 0},
            summary.to_public_dict()["instruction_manifest_coverage"])
        self.assertEqual(
            summary.to_public_dict()["instruction_manifest_coverage"],
            report["dataset_manifest"]["instruction_manifest_coverage"])

    def test_instruction_manifest_rejects_sessions_before_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude, codex = self.build_sources(root)
            manifest_path = root / "instructions" / "future.json"
            write_instruction_manifest(
                manifest_path,
                (InstructionSource(
                    source_kind="standing_instructions",
                    content_sha256="a" * 64,
                    version="future-v1"),),
                activated_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
            )
            pipeline = EvaluationPipeline(
                root / "work", instruction_manifest_path=manifest_path)
            with self.assertRaisesRegex(ValueError, "outside manifest coverage"):
                pipeline.extract(claude_root=claude, codex_root=codex)


if __name__ == "__main__":
    unittest.main()
