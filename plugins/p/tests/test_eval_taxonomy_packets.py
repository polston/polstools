"""Adaptive external annotation packets for P1 and P4."""

import csv
import contextlib
import hashlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
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
        schema_version=1, trace_id=trace, span_id=hashlib.sha256(
            (trace + ":" + str(sequence)).encode()).hexdigest()[:24],
        parent_span_id=None, source="fixture", adapter_version=1,
        source_version="1", span_kind=kind, started_at=None,
        sequence=sequence, call_signature=signature, tool_kind=tool,
        status=status, attributes=dict(attributes or {}),
    )


class TraceSplitTests(unittest.TestCase):
    def test_trace_stays_in_one_phase_across_rubrics_and_rounds(self):
        from retro_eval.taxonomy_packets import _candidates, _assigned_split
        rows = []
        for i in range(1, 65):
            trace = "%024x" % i
            rows.extend(record(trace, j, SpanKind.TOOL, signature="same", tool="Read")
                        for j in (1, 2, 3))
            rows.append(record(trace, 4, "retro.tool_result", tool="Read", status="error"))
        trace_by_span = {r.span_id: r.trace_id for r in rows}
        phases = {}
        assignments = {}
        for rubric in ("duplicate_work", "tool_failure_kind"):
            for c in _candidates(rows, rubric):
                split = _assigned_split(c, rubric)
                phases.setdefault(trace_by_span[c.stable_key], set()).add(split)
                assignments[rubric, c.stable_key] = split
            self.assertEqual(
                {c.stable_key: _assigned_split(c, rubric) for c in _candidates(rows, rubric)},
                {c.stable_key: _assigned_split(c, rubric) for c in _candidates(list(reversed(rows)), rubric)})
        self.assertTrue(all(len(values) == 1 for values in phases.values()))
        self.assertEqual({"calibration", "test"}, set(assignments.values()))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            traces = root / "traces.jsonl"
            JsonlTraceStore(traces).write(rows)
            overrides = {r: {"calibration": 5, "test": 5}
                         for r in ("duplicate_work", "tool_failure_kind")}
            first = write_taxonomy_review_packets(traces, root / "first", size_overrides=overrides)
            priors = [p["manifest"] for p in first["packets"]]
            original = {p: Path(p).read_bytes() for p in priors}
            second = write_taxonomy_review_packets(traces, root / "second", round_number=2,
                prior_manifests=priors, size_overrides=overrides)
            groups = {"calibration": set(), "test": set()}
            for result in (first, second):
                for packet in result["packets"]:
                    manifest = json.loads(Path(packet["manifest"]).read_text())
                    self.assertEqual("source_trace_v1", manifest["split_policy"])
                    self.assertEqual(packet["population"], len(manifest["candidate_group_sha256"]))
                    groups[packet["split"]].update(manifest["candidate_group_sha256"])
            self.assertTrue(all(groups.values()))
            self.assertFalse(groups["calibration"] & groups["test"])
            self.assertEqual(original, {p: Path(p).read_bytes() for p in priors})
            changed = json.loads(Path(priors[0]).read_text())
            changed.pop("split_policy")
            altered = root / "legacy.json"
            altered.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "split policy"):
                write_taxonomy_review_packets(traces, root / "invalid", round_number=2,
                    prior_manifests=[altered, *priors[1:]], size_overrides=overrides)
            self.assertFalse((root / "invalid").exists())


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
        empty = assess_taxonomy_promotion([], [], duplicate)
        for metric in ("precision", "recall", "agreement", "polling_false_positive_rate"):
            self.assertIsNone(empty[metric])

        duplicate_truth = [label for label in duplicate.extensions[
            "adaptive_sampling"]["support_labels"] for _ in range(20)]
        self.assertEqual("thresholds_met", assess_taxonomy_promotion(
            duplicate_truth, list(duplicate_truth), duplicate)["status"])
        passing = assess_taxonomy_promotion(duplicate_truth, duplicate_truth, duplicate)
        self.assertFalse(passing["decision_support"])
        self.assertEqual("observed_label_pairs", passing["assessment_scope"])
        self.assertEqual(2, passing["assessment_version"])
        duplicate_predictions = list(duplicate_truth)
        duplicate_predictions[40:45] = ["polling"] * 5
        self.assertEqual("unvalidated", assess_taxonomy_promotion(
            duplicate_truth, duplicate_predictions, duplicate)["status"])

        failure_truth = [label for label in failure.extensions[
            "adaptive_sampling"]["support_labels"] for _ in range(20)]
        self.assertEqual("thresholds_met", assess_taxonomy_promotion(
            failure_truth, list(failure_truth), failure)["status"])
        failure_predictions = list(failure_truth)
        failure_predictions[:11] = ["unknown"] * 11
        result = assess_taxonomy_promotion(
            failure_truth, failure_predictions, failure)
        self.assertEqual("unvalidated", result["status"])
        self.assertGreater(result["unknown_rate"], 0.10)


class TaxonomyPacketTests(unittest.TestCase):
    def test_manifest_cannot_certify_replaced_publication(self):
        from retro_eval.annotation import _load_packet, _packet_fingerprint
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            traces = root / "traces.jsonl"
            JsonlTraceStore(traces).write([
                record("a" * 24, j, SpanKind.TOOL, signature="same", tool="Read")
                for j in (1, 2)])
            original = Path.open
            replaced = []

            @contextlib.contextmanager
            def replace_after_write(path, *args, **kwargs):
                with original(path, *args, **kwargs) as handle:
                    yield handle
                mode = args[0] if args else kwargs.get("mode", "r")
                if path.suffix == ".csv" and mode in ("w", "x") and not replaced:
                    with original(path, newline="") as handle:
                        reader = csv.DictReader(handle)
                        fields, rows = reader.fieldnames, list(reader)
                    if rows:
                        replaced.append(path)
                        rows[0]["context"] = "Unvalidated replacement"
                        with original(path, "w", newline="") as handle:
                            writer = csv.DictWriter(handle, fieldnames=fields)
                            writer.writeheader()
                            writer.writerows(rows)

            with patch.object(Path, "open", replace_after_write):
                result = write_taxonomy_review_packets(traces, root / "packets")
            self.assertEqual(1, len(replaced))
            packet = next(packet for packet in result["packets"]
                          if Path(packet["source"]) == replaced[0])
            manifest_path = Path(packet["manifest"])
            metadata = json.loads(manifest_path.read_text())
            self.assertNotEqual(metadata["sample_sha256"], _packet_fingerprint(replaced[0]))
            with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
                _load_packet(replaced[0], manifest_path,
                    rubric_id=metadata["rubric_id"], rubric_version=metadata["rubric_version"])

    def test_sampling_budget_and_invalid_requests_reject_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            traces = root / "traces.jsonl"
            JsonlTraceStore(traces).write([
                record("%024x" % i, j, SpanKind.TOOL, signature="same", tool="Read")
                for i in range(1, 1201) for j in (1, 2)])
            for index, kwargs in enumerate((
                    {"size_overrides": {"duplicate_work": {"calibration": 500, "test": 500}}},
                    {"size_overrides": {"tool_failure_kind": {"test": -1}}},
                    {"size_overrides": {"duplicate_work": {"calibration": True}}},
                    {"size_overrides": {"duplicate_work": {"calibration": 1.5}}},
                    {"round_number": True}, {"round_number": 2}, {"round_number": 5})):
                output = root / ("invalid-%s" % index)
                with self.assertRaises(ValueError):
                    write_taxonomy_review_packets(traces, output, **kwargs)
                self.assertFalse(output.exists())
            first = write_taxonomy_review_packets(traces, root / "first",
                size_overrides={"duplicate_work": {"calibration": 150, "test": 150}})
            priors = [packet["manifest"] for packet in first["packets"]]
            preserved = {Path(packet[field]): Path(packet[field]).read_bytes()
                         for packet in first["packets"] for field in ("source", "manifest")}
            for name, supplied, sizes in (
                    ("over-budget", priors, {"calibration": 51, "test": 50}),
                    ("missing-prior", priors[:-1], {"calibration": 0, "test": 0}),
                    ("duplicate-prior", priors + priors[:1], {"calibration": 0, "test": 0})):
                output = root / name
                with self.assertRaises(ValueError):
                    write_taxonomy_review_packets(traces, output, round_number=2,
                        prior_manifests=supplied, size_overrides={"duplicate_work": sizes})
                self.assertFalse(output.exists())
            second = write_taxonomy_review_packets(traces, root / "second", round_number=2,
                prior_manifests=priors,
                size_overrides={"duplicate_work": {"calibration": 50, "test": 50}})
            total = sum(packet["population"] for result in (first, second)
                        for packet in result["packets"] if packet["rubric_id"] == "duplicate_work")
            self.assertEqual(400, total)
            self.assertEqual(preserved, {path: path.read_bytes() for path in preserved})

    def test_adaptive_round_requires_unchanged_private_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            traces = root / "traces.jsonl"
            rows = [record("a" * 24, j, SpanKind.TOOL, signature="same", tool="Read")
                    for j in (1, 2)]
            JsonlTraceStore(traces).write(rows)
            evidence = {rows[1].span_id: {"tool_result": "unchanged"}}
            first = write_taxonomy_review_packets(traces, root / "first", private_evidence=evidence)
            priors = [packet["manifest"] for packet in first["packets"]]
            for name, changed in (("omitted", None),
                    ("changed", {rows[1].span_id: {"tool_result": "changed"}})):
                output = root / name
                with self.assertRaisesRegex(ValueError, "same private evidence"):
                    write_taxonomy_review_packets(traces, output, round_number=2,
                        prior_manifests=priors, private_evidence=changed)
                self.assertFalse(output.exists())

    def test_existing_annotation_packets_survive_repeated_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            traces = root / "traces.jsonl"
            JsonlTraceStore(traces).write([
                record("a" * 24, 1, SpanKind.TOOL, signature="same", tool="Read"),
                record("a" * 24, 2, SpanKind.TOOL, signature="same", tool="Read")])
            output = root / "packets"
            first = write_taxonomy_review_packets(traces, output)
            marked = False
            for packet in first["packets"]:
                path = Path(packet["source"])
                with path.open(newline="") as handle:
                    reader = csv.DictReader(handle)
                    fields, rows = reader.fieldnames, list(reader)
                if rows:
                    rows[0].update(assessment="Correct", notes="Retain this assessment.")
                    with path.open("w", newline="") as handle:
                        writer = csv.DictWriter(handle, fieldnames=fields)
                        writer.writeheader()
                        writer.writerows(rows)
                    marked = True
                    break
            self.assertTrue(marked)
            before = {path.name: path.read_bytes() for path in output.iterdir()}
            with self.assertRaisesRegex(ValueError, "earlier round|destination already exists"):
                write_taxonomy_review_packets(traces, output, prior_manifests=[
                    packet["manifest"] for packet in first["packets"]])
            self.assertEqual(before, {path.name: path.read_bytes()
                                      for path in output.iterdir()})
            late = root / "late-collision"
            late.mkdir()
            sentinel = late / "tool-failure-test-round1-manifest.json"
            sentinel.write_bytes(b"existing review")
            with self.assertRaisesRegex(ValueError, "destination already exists"):
                write_taxonomy_review_packets(traces, late)
            self.assertEqual([sentinel], list(late.iterdir()))
            self.assertEqual(b"existing review", sentinel.read_bytes())

    def test_existing_instructions_reject_before_packet_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            traces = root / "traces.jsonl"
            JsonlTraceStore(traces).write([])
            output = root / "packets"
            output.mkdir()
            path = output / "01-review-instructions.md"
            path.write_text("Existing review instructions.\n")
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "destination already exists"):
                write_taxonomy_review_packets(traces, output)
            self.assertEqual([path], list(output.iterdir()))
            self.assertEqual(before, path.read_bytes())

    def test_missing_signatures_do_not_create_annotation_cases(self):
        from retro_eval.taxonomy_packets import _candidates
        rows = [record("a" * 24, 1, SpanKind.TOOL, tool="Read"),
                record("a" * 24, 2, SpanKind.TOOL, tool="Edit")]
        self.assertEqual((), _candidates(rows, "duplicate_work"))

    def test_adaptive_round_rejects_changed_or_unknown_candidate_taxonomy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            traces = root / "traces.jsonl"
            JsonlTraceStore(traces).write([
                record("a" * 24, 1, SpanKind.TOOL, signature="same", tool="Read"),
                record("a" * 24, 2, SpanKind.TOOL, signature="same", tool="Read")])
            result = write_taxonomy_review_packets(traces, root / "first")
            prior = json.loads(Path(result["packets"][0]["manifest"]).read_text())
            self.assertEqual(7, prior["candidate_taxonomy_version"])
            for index, version in enumerate((None, 5, True)):
                changed = dict(prior)
                if version is None:
                    changed.pop("candidate_taxonomy_version")
                else:
                    changed["candidate_taxonomy_version"] = version
                path = root / ("prior-%s.json" % index)
                path.write_text(json.dumps(changed))
                output = root / ("next-%s" % index)
                with self.assertRaisesRegex(ValueError, "candidate taxonomy"):
                    write_taxonomy_review_packets(traces, output,
                        round_number=2, prior_manifests=(path,))
                self.assertFalse(output.exists())

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
            original_packets = {Path(packet[field]): Path(packet[field]).read_bytes()
                                for packet in result["packets"]
                                for field in ("source", "manifest")}
            original_packets[Path(result["instructions"])] = Path(result["instructions"]).read_bytes()
            second = write_taxonomy_review_packets(
                traces, output, round_number=2,
                prior_manifests=tuple(packet["manifest"]
                                      for packet in result["packets"]),
                size_overrides={
                    "duplicate_work": {"calibration": 1, "test": 1},
                    "tool_failure_kind": {"calibration": 1, "test": 1},
                })

            self.assertEqual(original_packets, {path: path.read_bytes()
                                                for path in original_packets})
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
                    self.assertEqual(7, manifest["annotation_packet_version"])
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
            # The fixture has no failed calls, so the failure packets are
            # empty and the run is flagged rather than reported clean.
            self.assertEqual(1, taxonomy_packets_main([
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
