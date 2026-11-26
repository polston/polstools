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


if __name__ == "__main__":
    unittest.main()
