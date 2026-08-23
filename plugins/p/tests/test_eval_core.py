"""Contract tests for the local-first evaluation core."""

import sys
import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.dataset import DatasetManifest, stable_split  # noqa: E402
from retro_eval.proposals import Proposal, rank_proposals  # noqa: E402
from retro_eval.schema import (  # noqa: E402
    Capability,
    CapabilityState,
    LocalIdFactory,
    SpanKind,
    TraceRecord,
)
from retro_eval.statistics import (  # noqa: E402
    confusion_metrics, paired_effect_interval, wilson_interval,
)


T0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


class SchemaTests(unittest.TestCase):
    def test_local_ids_are_stable_but_installation_scoped(self):
        first = LocalIdFactory(b"installation-a").make("private/source/path")
        again = LocalIdFactory(b"installation-a").make("private/source/path")
        other = LocalIdFactory(b"installation-b").make("private/source/path")
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        self.assertNotIn("private", first)
        self.assertRegex(first, r"^[0-9a-f]{24}$")

    def test_trace_serialization_is_content_free_and_additive(self):
        record = TraceRecord(
            schema_version=1,
            trace_id="a" * 24,
            span_id="b" * 24,
            parent_span_id=None,
            source="codex",
            adapter_version=1,
            source_version="1.2.3",
            span_kind=SpanKind.TOOL,
            started_at=T0,
            sequence=3,
            input_tokens=100,
            cached_input_tokens=60,
            output_tokens=12,
            attributes={"codex.phase": "commentary", "future.field": 7},
        )
        payload = record.to_dict()
        text = repr(payload)
        self.assertNotIn("content", payload)
        self.assertNotIn("private/source", text)
        self.assertEqual(payload["input_tokens"], 100)
        self.assertEqual(payload["cached_input_tokens"], 60)
        self.assertEqual(payload["attributes"]["future.field"], 7)
        self.assertEqual(TraceRecord.from_dict(payload).to_dict(), payload)

    def test_capability_distinguishes_unavailable_from_zero(self):
        capability = Capability(CapabilityState.UNAVAILABLE, reason="not emitted")
        self.assertFalse(capability.observable)
        self.assertEqual(capability.to_dict()["state"], "unavailable")


class DatasetTests(unittest.TestCase):
    def test_split_is_stable_and_does_not_depend_on_input_order(self):
        ids = ["trace-%02d" % index for index in range(50)]
        forward = {item: stable_split(item, b"split-salt") for item in ids}
        backward = {item: stable_split(item, b"split-salt") for item in reversed(ids)}
        self.assertEqual(forward, backward)
        self.assertIn("calibration", set(forward.values()))
        self.assertIn("test", set(forward.values()))

    def test_manifest_rejects_open_windows_and_mixed_schema_versions(self):
        with self.assertRaises(ValueError):
            DatasetManifest(
                dataset_id="d1",
                schema_versions=(1, 2),
                adapter_versions={"claude": 1},
                start=T0,
                end=None,
                seed="seed",
            )

    def test_manifest_serializes_replay_contract_without_paths(self):
        manifest = DatasetManifest(
            dataset_id="d1", schema_versions=(1,), adapter_versions={"claude": 2},
            start=T0, end=datetime(2026, 8, 2, tzinfo=timezone.utc), seed="seed",
            inclusion_predicates=("direct_human_or_subagent",),
            exclusion_predicates=("active_session",), population=10,
            excluded_population=2, rubric_versions={"turn_intent": 1},
            scorer_versions={"repeated_call_rate": 1},
            source_fingerprints=("a" * 64,), content_policy="content_free",
            created_commit="abc123", split_policy={"calibration_share": 70},
        )
        payload = manifest.to_dict()
        self.assertEqual("content_free", payload["content_policy"])
        self.assertEqual(["a" * 64], payload["source_fingerprints"])
        self.assertNotIn("path", repr(payload).lower())


class StatisticsTests(unittest.TestCase):
    def test_confusion_metrics_report_population_and_abstentions(self):
        metrics = confusion_metrics(
            truth=[True, True, False, False, True],
            predicted=[True, None, True, False, False],
        )
        self.assertEqual(metrics["population"], 5)
        self.assertEqual(metrics["scored"], 4)
        self.assertEqual(metrics["abstained"], 1)
        self.assertEqual(metrics["tp"], 1)
        self.assertEqual(metrics["fp"], 1)
        self.assertEqual(metrics["tn"], 1)
        self.assertEqual(metrics["fn"], 1)
        self.assertAlmostEqual(metrics["precision"], 0.5)
        self.assertAlmostEqual(metrics["recall"], 0.5)
        self.assertAlmostEqual(metrics["f1"], 0.5)
        self.assertAlmostEqual(metrics["prevalence"], 0.5)
        self.assertAlmostEqual(metrics["agreement"], 0.5)
        self.assertAlmostEqual(metrics["kappa"], 0.0)

    def test_wilson_interval_contains_observed_rate(self):
        low, high = wilson_interval(6, 10)
        self.assertLess(low, 0.6)
        self.assertGreater(high, 0.6)
        self.assertGreaterEqual(low, 0.0)
        self.assertLessEqual(high, 1.0)

    def test_paired_effect_uses_injected_interval_backend(self):
        class OracleBackend:
            name = "oracle"

            def interval(self, values, statistic, confidence, seed):
                self.values = tuple(values)
                return (-0.25, 0.75)

        backend = OracleBackend()
        result = paired_effect_interval(
            [1, 2, 3], [2, 2, 5], backend=backend,
            statistic="mean", confidence=.95, seed=7,
        )
        self.assertEqual((1, 0, 2), backend.values)
        self.assertAlmostEqual(1.0, result["effect"])
        self.assertEqual((-0.25, 0.75), result["interval"])
        self.assertEqual("oracle", result["backend"])

    @unittest.skipUnless(importlib.util.find_spec("scipy"), "optional SciPy not installed")
    def test_scipy_bca_backend_is_seeded_and_contains_observed_mean(self):
        from retro_eval.scipy_statistics import ScipyBcaBackend

        backend = ScipyBcaBackend(resamples=999)
        first = paired_effect_interval(
            [5, 8, 13, 21, 34, 55], [4, 7, 10, 20, 30, 50],
            backend=backend, statistic="mean", seed=42,
        )
        second = paired_effect_interval(
            [5, 8, 13, 21, 34, 55], [4, 7, 10, 20, 30, 50],
            backend=backend, statistic="mean", seed=42,
        )
        self.assertEqual(first, second)
        self.assertLessEqual(first["interval"][0], first["effect"])
        self.assertGreaterEqual(first["interval"][1], first["effect"])


class ProposalTests(unittest.TestCase):
    def proposal(self, **overrides):
        values = {
            "proposal_id": "P1",
            "target_kind": "skill",
            "target_ref": "skill-a",
            "population": 20,
            "session_count": 3,
            "evidence_refs": ("e1", "e2"),
            "observed_rate": 0.4,
            "uncertainty": "95% CI 0.2..0.6",
            "expected_impact": "avoid repeated discovery",
            "exact_change": "expand the trigger description",
            "experiment": "replay the held-out trigger set",
            "success_threshold": "recall >= 0.85 at precision >= 0.80",
            "rollback": "restore the prior description",
            "confidence": 0.8,
            "avoidable_cost": 100.0,
        }
        values.update(overrides)
        return Proposal(**values)

    def test_incomplete_or_single_session_proposals_are_suppressed(self):
        self.assertEqual(self.proposal(session_count=1).suppression_reason(),
                         "insufficient sessions")
        self.assertEqual(self.proposal(exact_change="").suppression_reason(),
                         "no exact change")
        self.assertEqual(self.proposal(experiment="").suppression_reason(),
                         "no falsifiable experiment")

    def test_ranking_is_stable_and_components_remain_visible(self):
        low = self.proposal(proposal_id="low", confidence=0.5, avoidable_cost=10)
        high = self.proposal(proposal_id="high", confidence=0.9, avoidable_cost=100)
        ranked = rank_proposals([low, high])
        self.assertEqual([item.proposal_id for item in ranked], ["high", "low"])
        self.assertEqual(ranked[0].rank_components["confidence"], 0.9)
        self.assertEqual(ranked[0].status, "proposed")

    def test_proposal_serialization_carries_experiment_and_rollback_contract(self):
        item = self.proposal()
        payload = item.to_dict()
        required = {"population", "evidence_refs", "uncertainty",
                    "expected_impact", "exact_change", "experiment",
                    "success_threshold", "rollback", "status"}
        self.assertTrue(required.issubset(payload))
        self.assertNotIn("auto_apply", payload)
        self.assertEqual(3, payload["independent_evidence_count"])

    def test_non_session_evidence_never_falls_back_to_session_count(self):
        item = self.proposal(evidence_unit="source_measurement",
                             independent_evidence_count=0, session_count=20)
        self.assertEqual("insufficient independent evidence units",
                         item.suppression_reason())


if __name__ == "__main__":
    unittest.main()
