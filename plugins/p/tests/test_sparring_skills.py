"""Tests for the canonical cross-harness sparring-skills meta-skill."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "sparring-skills"
HELPER = SKILL_ROOT / "scripts" / "skill_spar.py"


def _run_helper(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-B", str(HELPER), *args],
        cwd=cwd or PLUGIN_ROOT,
        text=True,
        capture_output=True,
    )


class SparringSkillsTests(unittest.TestCase):
    def test_self_check_passes_in_source_and_relocated_copy(self):
        r1 = _run_helper("self-check")
        self.assertEqual(0, r1.returncode, r1.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            relocated = Path(tmp) / "relocated_p"
            shutil.copytree(PLUGIN_ROOT, relocated)
            rel_helper = relocated / "skills" / "sparring-skills" / "scripts" / "skill_spar.py"
            r2 = subprocess.run(
                [sys.executable, "-B", str(rel_helper), "self-check"],
                cwd=relocated,
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, r2.returncode, r2.stderr)

    def test_init_refuses_in_repo_work_dir_and_emits_isolated_packets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            brief = tmp_path / "brief.md"
            brief.write_text("Build a bounded parser utility.\n", encoding="utf-8")

            # In-repo refusal (exit 2)
            bad = _run_helper(
                "init",
                "--work-dir", str(PLUGIN_ROOT / "tmp_forbidden"),
                "--stage", "plan",
                "--skill-dir", str(PLUGIN_ROOT / "skills" / "writing-plans"),
                "--brief", str(brief),
                "--harness", "antigravity",
            )
            self.assertEqual(2, bad.returncode)

            # Symlink pointing into repo -> refused (exit 2)
            link = tmp_path / "repo_link"
            os.symlink(PLUGIN_ROOT, link)
            bad_link = _run_helper(
                "init",
                "--work-dir", str(link / "tmp_forbidden"),
                "--stage", "plan",
                "--skill-dir", str(PLUGIN_ROOT / "skills" / "writing-plans"),
                "--brief", str(brief),
                "--harness", "antigravity",
            )
            self.assertEqual(2, bad_link.returncode)

            # Valid external work dir
            work = tmp_path / "spar_run"
            ok = _run_helper(
                "init",
                "--work-dir", str(work),
                "--stage", "plan",
                "--skill-dir", str(PLUGIN_ROOT / "skills" / "writing-plans"),
                "--brief", str(brief),
                "--harness", "antigravity",
                "--round-id", "plan-r1",
            )
            self.assertEqual(0, ok.returncode, ok.stderr)
            packet = json.loads(ok.stdout)
            self.assertEqual("plan-r1", packet["round_id"])
            self.assertIn("Do NOT read or follow the candidate skill", packet["control_prompt"])
            self.assertIn("Read and follow the candidate skill", packet["treatment_prompt"])

    def test_anonymize_leak_detection_permissions_and_unblind_tamper_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            brief = tmp_path / "brief.md"
            brief.write_text("Design a compact parser.\n", encoding="utf-8")
            work = tmp_path / "spar"
            init_r = _run_helper(
                "init",
                "--work-dir", str(work),
                "--stage", "plan",
                "--skill-dir", str(PLUGIN_ROOT / "skills" / "writing-plans"),
                "--brief", str(brief),
                "--harness", "codex",
                "--round-id", "r1",
            )
            self.assertEqual(0, init_r.returncode, init_r.stderr)
            round_dir = work / "r1"
            ctrl = round_dir / "control.md"
            treat = round_dir / "treatment.md"

            # Blinding leak fails closed (exit 1)
            ctrl.write_text("This is the control arm output.\n", encoding="utf-8")
            treat.write_text("# Clean Plan\nStep 1: verify parser.\n", encoding="utf-8")
            leaked = _run_helper(
                "anonymize",
                "--round-dir", str(round_dir),
                "--control-file", str(ctrl),
                "--treatment-file", str(treat),
                "--seed", "7",
            )
            self.assertEqual(1, leaked.returncode)
            self.assertEqual("blinding_leak_detected", json.loads(leaked.stdout)["status"])

            # --strip-leaks succeeds and sets blinding_sanitized=True + 0o600 key perms
            stripped = _run_helper(
                "anonymize",
                "--round-dir", str(round_dir),
                "--control-file", str(ctrl),
                "--treatment-file", str(treat),
                "--seed", "7",
                "--strip-leaks",
            )
            self.assertEqual(0, stripped.returncode, stripped.stderr)
            anon_out = json.loads(stripped.stdout)
            self.assertTrue(anon_out["blinding_sanitized"])
            key_mode = stat.S_IMODE((round_dir / "sealed-key.json").stat().st_mode)
            self.assertEqual(0o600, key_mode)

            # Judge packets (default 2 judges)
            jp = _run_helper("judge-packet", "--round-dir", str(round_dir))
            self.assertEqual(0, jp.returncode, jp.stderr)
            jp_data = json.loads((round_dir / "judge-packets.json").read_text(encoding="utf-8"))
            self.assertEqual(2, len(jp_data["requests"]))

            # Tampered artifacts_sha256 -> exit 2
            bad_resp = round_dir / "bad_responses.json"
            bad_resp.write_text(json.dumps([
                {
                    "request_id": req["request_id"],
                    "artifacts_sha256": "0" * 64,
                    "axis_a_winner": "X",
                    "axis_b_winner": "X",
                    "overall_winner": "X",
                    "candidate_x_defects": [],
                    "candidate_y_defects": [],
                    "concrete_skill_gaps": [],
                    "unchecked": [],
                }
                for req in jp_data["requests"]
            ]), encoding="utf-8")
            tampered = _run_helper("unblind-score", "--round-dir", str(round_dir), "--responses-file", str(bad_resp))
            self.assertEqual(2, tampered.returncode)

            # Split verdict surfaced explicitly
            split_resp = round_dir / "split_responses.json"
            split_resp.write_text(json.dumps([
                {
                    "request_id": jp_data["requests"][0]["request_id"],
                    "artifacts_sha256": jp_data["artifacts_sha256"],
                    "axis_a_winner": "X",
                    "axis_b_winner": "Y",
                    "overall_winner": "X",
                    "candidate_x_defects": [],
                    "candidate_y_defects": [],
                    "concrete_skill_gaps": ["tighten check"],
                    "unchecked": [],
                },
                {
                    "request_id": jp_data["requests"][1]["request_id"],
                    "artifacts_sha256": jp_data["artifacts_sha256"],
                    "axis_a_winner": "Y",
                    "axis_b_winner": "Y",
                    "overall_winner": "Y",
                    "candidate_x_defects": [],
                    "candidate_y_defects": [],
                    "concrete_skill_gaps": [],
                    "unchecked": [],
                },
            ]), encoding="utf-8")
            scored = _run_helper("unblind-score", "--round-dir", str(round_dir), "--responses-file", str(split_resp))
            self.assertEqual(0, scored.returncode, scored.stderr)
            verdict = json.loads(scored.stdout)
            self.assertTrue(verdict["contested"])
            self.assertEqual("tie", verdict["consensus_winner"])

    def test_edit_gate_enforces_byte_cap_delete_first_diff_regression_and_privacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            before = tmp_path / "before"
            after = tmp_path / "after"
            before.mkdir()
            after.mkdir()
            base_text = "---\nname: demo-skill\ndescription: Demo skill.\n---\n# Demo\nRule one.\n"
            (before / "SKILL.md").write_text(base_text, encoding="utf-8")
            (after / "SKILL.md").write_text(base_text + "Rule two for general validation.\n", encoding="utf-8")

            # Net growth without --tightened-clause or --regression-test -> exit 1
            r_unbacked = _run_helper(
                "edit-gate",
                "--before-skill-dir", str(before),
                "--after-skill-dir", str(after),
                "--rationale", "General validation rule.",
            )
            self.assertEqual(1, r_unbacked.returncode)
            v1 = json.loads(r_unbacked.stdout)["violations"]
            self.assertTrue(any("--tightened-clause" in v for v in v1))
            self.assertTrue(any("--regression-test" in v for v in v1))

            # Growth WITH tightened clause + diff-verified regression test -> exit 0
            good_diff = tmp_path / "good.diff"
            good_diff.write_text(
                "diff --git a/plugins/p/tests/test_demo.py b/plugins/p/tests/test_demo.py\n"
                "+++ b/plugins/p/tests/test_demo.py\n"
                "+def test_rule_two(): pass\n"
                "+++ b/plugins/p/skills/demo/SKILL.md\n"
                "+Rule two for general validation.\n",
                encoding="utf-8",
            )
            r_ok = _run_helper(
                "edit-gate",
                "--before-skill-dir", str(before),
                "--after-skill-dir", str(after),
                "--diff-file", str(good_diff),
                "--tightened-clause", "Compressed redundant intro clause.",
                "--regression-test", "plugins/p/tests/test_demo.py",
                "--rationale", "General validation rule.",
            )
            self.assertEqual(0, r_ok.returncode, r_ok.stdout + r_ok.stderr)

            # Reject private home path & dated war story in diff -> exit 1
            bad_diff = tmp_path / "bad.diff"
            sample_home = "/" + "Users" + "/example/work"
            bad_diff.write_text(
                "+++ b/plugins/p/tests/test_demo.py\n"
                "+++ b/plugins/p/skills/demo/SKILL.md\n"
                f"+Incident on 2026-11-26 in {sample_home}.\n",
                encoding="utf-8",
            )
            r_priv = _run_helper(
                "edit-gate",
                "--before-skill-dir", str(before),
                "--after-skill-dir", str(after),
                "--diff-file", str(bad_diff),
                "--tightened-clause", "Tightened.",
                "--regression-test", "plugins/p/tests/test_demo.py",
                "--rationale", "General rule.",
            )
            self.assertEqual(1, r_priv.returncode)

            # Reject byte-cap overflow -> exit 1
            (after / "SKILL.md").write_text(base_text + ("x" * 9000) + "\n", encoding="utf-8")
            r_cap = _run_helper(
                "edit-gate",
                "--before-skill-dir", str(before),
                "--after-skill-dir", str(after),
                "--diff-file", str(good_diff),
                "--tightened-clause", "Tightened.",
                "--regression-test", "plugins/p/tests/test_demo.py",
                "--rationale", "General rule.",
                "--max-bytes", "8192",
            )
            self.assertEqual(1, r_cap.returncode)
            self.assertTrue(any("exceeds max_skill_bytes" in v for v in json.loads(r_cap.stdout)["violations"]))

    def test_chain_advance_records_sha256_and_emits_pilot_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            work = tmp_path / "chain"
            art = tmp_path / "winning-plan.md"
            art.write_text("# Winning Plan\n1. Ship clean code.\n", encoding="utf-8")

            r1 = _run_helper(
                "chain-advance",
                "--work-dir", str(work),
                "--stage", "plan",
                "--next-stage", "goal",
                "--selected-arm", "treatment",
                "--promoted-artifact", str(art),
            )
            self.assertEqual(0, r1.returncode, r1.stderr)
            r2 = _run_helper(
                "chain-advance",
                "--work-dir", str(work),
                "--stage", "execute",
                "--next-stage", "pilot-handoff",
                "--selected-arm", "synthesized",
                "--promoted-artifact", str(art),
                "--proposal-id", "sparring-demo-v1",
            )
            self.assertEqual(0, r2.returncode, r2.stderr)
            out = json.loads(r2.stdout)
            draft = json.loads(Path(out["pilot_draft"]).read_text(encoding="utf-8"))
            self.assertEqual("sparring-demo-v1", draft["proposal_identity"])
            self.assertEqual("insufficient_evidence", draft["initial_disposition"])

    def _init_temp_git_repo(self, repo_dir: Path, *, commit: bool = True, branch: str = "main") -> dict:
        repo_dir.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(repo_dir.parent),
        }
        email = "tester" + chr(64) + "example.invalid"
        subprocess.run(["git", "-C", str(repo_dir), "init", "-q", "-b", branch], env=env, check=True)
        if commit:
            (repo_dir / "README.md").write_text("# Demo\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo_dir), "add", "README.md"], env=env, check=True)
            subprocess.run(
                ["git", "-C", str(repo_dir), "-c", "user.name=tester", "-c", f"user.email={email}",
                 "commit", "-q", "-m", "initial"],
                env=env,
                check=True,
            )
        return env

    def test_resolve_defaults_routes_precedence_and_git_edge_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            env = self._init_temp_git_repo(repo)
            email = "tester" + chr(64) + "example.invalid"

            # 1. Clean main + untracked docs/plans note -> zero context -> exit 1 (needs_clarification)
            (repo / "docs" / "plans").mkdir(parents=True)
            (repo / "docs" / "plans" / "old-plan.md").write_text("# Old untracked plan\n", encoding="utf-8")
            r_none = _run_helper("resolve-defaults", "--workspace-dir", str(repo))
            self.assertEqual(1, r_none.returncode, r_none.stderr)
            d_none = json.loads(r_none.stdout)
            self.assertEqual("needs_clarification", d_none["status"])
            self.assertEqual(1, d_none["max_clarifying_questions"])

            # 2. Clean main + --task-summary -> default chain (writing-plans -> writing-goals)
            r_chain = _run_helper(
                "resolve-defaults", "--workspace-dir", str(repo), "--task-summary", "Refactor parser"
            )
            self.assertEqual(0, r_chain.returncode, r_chain.stderr)
            d_chain = json.loads(r_chain.stdout)
            self.assertEqual("no_plan_yet_default_chain", d_chain["route"])
            self.assertEqual("chain", d_chain["mode"])
            self.assertEqual(["writing-plans", "writing-goals"], [s["skill"] for s in d_chain["steps"]])
            self.assertTrue(d_chain["approval_boundary"]["single_consolidated_prompt"])
            self.assertEqual("pause_for_selection", d_chain["contested_chain_policy"])

            # 3. Inline conversation plan (--has-plan) -> writing-goals
            r_has_plan = _run_helper("resolve-defaults", "--workspace-dir", str(repo), "--has-plan")
            self.assertEqual(0, r_has_plan.returncode, r_has_plan.stderr)
            d_has_plan = json.loads(r_has_plan.stdout)
            self.assertEqual("existing_plan_in_context", d_has_plan["route"])
            self.assertEqual(["writing-goals"], [s["skill"] for s in d_has_plan["steps"]])

            # 4. --plan-file on disk -> writing-goals; missing --plan-file -> exit 2
            pf = tmp_path / "active-plan.md"
            pf.write_text("# Active Plan\n", encoding="utf-8")
            r_pf = _run_helper("resolve-defaults", "--plan-file", str(pf))
            self.assertEqual(0, r_pf.returncode, r_pf.stderr)
            self.assertEqual("existing_plan_in_context", json.loads(r_pf.stdout)["route"])
            r_pf_bad = _run_helper("resolve-defaults", "--plan-file", str(tmp_path / "missing.md"))
            self.assertEqual(2, r_pf_bad.returncode)

            # 5. Tracked modified plan file -> existing_plan_in_context
            tracked_plan = repo / "docs" / "active-plan.md"
            tracked_plan.write_text("# Plan v1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "docs/active-plan.md"], env=env, check=True)
            subprocess.run(
                ["git", "-C", str(repo), "-c", "user.name=tester", "-c", f"user.email={email}",
                 "commit", "-q", "-m", "add plan"],
                env=env,
                check=True,
            )
            tracked_plan.write_text("# Plan v2\n", encoding="utf-8")
            r_tp = _run_helper("resolve-defaults", "--workspace-dir", str(repo))
            self.assertEqual(0, r_tp.returncode, r_tp.stderr)
            self.assertEqual("existing_plan_in_context", json.loads(r_tp.stdout)["route"])

            # 6. Deleted tracked plan file is excluded by --diff-filter=ACMR (no false match)
            tracked_plan.unlink()
            r_del = _run_helper("resolve-defaults", "--workspace-dir", str(repo))
            self.assertEqual(1, r_del.returncode, r_del.stderr)
            subprocess.run(["git", "-C", str(repo), "checkout", "--", "docs/active-plan.md"], env=env, check=True)

            # 7. Target skill & recursive_guard & missing skill (exit 2)
            r_self = _run_helper("resolve-defaults", "--target-skill", "sparring-skills")
            self.assertEqual(0, r_self.returncode, r_self.stderr)
            d_self = json.loads(r_self.stdout)
            self.assertEqual("target_skill_in_context", d_self["route"])
            self.assertTrue(d_self["recursive_guard"])
            self.assertEqual("skill", d_self["steps"][0]["stage"])

            r_rev = _run_helper("resolve-defaults", "--target-skill", "adequacy-review")
            self.assertEqual(0, r_rev.returncode, r_rev.stderr)
            self.assertEqual("review", json.loads(r_rev.stdout)["steps"][0]["stage"])

            r_missing = _run_helper("resolve-defaults", "--target-skill", "no-such-skill")
            self.assertEqual(2, r_missing.returncode)

            # 8. Single vs multiple tracked modified skills in workspace
            s1 = repo / "plugins" / "p" / "skills" / "robust-over-simple" / "SKILL.md"
            s2 = repo / "plugins" / "p" / "skills" / "adequacy-review" / "SKILL.md"
            s1.parent.mkdir(parents=True)
            s2.parent.mkdir(parents=True)
            s1.write_text("v1\n", encoding="utf-8")
            s2.write_text("v1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "."], env=env, check=True)
            subprocess.run(
                ["git", "-C", str(repo), "-c", "user.name=tester", "-c", f"user.email={email}",
                 "commit", "-q", "-m", "add skills"],
                env=env,
                check=True,
            )
            s1.write_text("v2\n", encoding="utf-8")
            r_one_sk = _run_helper("resolve-defaults", "--workspace-dir", str(repo))
            self.assertEqual(0, r_one_sk.returncode, r_one_sk.stderr)
            d_one_sk = json.loads(r_one_sk.stdout)
            self.assertEqual("target_skill_in_context", d_one_sk["route"])
            self.assertEqual("robust-over-simple", d_one_sk["steps"][0]["skill"])

            s2.write_text("v2\n", encoding="utf-8")
            r_two_sk = _run_helper("resolve-defaults", "--workspace-dir", str(repo))
            self.assertEqual(1, r_two_sk.returncode, r_two_sk.stderr)
            self.assertEqual("ambiguous_target_skills", json.loads(r_two_sk.stdout)["route"])

            # 9. Unborn HEAD repo with staged file degrades gracefully without exit 128 crash
            unborn = tmp_path / "unborn"
            u_env = self._init_temp_git_repo(unborn, commit=False, branch="feat-new")
            (unborn / "app.py").write_text("print(1)\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(unborn), "add", "app.py"], env=u_env, check=True)
            r_unborn = _run_helper("resolve-defaults", "--workspace-dir", str(unborn))
            self.assertEqual(0, r_unborn.returncode, r_unborn.stderr)
            self.assertEqual("no_plan_yet_default_chain", json.loads(r_unborn.stdout)["route"])

    def test_edit_gate_per_markdown_cap_scripts_exclusion_and_aux_privacy_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            before = tmp_path / "before"
            after = tmp_path / "after"
            (before / "scripts").mkdir(parents=True)
            (after / "scripts" / "__pycache__").mkdir(parents=True)
            (after / "references").mkdir(parents=True)
            base_text = "---\nname: demo-skill\ndescription: Demo.\n---\n# Demo\nOld verbose line.\n"
            (before / "SKILL.md").write_text(base_text, encoding="utf-8")
            (after / "SKILL.md").write_text("---\nname: demo-skill\ndescription: Demo.\n---\n# Demo\nShort.\n", encoding="utf-8")
            (after / "scripts" / "tool.py").write_text("# " + ("a" * 20000) + "\n", encoding="utf-8")
            (after / "scripts" / "__pycache__" / "tool.pyc").write_bytes(b"\x00" * 20000)

            r_ok = _run_helper(
                "edit-gate",
                "--before-skill-dir", str(before),
                "--after-skill-dir", str(after),
                "--rationale", "General tightening.",
            )
            self.assertEqual(0, r_ok.returncode, r_ok.stdout + r_ok.stderr)
            receipt = json.loads(r_ok.stdout)
            self.assertLessEqual(receipt["max_markdown_file_bytes"], 8192)

            # Auxiliary markdown privacy leak caught even when --diff-file is omitted
            sample_home = "/" + "Users" + "/example/work"
            (after / "references" / "adapter.md").write_text(f"# Adapter\nPath: {sample_home}\n", encoding="utf-8")
            r_priv = _run_helper(
                "edit-gate",
                "--before-skill-dir", str(before),
                "--after-skill-dir", str(after),
                "--rationale", "General rule.",
            )
            self.assertEqual(1, r_priv.returncode)
            self.assertTrue(any("private pattern" in v for v in json.loads(r_priv.stdout)["violations"]))

            # Oversized auxiliary markdown (>8192 B) rejected
            (after / "references" / "adapter.md").write_text("x" * 9000 + "\n", encoding="utf-8")
            r_big = _run_helper(
                "edit-gate",
                "--before-skill-dir", str(before),
                "--after-skill-dir", str(after),
                "--rationale", "General rule.",
            )
            self.assertEqual(1, r_big.returncode)
            self.assertTrue(any("auxiliary markdown" in v for v in json.loads(r_big.stdout)["violations"]))

    def test_blinded_subdir_isolation_compact_judge_packets_and_skill_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            brief = tmp_path / "brief.md"
            brief.write_text("Evaluate skill clarity.\n", encoding="utf-8")
            work = tmp_path / "spar"
            r_init = _run_helper(
                "init",
                "--work-dir", str(work),
                "--stage", "skill",
                "--skill-dir", str(PLUGIN_ROOT / "skills" / "robust-over-simple"),
                "--brief", str(brief),
                "--harness", "antigravity",
                "--round-id", "skill-r1",
            )
            self.assertEqual(0, r_init.returncode, r_init.stderr)
            round_dir = work / "skill-r1"
            ctrl = round_dir / "control.md"
            treat = round_dir / "treatment.md"
            ctrl.write_text("# Control\n" + ("word " * 4000) + "\n", encoding="utf-8")
            treat.write_text("# Treatment\n" + ("word " * 4000) + "\n", encoding="utf-8")

            r_anon = _run_helper(
                "anonymize",
                "--round-dir", str(round_dir),
                "--control-file", str(ctrl),
                "--treatment-file", str(treat),
                "--seed", "3",
            )
            self.assertEqual(0, r_anon.returncode, r_anon.stderr)
            self.assertTrue((round_dir / "blinded" / "candidate-X.md").is_file())
            self.assertTrue((round_dir / "blinded" / "candidate-Y.md").is_file())
            self.assertFalse((round_dir / "blinded" / "sealed-key.json").exists())

            r_jp = _run_helper("judge-packet", "--round-dir", str(round_dir))
            self.assertEqual(0, r_jp.returncode, r_jp.stderr)
            jp_text = (round_dir / "judge-packets.json").read_text(encoding="utf-8")
            # Compact path-referenced packets stay small even for 20 KB+ candidate artifacts
            self.assertLess(len(jp_text), 12000)
            self.assertIn("blinded", jp_text)


if __name__ == "__main__":
    unittest.main()
