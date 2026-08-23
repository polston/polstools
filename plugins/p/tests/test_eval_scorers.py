import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.schema import SCHEMA_VERSION, SpanKind, TraceRecord  # noqa: E402
from retro_eval.catalog import load_metric_catalogue  # noqa: E402
from retro_eval.scorers import ScorerRegistry  # noqa: E402
from retro_eval.deterministic_scorers import (  # noqa: E402
    InputTokensPerOutcomeScorer, SkillLifecycleScorer,
)
from retro_eval.reporting import run_deterministic_report  # noqa: E402
from retro_eval.storage import JsonlTraceStore  # noqa: E402


def record(trace, sequence, kind, **values):
    base = dict(
        schema_version=SCHEMA_VERSION, trace_id=trace,
        span_id=f"{trace}-{sequence}", parent_span_id=None,
        source="fixture", adapter_version=1, source_version="1",
        span_kind=kind, started_at=None, sequence=sequence,
    )
    base.update(values)
    return TraceRecord(**base)


class ScorerRegistryTests(unittest.TestCase):
    def test_skill_lifecycle_reports_explicit_boundaries_not_opportunities(self):
        result = SkillLifecycleScorer(
            "skill_invocation_rate", minimum_n=1).score([
                record("a", 0, SpanKind.TRACE, status="complete"),
                record("a", 1, "retro.skill.start", attributes={
                    "invocation_id": "i1", "skill_id": "s1"}),
                record("a", 2, "retro.skill.end", attributes={
                    "invocation_id": "i1", "skill_id": "s1",
                    "outcome": "not_observable"}),
                record("b", 0, SpanKind.TRACE, status="complete"),
            ])
        self.assertEqual(0.5, result.value)
        self.assertEqual(1, result.details["starts"])
        self.assertEqual(1, result.details["ends"])
        self.assertEqual(0.0, result.details["unmatched_terminal_rate"])
        self.assertEqual("not_observable",
                         result.details["missed_trigger_rate"])
        self.assertEqual("not_observable",
                         result.details["opportunity_rate"])

    def test_skill_lifecycle_separates_missing_ends_from_orphan_terminals(self):
        result = SkillLifecycleScorer(
            "skill_invocation_rate", minimum_n=1).score([
                record("a", 0, SpanKind.TRACE, status="complete"),
                record("a", 1, "retro.skill.start", attributes={
                    "invocation_id": "started-only", "skill_id": "s1"}),
                record("a", 2, "retro.skill.end", attributes={
                    "invocation_id": "ended-only", "skill_id": "s2",
                    "outcome": "not_observable"}),
            ])
        self.assertEqual(1.0, result.details["unmatched_start_rate"])
        self.assertEqual(1.0, result.details["unmatched_terminal_rate"])
        self.assertEqual(0.0, result.details["lifecycle_completion_rate"])

        missing_end = SkillLifecycleScorer(
            "skill_invocation_rate", minimum_n=1).score([
                record("b", 0, SpanKind.TRACE, status="complete"),
                record("b", 1, "retro.skill.start", attributes={
                    "invocation_id": "started-only", "skill_id": "s1"}),
            ])
        self.assertEqual(1.0, missing_end.details["unmatched_terminal_rate"])
        self.assertEqual(0.0, missing_end.details["orphan_terminal_rate"])

    def test_profile_adds_scorers_without_orchestration_switch(self):
        profile = {
            "schema_version": 1,
            "scorers": [
                {"id": "repeat", "module": "retro_eval.deterministic_scorers",
                 "class": "RepeatedCallScorer", "options": {"minimum_n": 1,
                                                               "max_evidence_refs": 1}},
                {"id": "outcome", "module": "retro_eval.deterministic_scorers",
                 "class": "VerifiedOutcomeScorer", "options": {"minimum_n": 1}},
                {"id": "tool_failure", "module": "retro_eval.deterministic_scorers",
                 "class": "ToolFailureScorer", "options": {"minimum_n": 1}},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scorers.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            registry = ScorerRegistry.from_profile(path)
        records = [
            record("a", 0, SpanKind.TRACE, status="complete"),
            record("a", 1, SpanKind.TOOL, call_signature="same"),
            record("a", 2, SpanKind.TOOL, call_signature="same"),
            record("b", 0, SpanKind.TRACE, status="open"),
            record("b", 1, SpanKind.TOOL, call_signature="first"),
            record("a", 3, "retro.tool_result", status="error", tool_kind="Read"),
            record("a", 4, "retro.tool_result", status="ok", tool_kind="Read"),
        ]
        results = registry.score(records, capabilities={"tool_trajectory": True,
                                                        "tool_result_status": True,
                                                        "outcomes": True})
        by_id = {item.scorer_id: item for item in results}
        self.assertEqual(.5, by_id["repeat"].value)
        self.assertEqual(.5, by_id["outcome"].value)
        self.assertEqual(.5, by_id["tool_failure"].value)
        self.assertEqual({"failures": 1, "results": 2},
                         by_id["tool_failure"].details["by_tool"]["Read"])
        self.assertEqual(2, by_id["repeat"].eligible_population)
        self.assertEqual(1, by_id["repeat"].numerator)
        self.assertLess(by_id["repeat"].interval_low, .5)
        self.assertGreater(by_id["repeat"].interval_high, .5)
        self.assertEqual(1, len(by_id["repeat"].evidence_refs))
        self.assertEqual(1, by_id["repeat"].details["duplicate_by_tool"][""])

    def test_missing_capability_abstains_instead_of_reporting_zero(self):
        profile = {
            "schema_version": 1,
            "scorers": [{"id": "repeat", "module": "retro_eval.deterministic_scorers",
                         "class": "RepeatedCallScorer", "options": {"minimum_n": 1}}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scorers.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            result = ScorerRegistry.from_profile(path).score([], capabilities={})[0]
        self.assertTrue(result.abstained)
        self.assertIsNone(result.value)
        self.assertEqual("not_observable", result.label)

    def test_minimum_population_is_configuration(self):
        profile = {
            "schema_version": 1,
            "scorers": [{"id": "verified_outcome_rate", "module": "retro_eval.deterministic_scorers",
                         "class": "VerifiedOutcomeScorer", "options": {"minimum_n": 3}}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scorers.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            result = ScorerRegistry.from_profile(path).score(
                [record("a", 0, SpanKind.TRACE, status="complete")],
                capabilities={"outcomes": True},
            )[0]
        self.assertTrue(result.abstained)
        self.assertEqual("insufficient_evidence", result.label)

    def test_report_groups_capabilities_by_source_and_contains_manifest(self):
        profile = {
            "schema_version": 1,
            "scorers": [{"id": "verified_outcome_rate", "module": "retro_eval.deterministic_scorers",
                         "class": "VerifiedOutcomeScorer", "options": {"minimum_n": 1}}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "scorers.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            (root / "id-salt.bin").write_bytes(b"x" * 32)
            JsonlTraceStore(root / "traces.jsonl").write([
                record("a", 0, SpanKind.TRACE, source="one", status="complete",
                       started_at=datetime(2026, 8, 1, tzinfo=timezone.utc)),
                record("b", 0, SpanKind.TRACE, source="two", status="open",
                       started_at=datetime(2026, 8, 2, tzinfo=timezone.utc)),
            ])
            (root / "extraction.json").write_text(json.dumps({
                "schema_version": 1,
                "sources": {
                    "one": {"outcomes": {"state": "available"},
                            "source_set_sha256": "a" * 64},
                    "two": {"outcomes": {"state": "unavailable"},
                            "source_set_sha256": "b" * 64},
                },
                "included_traces": 2,
                "excluded_traces": 1,
                "exclusion_reasons": {"automation": 1},
            }), encoding="utf-8")
            report = run_deterministic_report(
                root, registry=ScorerRegistry.from_profile(profile_path),
                created_commit="abc123")
        self.assertFalse(report["sources"]["one"][0]["abstained"])
        self.assertEqual("not_observable", report["sources"]["two"][0]["label"])
        self.assertEqual(2, report["manifest"]["spans"])
        self.assertIn("trace_sha256", report["manifest"])
        self.assertNotIn(str(root), json.dumps(report))

        coverage = {item["metric_id"]: item for item in report["coverage"]["one"]}
        metric_count = len(load_metric_catalogue(
            PLUGIN_ROOT / "rubrics" / "metrics.json").metrics)
        self.assertEqual(metric_count, len(coverage))
        self.assertEqual("measured", coverage["verified_outcome_rate"]["status"])
        self.assertEqual(
            "not_observable", coverage["skill_chain_completion_rate"]["status"])
        self.assertEqual(
            ["skill_chaining"],
            coverage["skill_chain_completion_rate"]["missing_capabilities"],
        )
        self.assertEqual(metric_count, sum(
            report["coverage_summary"]["one"].values()))
        manifest = report["dataset_manifest"]
        self.assertEqual("abc123", manifest["created_commit"])
        self.assertEqual(2, manifest["population"])
        self.assertEqual(1, manifest["excluded_population"])
        self.assertEqual(["a" * 64, "b" * 64], manifest["source_fingerprints"])
        self.assertEqual(
            ["one", "two"], manifest["split_policy"]["fingerprint_source_order"])

    def test_cost_scorer_uses_injected_uncertainty_backend(self):
        class OracleBackend:
            name = "oracle"

            def interval(self, values, statistic, confidence, seed):
                self.values = tuple(values)
                return (10.0, 20.0)

        backend = OracleBackend()
        result = InputTokensPerOutcomeScorer(
            scorer_id="cost", minimum_n=2, interval_backend=backend,
            confidence=.95, seed=7, max_evidence_refs=1,
        ).score([
            record("a", 0, SpanKind.TRACE, status="complete", input_tokens=10),
            record("b", 0, SpanKind.TRACE, status="complete", input_tokens=20),
            record("c", 0, SpanKind.TRACE, status="open", input_tokens=100),
        ])
        self.assertEqual(15.0, result.value)
        self.assertEqual((10.0, 20.0), (result.interval_low, result.interval_high))
        self.assertEqual((10, 20), backend.values)
        self.assertEqual(2, result.eligible_population)


if __name__ == "__main__":
    unittest.main()
