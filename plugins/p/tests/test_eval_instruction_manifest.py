"""Contracts for private, versioned instruction-source provenance."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.dataset import DatasetManifest  # noqa: E402
from retro_eval.instruction_manifest import (  # noqa: E402
    InstructionManifestIndex,
    InstructionSource,
    hash_instruction_source,
    write_instruction_manifest,
)
from retro_eval.instruction_manifest_cli import main as instruction_manifest_main  # noqa: E402
from retro_eval.pipeline import ExtractionSummary  # noqa: E402


T0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


class InstructionManifestTests(unittest.TestCase):
    def source(self, **overrides):
        values = {
            "source_kind": "standing_instructions",
            "content_sha256": "a" * 64,
            "version": "aligned-v1",
            "commit": "b" * 40,
            "privacy_class": "private_local",
        }
        values.update(overrides)
        return InstructionSource(**values)

    def test_writer_is_external_content_free_and_resolves_activation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first_path = root / "manifest-1.json"
            second_path = root / "manifest-2.json"
            first = write_instruction_manifest(
                first_path, (self.source(),), activated_at=T0)
            second = write_instruction_manifest(
                second_path,
                (self.source(content_sha256="c" * 64, version="aligned-v2"),),
                activated_at=T1,
            )

            payload = json.loads(first_path.read_text(encoding="utf-8"))
            serialized = first_path.read_text(encoding="utf-8")
            self.assertEqual(1, payload["schema_version"])
            self.assertEqual(first.manifest_sha256, payload["manifest_sha256"])
            self.assertNotIn("content", payload["sources"][0])
            self.assertNotIn("path", serialized.lower())
            self.assertEqual(first.manifest_sha256,
                             InstructionManifestIndex(root).active_at(T0).manifest_sha256)
            self.assertEqual(second.manifest_sha256,
                             InstructionManifestIndex(root).active_at(T1).manifest_sha256)

    def test_writer_rejects_repository_destination_and_ambiguous_boundaries(self):
        with self.assertRaisesRegex(ValueError, "outside repositories"):
            write_instruction_manifest(
                PLUGIN_ROOT / "private-manifest.json", (self.source(),),
                activated_at=T0)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            write_instruction_manifest(root / "one.json", (self.source(),),
                                       activated_at=T0)
            write_instruction_manifest(
                root / "two.json", (self.source(version="other"),),
                activated_at=T0)
            with self.assertRaisesRegex(ValueError, "ambiguous activation"):
                InstructionManifestIndex(root).active_at(T0)

    def test_trace_and_dataset_manifests_carry_only_manifest_hash(self):
        digest = "d" * 64
        trace_manifest = ExtractionSummary(
            included_traces=1, excluded_traces=0, normalized_spans=2,
            sources={}, exclusion_reasons={},
            instruction_manifest_sha256=digest,
            instruction_manifest_coverage={
                "population": 1, "resolved": 1, "unresolved": 0})
        dataset = DatasetManifest(
            dataset_id="d1", schema_versions=(1,),
            adapter_versions={"codex": 1}, start=T0, end=T1, seed="seed",
            instruction_manifest_sha256=digest,
            instruction_manifest_coverage={
                "population": 0, "resolved": 0, "unresolved": 0})

        trace_payload = trace_manifest.to_public_dict()
        dataset_payload = dataset.to_dict()
        self.assertEqual(3, trace_payload["schema_version"])
        self.assertEqual(3, dataset_payload["schema_version"])
        self.assertEqual(digest, trace_payload["instruction_manifest_sha256"])
        self.assertEqual(digest, dataset_payload["instruction_manifest_sha256"])
        self.assertEqual(0, trace_payload[
            "instruction_manifest_coverage"]["unresolved"])
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            DatasetManifest(
                dataset_id="d2", schema_versions=(1,),
                adapter_versions={"codex": 1}, start=T0, end=T1, seed="seed",
                instruction_manifest_sha256="not-a-hash")

    def test_cli_hashes_sources_without_persisting_paths_or_content(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "private-source"
            source.mkdir()
            (source / "rules.md").write_text("private rule text", encoding="utf-8")
            output = root / "manifests" / "active.json"
            exit_code = instruction_manifest_main([
                "write", "--output", str(output),
                "--activated-at", "2026-08-01T12:00:00Z",
                "--source", "standing_instructions=" + str(source),
                "--version", "standing_instructions=aligned-v1",
            ])

            payload = json.loads(output.read_text(encoding="utf-8"))
            serialized = output.read_text(encoding="utf-8")
            self.assertEqual(0, exit_code)
            self.assertEqual(hash_instruction_source(source),
                             payload["sources"][0]["content_sha256"])
            self.assertNotIn(str(source), serialized)
            self.assertNotIn("private rule text", serialized)


if __name__ == "__main__":
    unittest.main()
