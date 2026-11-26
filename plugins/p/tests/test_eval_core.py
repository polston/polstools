"""Contract tests for the local-first evaluation core."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_optional import requires  # noqa: E402
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

    def test_paired_interval_rejects_malformed_backend_bounds(self):
        class Backend:
            name = "authored"

            def __init__(self, result):
                self.result = result

            def interval(self, *args):
                return self.result

        for bounds in ((), (1,), (1, 2, 3), (False, 1), ("0", 1), (2, 1),
                       (float('inf'), 0), (0, float('-inf')),
                       (float('inf'), float('-inf'))):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                paired_effect_interval([0, 0], [1, 2], backend=Backend(bounds))
        result = paired_effect_interval([0, 0], [1, 1], backend=Backend((1, 1)))
        self.assertEqual(result['interval'], (1, 1))
        self.assertNotIn('interval_reason', result)

    def test_paired_interval_preserves_effect_when_bounds_are_nonfinite(self):
        class Backend:
            name = "authored-nonfinite"

            def __init__(self, bounds):
                self.bounds = bounds

            def interval(self, *args):
                return self.bounds

        for bounds in ((float('nan'), float('nan')), (0, float('inf')),
                       (float('-inf'), 10)):
            with self.subTest(bounds=bounds):
                result = paired_effect_interval([0, 0], [0, 10], backend=Backend(bounds))
                self.assertEqual(5, result["effect"])
                self.assertIsNone(result["interval"])
                self.assertEqual("nonfinite_backend_interval", result["interval_reason"])

    @requires("scipy")
    def test_real_bca_degenerate_sample_keeps_effect_without_fabricating_interval(self):
        import warnings
        from retro_eval.scipy_statistics import ScipyBcaBackend
        for value in (0, 10):
            with self.subTest(value=value), warnings.catch_warnings(record=True):
                result = paired_effect_interval([0] * 20, [value] * 20,
                    backend=ScipyBcaBackend(resamples=199), confidence=.95, seed=0)
                self.assertEqual(value, result["effect"])
                self.assertIsNone(result["interval"])
                self.assertEqual("nonfinite_backend_interval", result["interval_reason"])

    @requires("scipy")
    def test_scipy_bca_interval_agrees_with_a_stdlib_bootstrap_reference(self):
        import random
        import statistics
        from retro_eval.scipy_statistics import ScipyBcaBackend

        rng = random.Random(20261006)
        control = [rng.gauss(100, 15) for _ in range(40)]
        candidate = [value + rng.gauss(8, 10) for value in control]
        differences = [after - before for before, after in zip(control, candidate)]
        observed = statistics.fmean(differences)

        # Reference: percentile bootstrap of the mean, 20000 seeded resamples.
        reference_rng = random.Random(7)
        count = len(differences)
        means = sorted(
            statistics.fmean(reference_rng.choices(differences, k=count))
            for _ in range(20000))
        low_ref, high_ref = means[int(0.025 * 20000)], means[int(0.975 * 20000)]
        width = high_ref - low_ref

        result = paired_effect_interval(
            control, candidate, backend=ScipyBcaBackend(resamples=9999),
            statistic="mean", confidence=0.95, seed=42)
        low, high = result["interval"]
        self.assertAlmostEqual(observed, result["effect"])
        self.assertLessEqual(low, observed)
        self.assertGreaterEqual(high, observed)
        # BCa and percentile intervals differ slightly on a roughly symmetric
        # sample; each bound must sit within 10% of the reference width.
        self.assertAlmostEqual(low_ref, low, delta=0.10 * width)
        self.assertAlmostEqual(high_ref, high, delta=0.10 * width)
        self.assertEqual(result, paired_effect_interval(
            control, candidate, backend=ScipyBcaBackend(resamples=9999),
            statistic="mean", confidence=0.95, seed=42))


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

    def test_proposal_numeric_fields_preserve_types_and_finite_bounds(self):
        for field in ("population", "session_count", "independent_evidence_count"):
            for value in (True, False, -1, 1.5, float("nan"), float("inf"), "2"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.proposal(**{field: value})
        for field in ("confidence", "observed_rate", "avoidable_cost"):
            for value in (True, False, -1, float("nan"), float("inf"), "1", 10 ** 400):
                with self.subTest(field=field, value=str(value)), self.assertRaises(ValueError):
                    self.proposal(**{field: value})
        for field in ("confidence", "observed_rate"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.proposal(**{field: 1.01})
        self.assertIsNone(self.proposal(observed_rate=None).observed_rate)
        self.assertEqual(self.proposal(confidence=0, observed_rate=1, avoidable_cost=0).confidence, 0)
        self.assertEqual(self.proposal(population=0).suppression_reason(), "no eligible population")

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
