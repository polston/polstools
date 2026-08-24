"""Versioned metric, rubric, and evaluator-result contracts."""

import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.catalog import (  # noqa: E402
    ensure_rubric_use, load_annotation_protocol_catalogue,
    load_metric_catalogue, load_rubric_catalogue,
)
from retro_eval.predictors import load_predictor  # noqa: E402
from retro_eval.scoring import ScoreResult  # noqa: E402


RUBRICS = PLUGIN_ROOT / "rubrics"


class MetricCatalogueTests(unittest.TestCase):
    def test_every_required_domain_has_metrics_with_denominators(self):
        catalogue = load_metric_catalogue(RUBRICS / "metrics.json")
        required = {"skills", "prompts_rules_hooks", "agent_orchestration",
                    "context_cost", "clarity_process", "security_privacy", "outcomes"}
        self.assertEqual({metric.domain for metric in catalogue.metrics}, required)
        self.assertEqual(len({metric.id for metric in catalogue.metrics}),
                         len(catalogue.metrics))
        for metric in catalogue.metrics:
            self.assertGreater(metric.version, 0)
            self.assertTrue(metric.denominator)
            self.assertTrue(metric.eligible_population)
            self.assertTrue(metric.uncertainty_method)
            self.assertTrue(metric.known_biases)
            self.assertGreaterEqual(metric.minimum_n, 2)
        self.assertNotIn("quality_score", {metric.id for metric in catalogue.metrics})

    def test_catalogue_version_and_metric_versions_are_explicit(self):
        raw = json.loads((RUBRICS / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], 1)
        versions = {item["id"]: item["version"] for item in raw["metrics"]}
        self.assertEqual(3, versions["repeated_call_rate"])
        self.assertEqual(3, versions["tool_failure_rate"])
        self.assertEqual(2, versions["skill_invocation_rate"])
        self.assertTrue(all(version == 1 for metric_id, version in versions.items()
                            if metric_id not in {
                                "repeated_call_rate", "tool_failure_rate",
                                "skill_invocation_rate"}))

    def test_catalogue_and_definition_extensions_survive_loading(self):
        payload = json.loads((RUBRICS / "metrics.json").read_text(encoding="utf-8"))
        payload["owner"] = "local-evaluation"
        payload["metrics"][0]["vendor.annotation"] = {"kind": "experimental"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            catalogue = load_metric_catalogue(path)
        self.assertEqual("local-evaluation", catalogue.extensions["owner"])
        self.assertEqual(
            {"kind": "experimental"},
            catalogue.metrics[0].extensions["vendor.annotation"],
        )


class RubricCatalogueTests(unittest.TestCase):
    def test_required_rubrics_have_abstention_and_calibration_contracts(self):
        catalogue = load_rubric_catalogue(RUBRICS / "rubrics.json")
        required = {
            "human_provenance", "handoff_kind", "skill_opportunity",
            "duplicate_work", "turn_intent", "outcome_state",
            "prompt_contract_quality", "agent_prompt_contract",
            "subagent_result_use",
            "proposal_grounding", "security_event",
            "tool_failure_kind",
        }
        self.assertTrue(required.issubset({rubric.id for rubric in catalogue.rubrics}))
        for rubric in catalogue.rubrics:
            self.assertGreaterEqual(len(rubric.labels), 2)
            self.assertTrue(rubric.required_evidence)
            self.assertTrue(rubric.abstain_when)
            self.assertGreaterEqual(rubric.minimum_n, 2)
            self.assertGreater(rubric.target_precision, 0)
            self.assertGreater(rubric.target_recall, 0)
            self.assertGreater(rubric.target_agreement, 0)

    def test_uncalibrated_targets_are_explicitly_provisional(self):
        raw = json.loads((RUBRICS / "rubrics.json").read_text(encoding="utf-8"))
        self.assertEqual("provisional", raw["status"])
        statuses = {item["id"]: item["calibration_status"] for item in raw["rubrics"]}
        self.assertTrue(all(value in {"unvalidated", "calibration_only"}
                            for value in statuses.values()))
        self.assertEqual("calibration_only", statuses["turn_friction_legacy"])

    def test_legacy_rubric_is_machine_gated_to_non_decision_uses(self):
        catalogue = load_rubric_catalogue(RUBRICS / "rubrics.json")
        rubric = next(item for item in catalogue.rubrics
                      if item.id == "turn_friction_legacy")
        ensure_rubric_use(rubric, "candidate_sampling")
        ensure_rubric_use(rubric, "scorer_validation")
        with self.assertRaisesRegex(ValueError, "decision_support"):
            ensure_rubric_use(rubric, "decision_support")

    def test_agent_prompt_metric_has_an_explicit_observability_contract(self):
        catalogue = load_metric_catalogue(RUBRICS / "metrics.json")
        metric = next(item for item in catalogue.metrics
                      if item.id == "agent_prompt_contract_rate")
        self.assertEqual(
            ("subagent_identity", "agent_prompt_structure"),
            metric.source_capabilities,
        )
        self.assertIn("observable", metric.eligible_population)

    def test_human_prompt_quality_has_a_separate_metric_contract(self):
        catalogue = load_metric_catalogue(RUBRICS / "metrics.json")
        metric = next(item for item in catalogue.metrics
                      if item.id == "prompt_contract_gap_rate")
        self.assertEqual("prompts_rules_hooks", metric.domain)
        self.assertEqual(("human_provenance",), metric.source_capabilities)
        self.assertEqual("prompt-contract-v1", metric.validation_dataset)

    def test_configured_deterministic_predictor_is_loadable(self):
        catalogue = load_rubric_catalogue(RUBRICS / "rubrics.json")
        rubric = next(item for item in catalogue.rubrics
                      if item.id == "turn_friction_legacy")
        predictor = load_predictor(rubric.extensions["deterministic_predictor"])
        self.assertEqual("question", predictor({
            "context_chars": "500", "user_turn": "why?",
        }))

    def test_annotation_protocol_is_complete_and_matches_the_rubric(self):
        rubrics = load_rubric_catalogue(RUBRICS / "rubrics.json")
        protocols = load_annotation_protocol_catalogue(
            RUBRICS / "annotation-protocols.json", rubrics)
        protocol = protocols.get("turn-friction-dominant-intent", 2)
        rubric = next(item for item in rubrics.rubrics
                      if item.id == protocol.rubric_id)
        self.assertEqual(rubric.version, protocol.rubric_version)
        self.assertEqual(set(rubric.labels), set(protocol.labels))
        self.assertEqual(set(protocol.labels), set(protocol.decision_order))
        self.assertEqual(set(protocol.labels), set(protocol.definitions))
        self.assertIn("{context}", protocol.prompt_template)
        self.assertIn("{user_turn}", protocol.prompt_template)
        self.assertTrue(protocol.human_instruction)
        self.assertEqual(set(protocol.labels), set(protocol.label_prompts))
        self.assertEqual("Accept it and continue",
                         protocol.label_prompts["approval"]["action"])
        self.assertEqual(64, len(protocol.sha256))

    def test_taxonomy_protocols_are_bound_to_adaptive_rubrics(self):
        rubrics = load_rubric_catalogue(RUBRICS / "rubrics.json")
        protocols = load_annotation_protocol_catalogue(
            RUBRICS / "annotation-protocols.json", rubrics)
        duplicate = next(item for item in rubrics.rubrics
                         if item.id == "duplicate_work")
        failure = next(item for item in rubrics.rubrics
                       if item.id == "tool_failure_kind")

        self.assertEqual(3, duplicate.version)
        self.assertEqual(2, failure.version)
        for rubric, protocol_id, protocol_version in (
                (duplicate, "duplicate-work-taxonomy", 3),
                (failure, "tool-failure-taxonomy", 2)):
            plan = rubric.extensions["adaptive_sampling"]
            self.assertGreaterEqual(plan["maximum_rounds"], 2)
            self.assertGreaterEqual(plan["minimum_heldout_per_label"], 20)
            protocol = protocols.get(protocol_id, protocol_version)
            self.assertEqual(rubric.version, protocol.rubric_version)
            self.assertEqual(set(rubric.labels), set(protocol.labels))

    def test_annotation_protocol_rejects_incomplete_decision_order(self):
        rubrics = load_rubric_catalogue(RUBRICS / "rubrics.json")
        payload = json.loads((RUBRICS / "annotation-protocols.json").read_text(
            encoding="utf-8"))
        payload["protocols"][0]["decision_order"] = ["interrupt"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "protocols.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "decision order"):
                load_annotation_protocol_catalogue(path, rubrics)


class ScoreResultTests(unittest.TestCase):
    def test_score_result_keeps_observability_and_execution_evidence(self):
        score = ScoreResult(
            scorer_id="tool_error",
            scorer_version=1,
            scope="span",
            value=1.0,
            label="error",
            abstained=False,
            reason="failed result observed",
            evidence_refs=("e1",),
            population=10,
            eligible_population=8,
            latency_ms=3,
            estimated_cost=0.0,
            limitations=("source version floor",),
        )
        payload = score.to_dict()
        self.assertEqual(payload["eligible_population"], 8)
        self.assertEqual(payload["scorer_version"], 1)
        self.assertNotIn("prompt", payload)
        self.assertNotIn("content", payload)

    def test_abstention_cannot_carry_a_numeric_score(self):
        with self.assertRaises(ValueError):
            ScoreResult(
                scorer_id="unknown", scorer_version=1, scope="trace",
                value=0.0, label="", abstained=True, reason="not observable",
                evidence_refs=(), population=1, eligible_population=0,
                latency_ms=0, estimated_cost=0.0, limitations=(),
            )


class CrossCatalogueTests(unittest.TestCase):
    def test_scorer_profiles_reference_metrics_and_share_minimum_population(self):
        metrics = {item.id: item for item in
                   load_metric_catalogue(RUBRICS / "metrics.json").metrics}
        profile = json.loads((RUBRICS / "scorers.json").read_text(encoding="utf-8"))
        for scorer in profile["scorers"]:
            self.assertIn(scorer["id"], metrics)
            self.assertEqual(metrics[scorer["id"]].minimum_n,
                             scorer["options"]["minimum_n"])


if __name__ == "__main__":
    unittest.main()
