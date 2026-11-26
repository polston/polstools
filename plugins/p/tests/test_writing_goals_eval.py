"""Draft-quality evaluation tests for `skills/writing-goals` across harnesses."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.catalog import load_rubric_catalogue
from retro_eval.goal_contracts import (
    DEFAULT_RUBRICS_PATH,
    RUBRIC_ID,
    GoalEvalScenario,
    build_goal_contract_judge,
    evaluate_goal_contract,
    run_goal_eval_suite,
    synthetic_goal_eval_suite,
)
from retro_eval.judge import JudgeCase


class _FakeExternalProvider:
    provider_id = "fake_external_provider"
    local = False

    def __init__(self, label="complete_contract"):
        self.label = label
        self.calls = 0

    def judge(self, request):
        self.calls += 1
        return {
            "label": self.label,
            "reason": "synthetic_provider_decision",
            "estimated_cost": 0.001,
        }


class WritingGoalsEvalSuiteTests(unittest.TestCase):
    def test_rubric_exists_and_matches_all_emitted_labels(self):
        catalogue = load_rubric_catalogue(DEFAULT_RUBRICS_PATH)
        by_id = {item.id: item for item in catalogue.rubrics}
        self.assertIn(RUBRIC_ID, by_id)
        rubric = by_id[RUBRIC_ID]

        suite = synthetic_goal_eval_suite()
        self.assertGreaterEqual(len(suite), 8)
        harnesses = {case.harness for case in suite}
        self.assertEqual({"claude", "codex", "antigravity"}, harnesses)

        for scenario in suite:
            evaluation = evaluate_goal_contract(scenario)
            with self.subTest(scenario=scenario.scenario_id):
                self.assertIn(evaluation.label, rubric.labels)

    def test_synthetic_suite_passes_direct_and_model_judge_both_directions(self):
        summary = run_goal_eval_suite()
        self.assertEqual(summary["rubric_id"], RUBRIC_ID)
        self.assertEqual(
            0,
            summary["mismatched"],
            f"Unexpected mismatches: {summary['mismatches']}",
        )
        self.assertEqual(summary["total"], summary["matched"])

        positive_count = sum(
            1 for r in summary["results"] if r["expected_label"] == "complete_contract"
        )
        negative_count = sum(
            1 for r in summary["results"] if r["expected_label"] != "complete_contract"
        )
        self.assertGreaterEqual(positive_count, 3)
        self.assertGreaterEqual(negative_count, 6)

    def test_abstains_on_unknown_harness_or_empty_contract(self):
        unknown = evaluate_goal_contract(
            GoalEvalScenario(
                scenario_id="bad_harness",
                harness="unknown_harness",
                contract_text="EVIDENCE: `pytest` exits 0.",
            )
        )
        self.assertEqual("ambiguous", unknown.label)
        self.assertFalse(unknown.passed)
        self.assertEqual(("invalid_harness",), tuple(d.code for d in unknown.defects))

        empty = evaluate_goal_contract(
            GoalEvalScenario(
                scenario_id="empty_text",
                harness="claude",
                contract_text="   ",
            )
        )
        self.assertEqual("ambiguous", empty.label)
        self.assertFalse(empty.passed)
        self.assertEqual(("empty_contract",), tuple(d.code for d in empty.defects))

    def test_detects_unreconciled_standing_release_rule(self):
        contract = (
            "EVIDENCE: `pytest tests/test_core.py` exits 0 with 0 failed.\n"
            "ARTIFACT: Updated `src/core.py`.\n"
            "CONSTRAINTS: Do not weaken tests.\n"
            "PARKED: State error, remedies, missing state, and resume check.\n"
            "BOUNDS: 10 turns; reaching the bound stops incomplete.\n"
        )
        result = evaluate_goal_contract(
            GoalEvalScenario(
                scenario_id="standing_rule_missed",
                harness="claude",
                contract_text=contract,
                has_standing_release_rule=True,
            )
        )
        self.assertFalse(result.passed)
        self.assertEqual("missing_authority_reconciliation", result.label)
        self.assertIn("unreconciled_standing_rule", {d.code for d in result.defects})

    def test_detects_bound_treated_as_completion_and_invented_phrase(self):
        contract = (
            "EVIDENCE: Run `echo GOAL_DONE` and confirm it looks clean.\n"
            "CONSTRAINTS: Keep scope bounded.\n"
            "PARKED: Stop on failure with resume check.\n"
            "BOUNDS: 10 turns; when the bound is reached, mark complete.\n"
        )
        result = evaluate_goal_contract(
            GoalEvalScenario(
                scenario_id="bound_and_phrase_bad",
                harness="claude",
                contract_text=contract,
            )
        )
        codes = {d.code for d in result.defects}
        self.assertFalse(result.passed)
        self.assertIn("invented_success_phrase", codes)
        self.assertIn("bound_as_completion", codes)

    def test_opt_in_external_provider_respects_privacy_boundary(self):
        provider = _FakeExternalProvider(label="complete_contract")
        judge = build_goal_contract_judge(provider=provider, allow_private_external=False)

        public_case = JudgeCase(
            case_id="pub_1",
            evidence_ref="synthetic://goal_contracts/pub_1",
            evidence="{}",
            data_classification="public_synthetic",
        )
        execution = judge.evaluate(public_case)
        self.assertEqual("complete_contract", execution.label)
        self.assertFalse(execution.abstained)
        self.assertEqual(1, provider.calls)

        private_case = JudgeCase(
            case_id="priv_1",
            evidence_ref="private://goal_contracts/priv_1",
            evidence="{}",
            data_classification="private_local",
        )
        with self.assertRaises(PermissionError):
            judge.evaluate(private_case)
        self.assertEqual(1, provider.calls)


if __name__ == "__main__":
    unittest.main()
