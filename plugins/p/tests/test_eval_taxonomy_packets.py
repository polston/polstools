"""Adaptive external annotation packets for P1 and P4."""

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.schema import SpanKind, TraceRecord  # noqa: E402
from retro_eval.adapters.claude import ClaudeAdapter  # noqa: E402
from retro_eval.private_evidence import collect_private_tool_evidence  # noqa: E402
from retro_eval.storage import JsonlTraceStore  # noqa: E402
from retro_eval.taxonomy_packets import (  # noqa: E402
    AdaptiveSamplingPlan,
    assess_label_support, assess_taxonomy_promotion,
    write_taxonomy_review_packets,
)
from retro_eval.catalog import load_rubric_catalogue  # noqa: E402
from retro_eval.taxonomy_packets_cli import main as taxonomy_packets_main  # noqa: E402


def record(trace, sequence, kind, *, signature="", tool="", status="ok",
           attributes=None):
    return TraceRecord(
        schema_version=1, trace_id=trace, span_id=(trace[:20] + "%04d" % sequence),
        parent_span_id=None, source="fixture", adapter_version=1,
        source_version="1", span_kind=kind, started_at=None,
        sequence=sequence, call_signature=signature, tool_kind=tool,
        status=status, attributes=dict(attributes or {}),
    )


class AdaptiveSupportTests(unittest.TestCase):
    def test_support_requires_each_registered_class_and_caps_rounds(self):
        plan = AdaptiveSamplingPlan(
            initial_calibration=2, initial_heldout=2,
            minimum_heldout_per_label=2,
            support_labels=("a", "b"), maximum_rounds=3, maximum_total=20)
        self.assertEqual("needs_more", assess_label_support(
            ["a", "a", "b"], plan, round_number=1)["status"])
        self.assertEqual("ready", assess_label_support(
            ["a", "a", "b", "b"], plan, round_number=1)["status"])
        self.assertEqual("insufficient_evidence", assess_label_support(
            ["a", "a", "b"], plan, round_number=3)["status"])

    def test_promotion_requires_support_and_every_preregistered_threshold(self):
        rubrics = load_rubric_catalogue(
            PLUGIN_ROOT / "rubrics" / "rubrics.json")
        duplicate = next(item for item in rubrics.rubrics
                         if item.id == "duplicate_work")
        failure = next(item for item in rubrics.rubrics
                       if item.id == "tool_failure_kind")
        self.assertEqual("unvalidated", assess_taxonomy_promotion(
            [], [], duplicate)["status"])

        duplicate_truth = [label for label in duplicate.extensions[
            "adaptive_sampling"]["support_labels"] for _ in range(20)]
        self.assertEqual("promoted", assess_taxonomy_promotion(
            duplicate_truth, list(duplicate_truth), duplicate)["status"])
        duplicate_predictions = list(duplicate_truth)
        duplicate_predictions[40:45] = ["polling"] * 5
        self.assertEqual("unvalidated", assess_taxonomy_promotion(
            duplicate_truth, duplicate_predictions, duplicate)["status"])

        failure_truth = [label for label in failure.extensions[
            "adaptive_sampling"]["support_labels"] for _ in range(20)]
        self.assertEqual("promoted", assess_taxonomy_promotion(
            failure_truth, list(failure_truth), failure)["status"])
        failure_predictions = list(failure_truth)
        failure_predictions[:11] = ["unknown"] * 11
        result = assess_taxonomy_promotion(
            failure_truth, failure_predictions, failure)
        self.assertEqual("unvalidated", result["status"])
        self.assertGreater(result["unknown_rate"], 0.10)


class TaxonomyPacketTests(unittest.TestCase):
    def test_polling_proposal_requires_cost_context_and_flags_repeated_overhead(self):
        from retro_eval.taxonomy_packets import _candidates

        trace = "a" * 24
        first = record(trace, 1, SpanKind.TOOL,
                       signature="same", tool="wait")
        second = record(trace, 2, SpanKind.TOOL,
                        signature="same", tool="wait")
        evidence = {
            first.span_id: {"tool_result": "Script running with cell ID 7"},
            second.span_id: {"tool_result": "Script running with cell ID 7"},
        }
        (bounded_unknown,) = _candidates(
            (first, second), "duplicate_work", evidence)
        self.assertEqual("ambiguous", bounded_unknown.proposed_label)
        self.assertEqual("polling_cost_unobservable",
                         bounded_unknown.proposal_reason)

        probe = record(trace, 3, SpanKind.TOOL,
                       signature="probe", tool="wait_agent")
        third = record(trace, 4, SpanKind.TOOL,
                       signature="same", tool="wait")
        evidence[probe.span_id] = {"tool_result": "Wait timed out"}
        evidence[third.span_id] = {"tool_result": "Script running with cell ID 7"}
        candidates = _candidates(
            (first, second, probe, third), "duplicate_work", evidence)
        prolonged = next(item for item in candidates
                         if item.stable_key == third.span_id)
        self.assertEqual("wasteful_duplicate", prolonged.proposed_label)
        self.assertEqual("repeated_polling_overhead",
                         prolonged.proposal_reason)

    def test_failure_sampling_uses_all_redacted_evidence_hint_classes(self):
        from retro_eval.taxonomy_packets import _failure_sampling_stratum

        cases = {
            "control_refusal": {"tool_result": "Refusing to run outside the worktree"},
            "missing_target": {"tool_result": "No such file or directory"},
            "malformed_input": {"tool_result": "Syntax error near unexpected token"},
            "expected_probe": {"tool_input": "Get-Command missing-tool",
                               "tool_result": "Exit code 1"},
            "execution_failure": {"tool_input": "build project",
                                  "tool_result": "compiler exited 2"},
            "unknown": {},
        }

        for expected, evidence in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(expected, _failure_sampling_stratum(evidence))

    def test_duplicate_packet_bounds_long_intervening_tool_history(self):
        from retro_eval.taxonomy_packets import _candidates

        previous = record("a" * 24, 1, SpanKind.TOOL,
                          signature="same", tool="Read")
        intervening = tuple(
            record("a" * 24, index + 2, SpanKind.TOOL,
                   signature="different-%s" % index, tool="Read")
            for index in range(200)
        )
        current = record("a" * 24, 202, SpanKind.TOOL,
                         signature="same", tool="Read")

        candidate = _candidates(
            (previous,) + intervening + (current,), "duplicate_work")[0]

        self.assertIn("### Intervening operations", candidate.context)
        self.assertIn("- Count: 200", candidate.context)
        self.assertEqual(4, candidate.context.count("purpose=unavailable"))
        self.assertLess(len(candidate.context), 1000)

    def test_source_adapter_private_evidence_enriches_external_packets_only(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_root = root / "claude"
            transcript = source_root / "project" / "main.jsonl"
            transcript.parent.mkdir(parents=True)
            rows = [
                {"type": "user", "timestamp": "2026-08-01T12:00:00Z",
                 "promptSource": "typed",
                 "message": {"role": "user", "content": "start"}},
                {"type": "assistant", "timestamp": "2026-08-01T12:00:01Z",
                 "message": {"role": "assistant", "content": [
                     {"type": "text", "text": "Inspect the initial state"},
                     {"type": "tool_use", "id": "tool-1", "name": "Read",
                      "input": {"file": "private-target"}}]}},
                {"type": "user", "timestamp": "2026-08-01T12:00:02Z",
                 "message": {"role": "user", "content": [
                     {"type": "tool_result", "tool_use_id": "tool-1",
                      "is_error": True, "content": "private missing target"}]}},
                {"type": "assistant", "timestamp": "2026-08-01T12:00:03Z",
                 "message": {"role": "assistant", "content": [
                     {"type": "text", "text": "Verify after the result"},
                     {"type": "tool_use", "id": "tool-2", "name": "Read",
                      "input": {"file": "private-target"}}]}},
            ]
            with transcript.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")
            salt = b"x" * 32
            normalized = ClaudeAdapter(salt).read(transcript, source_root).records
            traces = root / "traces.jsonl"
            JsonlTraceStore(traces).write(normalized)
            evidence = collect_private_tool_evidence(
                {"claude": source_root}, salt,
                redactor=lambda text: text.replace("private", "<redacted>"))
            output = root / "packets"
            write_taxonomy_review_packets(
                traces, output, private_evidence=evidence,
                size_overrides={
                    "duplicate_work": {"calibration": 1, "test": 1},
                    "tool_failure_kind": {"calibration": 1, "test": 1},
                })

            packet_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output.glob("*.csv"))
            duplicate_rows = []
            for path in output.glob("duplicate-work-*.csv"):
                with path.open("r", encoding="utf-8", newline="") as handle:
                    duplicate_rows.extend(csv.DictReader(handle))
            duplicate_context = "\n".join(
                row["context"] for row in duplicate_rows)
            duplicate_current = "\n".join(
                row["user_turn"] for row in duplicate_rows)
            self.assertIn("<redacted>", packet_text)
            self.assertIn("Prior call purpose: Inspect the initial state",
                          duplicate_context)
            self.assertIn("Prior result:", duplicate_context)
            self.assertIn("<redacted> missing target", duplicate_context)
            self.assertIn("Current call purpose: Verify after the result",
                          duplicate_current)
            self.assertIn("Prior call input:", duplicate_context)
            self.assertIn("Current call input:", duplicate_current)
            self.assertIn("### Current repeated call", duplicate_current)
            self.assertNotIn("Assess whether", duplicate_current)
            self.assertNotIn("### Current", duplicate_context)
            self.assertNotIn("use the redacted current input above", packet_text)
            self.assertNotIn("private-target", packet_text)
            normalized_text = traces.read_text(encoding="utf-8")
            self.assertNotIn("<redacted>", normalized_text)
            self.assertNotIn("private-target", normalized_text)

    def test_packets_are_external_nonoverlapping_and_protocol_bound(self):
        records = []
        for index in range(12):
            trace = "%024x" % (index + 1)
            tool = "wait_agent" if index % 3 == 0 else "Read"
            records.extend([
                record(trace, 1, SpanKind.TOOL, signature="same-" + trace,
                       tool=tool),
                record(trace, 2, SpanKind.TOOL, signature="same-" + trace,
                       tool=tool),
                record(trace, 3, "retro.tool_result", tool=tool,
                       status="error", attributes={
                           "error_kind": "missing_target" if index % 2 else "unknown"}),
            ])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            traces = root / "traces.jsonl"
            JsonlTraceStore(traces).write(records)
            output = root / "annotations" / "proposal-taxonomies"
            result = write_taxonomy_review_packets(
                traces, output, size_overrides={
                    "duplicate_work": {"calibration": 3, "test": 3},
                    "tool_failure_kind": {"calibration": 4, "test": 4},
                })
            second = write_taxonomy_review_packets(
                traces, output, round_number=2,
                prior_manifests=tuple(packet["manifest"]
                                      for packet in result["packets"]),
                size_overrides={
                    "duplicate_work": {"calibration": 1, "test": 1},
                    "tool_failure_kind": {"calibration": 1, "test": 1},
                })

            self.assertEqual(4, len(result["packets"]))
            instructions = output / "01-review-instructions.md"
            self.assertTrue(instructions.exists())
            instruction_text = instructions.read_text(encoding="utf-8")
            self.assertIn("rule-based scorer's proposed diagnosis",
                          instruction_text)
            self.assertNotIn("agent's proposed diagnosis", instruction_text)
            all_ids = set()
            for expected_round, packet_group in (
                    (1, result["packets"]), (2, second["packets"])):
                for packet in packet_group:
                    source = Path(packet["source"])
                    manifest_path = Path(packet["manifest"])
                    with source.open("r", encoding="utf-8", newline="") as handle:
                        rows = list(csv.DictReader(handle))
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    ids = {row["case_id"] for row in rows}
                    self.assertFalse(ids & all_ids)
                    all_ids |= ids
                    self.assertEqual(expected_round,
                                     manifest["adaptive_sampling"]["round"])
                    self.assertEqual(6, manifest["annotation_packet_version"])
                    if packet["rubric_id"] == "duplicate_work":
                        self.assertEqual(
                            5, manifest["annotation_protocol_version"])
                        self.assertEqual(
                            "passed", manifest["review_quality"]["status"])
                        self.assertEqual(
                            len(rows), manifest["review_quality"]["case_count"])
                    for row in rows:
                        self.assertTrue(row["proposed_label"])
                        self.assertTrue(row["proposal_reason"])
                        self.assertEqual("", row["assessment"])
                        self.assertEqual("", row["human_label"])
                    if packet["rubric_id"] == "tool_failure_kind":
                        self.assertEqual(1, manifest["sampling_hint_version"])
                    self.assertIn("annotation_protocol_sha256", manifest)
                    self.assertNotIn(
                        str(traces), manifest_path.read_text(encoding="utf-8"))
                    self.assertNotIn(
                        "same-", source.read_text(encoding="utf-8"))

    def test_cli_samples_and_assesses_without_repository_output(self):
        records = [
            record("a" * 24, 1, SpanKind.TOOL, signature="same", tool="Read"),
            record("a" * 24, 2, SpanKind.TOOL, signature="same", tool="Read"),
        ]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            traces = root / "traces.jsonl"
            JsonlTraceStore(traces).write(records)
            output = root / "packets"
            self.assertEqual(0, taxonomy_packets_main([
                "sample", "--traces", str(traces),
                "--output-dir", str(output),
                "--duplicate-calibration", "1", "--duplicate-heldout", "1",
                "--failure-calibration", "1", "--failure-heldout", "1",
            ]))
            packet = next(output.glob("duplicate-work-test-round1.csv"))
            manifest = next(output.glob(
                "duplicate-work-test-round1-manifest.json"))
            self.assertEqual(1, taxonomy_packets_main([
                "assess", "--source", str(packet),
                "--manifest", str(manifest),
            ]))


if __name__ == "__main__":
    unittest.main()
