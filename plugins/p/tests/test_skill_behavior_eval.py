"""Tests for dual-axis behavioral skill evaluations and the proportionality workflow pack."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.catalog import load_rubric_catalogue
from retro_eval.judge import JudgeCase
from retro_eval.skill_behavior import (
    DEFAULT_RUBRICS_PATH,
    RUBRIC_ID,
    SkillBehaviorScenario,
    build_skill_behavior_judge,
    compare_skill_behavior_runs,
    evaluate_skill_behavior,
    load_external_scenarios,
    run_skill_behavior_suite,
    synthetic_skill_behavior_suite,
)
from retro_eval.workflow.packs import default_packs


class _FakeExternalProvider:
    provider_id = "fake_external_provider"
    local = False

    def __init__(self, label="balanced_pass"):
        self.label = label
        self.calls = 0

    def judge(self, request):
        self.calls += 1
        return {
            "label": self.label,
            "reason": "synthetic_provider_decision",
            "estimated_cost": 0.001,
        }


class DualAxisSkillBehaviorEvalTests(unittest.TestCase):
    def test_rubric_exists_and_matches_all_emitted_labels(self):
        catalogue = load_rubric_catalogue(DEFAULT_RUBRICS_PATH)
        by_id = {item.id: item for item in catalogue.rubrics}
        self.assertIn(RUBRIC_ID, by_id)
        rubric = by_id[RUBRIC_ID]

        suite = synthetic_skill_behavior_suite()
        self.assertGreaterEqual(len(suite), 12)
        self.assertEqual({"claude", "codex", "antigravity"}, {case.harness for case in suite})
        self.assertEqual(
            {"balanced_pass", "rigor_failure", "proportionality_failure", "dual_failure", "ambiguous"},
            {case.expected_label for case in suite},
        )
        for scenario in suite:
            evaluation = evaluate_skill_behavior(scenario)
            with self.subTest(scenario=scenario.scenario_id):
                self.assertIn(evaluation.label, rubric.labels)

    def test_synthetic_suite_passes_direct_and_model_judge(self):
        summary = run_skill_behavior_suite()
        self.assertEqual(summary["rubric_id"], RUBRIC_ID)
        self.assertEqual(0, summary["mismatched"], f"Mismatches: {summary['mismatches']}")
        self.assertEqual(summary["total"], summary["matched"])

    def test_single_mutation_anti_gaming_flips_exact_axis(self):
        base = synthetic_skill_behavior_suite()[0]
        self.assertEqual("balanced_pass", evaluate_skill_behavior(base).label)

        # Drop reproduction -> rigor_failure
        p_rigor = deepcopy(base.evidence_payload)
        p_rigor["rigor"]["reproduced_root_cause"] = False
        r_eval = evaluate_skill_behavior(
            SkillBehaviorScenario(
                scenario_id="mut_rigor",
                skill_name=base.skill_name,
                harness=base.harness,
                task_size=base.task_size,
                evidence_payload=p_rigor,
            )
        )
        self.assertEqual("rigor_failure", r_eval.label)
        self.assertIn("skipped_reproduction", r_eval.defect_codes)

        # Add 1 in-repo scratch file -> proportionality_failure (zero-tolerance)
        p_scratch = deepcopy(base.evidence_payload)
        p_scratch["proportionality"]["scratch_artifacts"] = [
            {"inside_repo": True, "file_count": 1, "total_bytes": 128}
        ]
        s_eval = evaluate_skill_behavior(
            SkillBehaviorScenario(
                scenario_id="mut_scratch",
                skill_name=base.skill_name,
                harness=base.harness,
                task_size=base.task_size,
                evidence_payload=p_scratch,
            )
        )
        self.assertEqual("proportionality_failure", s_eval.label)
        self.assertIn("repo_scratch_sprawl", s_eval.defect_codes)

        # Exceed compaction cap -> proportionality_failure
        p_comp = deepcopy(base.evidence_payload)
        p_comp["proportionality"]["compactions"] = 6
        c_eval = evaluate_skill_behavior(
            SkillBehaviorScenario(
                scenario_id="mut_compactions",
                skill_name=base.skill_name,
                harness=base.harness,
                task_size=base.task_size,
                evidence_payload=p_comp,
            )
        )
        self.assertEqual("proportionality_failure", c_eval.label)
        self.assertIn("disproportionate_ceremony", c_eval.defect_codes)

        # Combine rigor + proportionality defects -> dual_failure
        p_dual = deepcopy(p_rigor)
        p_dual["proportionality"]["reviewer_threads"] = [
            {"prior_task_count": 3, "age_hours": 12.0, "claimed_independent": True, "blinded": False}
        ]
        d_eval = evaluate_skill_behavior(
            SkillBehaviorScenario(
                scenario_id="mut_dual",
                skill_name=base.skill_name,
                harness=base.harness,
                task_size=base.task_size,
                evidence_payload=p_dual,
            )
        )
        self.assertEqual("dual_failure", d_eval.label)
        self.assertIn("skipped_reproduction", d_eval.defect_codes)
        self.assertIn("recycled_reviewer_thread", d_eval.defect_codes)

    def test_abstains_on_unknown_harness_or_task_size(self):
        bad_harness = evaluate_skill_behavior(
            SkillBehaviorScenario(
                scenario_id="bad_h",
                skill_name="x",
                harness="unknown",
                task_size="small",
                evidence_payload={"rigor": {}, "proportionality": {}},
            )
        )
        self.assertTrue(bad_harness.abstained)
        self.assertEqual("ambiguous", bad_harness.label)

        bad_size = evaluate_skill_behavior(
            SkillBehaviorScenario(
                scenario_id="bad_s",
                skill_name="x",
                harness="codex",
                task_size="giant",
                evidence_payload={"rigor": {}, "proportionality": {}},
            )
        )
        self.assertTrue(bad_size.abstained)
        self.assertEqual("ambiguous", bad_size.label)

    def test_compare_skill_behavior_runs_verdicts(self):
        pass_case = synthetic_skill_behavior_suite()[0]
        prop_fail = next(s for s in synthetic_skill_behavior_suite() if s.expected_label == "proportionality_failure")
        rigor_fail = next(s for s in synthetic_skill_behavior_suite() if s.expected_label == "rigor_failure")

        base_prop = run_skill_behavior_suite([
            SkillBehaviorScenario(
                scenario_id="case_1",
                skill_name="s",
                harness="codex",
                task_size=prop_fail.task_size,
                evidence_payload=prop_fail.evidence_payload,
                expected_label="balanced_pass",
            )
        ])
        cand_pass = run_skill_behavior_suite([
            SkillBehaviorScenario(
                scenario_id="case_1",
                skill_name="s",
                harness="codex",
                task_size=pass_case.task_size,
                evidence_payload=pass_case.evidence_payload,
                expected_label="balanced_pass",
            )
        ])
        cand_rigor = run_skill_behavior_suite([
            SkillBehaviorScenario(
                scenario_id="case_1",
                skill_name="s",
                harness="codex",
                task_size=rigor_fail.task_size,
                evidence_payload=rigor_fail.evidence_payload,
                expected_label="balanced_pass",
            )
        ])

        self.assertEqual("pareto_improved", compare_skill_behavior_runs(base_prop, cand_pass)["verdict"])
        self.assertEqual("regressed_proportionality", compare_skill_behavior_runs(cand_pass, base_prop)["verdict"])
        self.assertEqual("regressed_rigor", compare_skill_behavior_runs(cand_pass, cand_rigor)["verdict"])
        self.assertEqual("mixed_tradeoff", compare_skill_behavior_runs(base_prop, cand_rigor)["verdict"])
        self.assertEqual("no_change", compare_skill_behavior_runs(cand_pass, cand_pass)["verdict"])

    def test_privacy_boundary_blocks_external_provider_and_in_repo_or_symlink_paths(self):
        provider = _FakeExternalProvider()
        judge = build_skill_behavior_judge(provider=provider, allow_private_external=False)
        pub = JudgeCase(
            case_id="pub_1",
            evidence_ref="public_synthetic://skill_behavior/pub_1",
            evidence=synthetic_skill_behavior_suite()[0].to_evidence_json(),
            data_classification="public_synthetic",
        )
        self.assertEqual("balanced_pass", judge.evaluate(pub).label)
        self.assertEqual(1, provider.calls)

        priv = JudgeCase(
            case_id="priv_1",
            evidence_ref="private_local://skill_behavior/priv_1",
            evidence=synthetic_skill_behavior_suite()[0].to_evidence_json(),
            data_classification="private_local",
        )
        with self.assertRaises(PermissionError):
            judge.evaluate(priv)
        self.assertEqual(1, provider.calls)

        # In-repo path refusal
        in_repo = PLUGIN_ROOT / "rubrics" / "rubrics.json"
        with self.assertRaises(ValueError):
            load_external_scenarios(in_repo)

        # Symlink outside Git pointing inside Git -> refused
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "link.json"
            os.symlink(in_repo, link)
            with self.assertRaises(ValueError):
                load_external_scenarios(link)

            # Valid external JSONL -> forced private_local
            ext = Path(tmp) / "cases.jsonl"
            sample = synthetic_skill_behavior_suite()[0]
            ext.write_text(
                json.dumps({
                    "scenario_id": "ext_1",
                    "skill_name": "s",
                    "harness": "codex",
                    "task_size": "small",
                    "data_classification": "public_synthetic",
                    "evidence_payload": sample.evidence_payload,
                }) + "\n",
                encoding="utf-8",
            )
            loaded = load_external_scenarios(ext)
            self.assertEqual(1, len(loaded))
            self.assertEqual("private_local", loaded[0].data_classification)

    def test_cli_exit_codes_0_1_and_2(self):
        cli = PLUGIN_ROOT / "bin" / "retro-eval-skill-behavior"
        ok = subprocess.run(
            [sys.executable, "-B", str(cli), "run", "--synthetic"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(0, ok.returncode, ok.stderr)
        payload = json.loads(ok.stdout)
        self.assertEqual(0, payload["mismatched"])

        # Exit 2 on in-repo external path
        bad_repo = subprocess.run(
            [sys.executable, "-B", str(cli), "run", "--external-cases", str(PLUGIN_ROOT / "rubrics" / "rubrics.json")],
            text=True,
            capture_output=True,
        )
        self.assertEqual(2, bad_repo.returncode)

        # Exit 1 on failing external case and on regressed compare
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pass_s = synthetic_skill_behavior_suite()[0]
            fail_s = next(s for s in synthetic_skill_behavior_suite() if s.expected_label == "proportionality_failure")
            base_f = tmp_path / "base.jsonl"
            cand_f = tmp_path / "cand.jsonl"
            base_f.write_text(json.dumps({
                "scenario_id": "c1", "skill_name": "s", "harness": "codex",
                "task_size": pass_s.task_size, "evidence_payload": pass_s.evidence_payload,
                "expected_label": "balanced_pass",
            }) + "\n", encoding="utf-8")
            cand_f.write_text(json.dumps({
                "scenario_id": "c1", "skill_name": "s", "harness": "codex",
                "task_size": fail_s.task_size, "evidence_payload": fail_s.evidence_payload,
                "expected_label": "balanced_pass",
            }) + "\n", encoding="utf-8")
            base_out = tmp_path / "base_eval.json"
            cand_out = tmp_path / "cand_eval.json"

            r_pass = subprocess.run(
                [sys.executable, "-B", str(cli), "run", "--external-cases", str(base_f), "--output", str(base_out)],
                text=True, capture_output=True,
            )
            self.assertEqual(0, r_pass.returncode, r_pass.stderr)

            r_fail = subprocess.run(
                [sys.executable, "-B", str(cli), "run", "--external-cases", str(cand_f), "--output", str(cand_out)],
                text=True, capture_output=True,
            )
            self.assertEqual(1, r_fail.returncode)

            r_cmp = subprocess.run(
                [sys.executable, "-B", str(cli), "compare", "--baseline", str(base_out), "--candidate", str(cand_out)],
                text=True, capture_output=True,
            )
            self.assertEqual(1, r_cmp.returncode)
            self.assertEqual("regressed_proportionality", json.loads(r_cmp.stdout)["verdict"])

    def test_proportionality_workflow_pack_emits_leads_and_abstains_cleanly(self):
        packs = default_packs()
        self.assertIn("proportionality", packs.versions(["diagnosis", "proportionality"]))

        waste_records = [
            {"span_id": f"s_sub_{i}", "span_kind": "retro.tool_call", "tool_kind": "subagent", "call_signature": "send_message:reviewer_1"}
            for i in range(5)
        ] + [
            {"span_id": "s_edit_0", "span_kind": "retro.tool_call", "tool_kind": "edit", "call_signature": "edit:src/parser.py"},
        ] + [
            {"span_id": f"s_read_{i}", "span_kind": "retro.tool_call", "tool_kind": "read", "call_signature": "read:HANDOFF.md"}
            for i in range(14)
        ]
        waste_tools = {
            f"t_{i}": {"tool_input": f"cat data > repo/scratch/snapshot_{i}.json"}
            for i in range(4)
        }
        ctx = {
            "source": "codex",
            "source_key": "synthetic_trace_1",
            "capabilities": {},
            "records": waste_records,
            "excerpts": {"prompts": {}, "tools": waste_tools},
            "normalized_ref": {"artifact_id": "norm_1"},
            "excerpts_ref": {"artifact_id": "exc_1"},
        }
        res = packs.run(["proportionality"], ctx)[0]
        self.assertEqual("completed", res["status"])
        categories = {c["category"] for c in res["candidates"]}
        self.assertIn("repeated_subagent_dispatch_lead", categories)
        self.assertIn("repo_scratch_sprawl_lead", categories)
        self.assertIn("ceremony_overhead_lead", categories)

        # Clean trace with no subagent signals -> zero false leads + explicit limitation
        clean_ctx = {
            "source": "antigravity",
            "source_key": "synthetic_clean_1",
            "capabilities": {},
            "records": [
                {"span_id": "c1", "span_kind": "retro.tool_call", "tool_kind": "edit", "call_signature": "edit:src/parser.py"},
                {"span_id": "c2", "span_kind": "retro.tool_call", "tool_kind": "read", "call_signature": "read:src/parser.py"},
            ],
            "excerpts": {"prompts": {}, "tools": {"t0": {"tool_input": "echo ok > /tmp/scratch.txt"}}},
            "normalized_ref": {"artifact_id": "norm_2"},
            "excerpts_ref": {"artifact_id": "exc_2"},
        }
        clean_res = packs.run(["proportionality"], clean_ctx)[0]
        self.assertEqual([], clean_res["candidates"])
        self.assertTrue(any("unobserved" in lim for lim in clean_res["limitations"]))


if __name__ == "__main__":
    unittest.main()
