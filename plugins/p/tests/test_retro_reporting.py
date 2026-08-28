"""Per-harness output of pack, skills, and subagents."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from test_retro_extract import load_retro


def base_row(**over):
    today = datetime.now(timezone.utc).date().isoformat()
    row = {"transcript": "p/s.jsonl", "harness": "claude",
           "population": "main", "parent_session_id": "",
           "project_key": "p", "ineligible": [], "compacted": False,
           "session_id": "s", "project": "~", "git_branch": "", "cc_version": "",
           "date": today, "duration_s": 60, "tokens_in": 0, "tokens_out": 10,
           "cache_read": 0, "skills_used": [], "schema": 7, "ending": "text",
           "eligible": []}
    for key in ["turns", "user_prompts", "tool_calls", "tool_errors",
                "repeat_calls", "correction_candidates", "approval_turns",
                "interrupts", "permission_mode_changes", "queued_prompts",
                "skill_runs"]:
        row.setdefault(key, 0)
    for key in ["schema_rejected", "unread_before_write",
                "missing_path_target", "search_pattern_rejected",
                "invalid_tool_input", "workspace_target_outside",
                "workspace_shape_unverifiable"]:
        row[key] = 0
    row.update(over)
    return row


class Reporting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / "work"
        self.work.mkdir(parents=True)

    def load_with_ledger(self, rows):
        with open(self.work / "metrics.jsonl", "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        patcher = mock.patch.dict(os.environ, {"RETRO_HOME": str(self.work)})
        patcher.start()
        self.addCleanup(patcher.stop)
        return load_retro()

    def run_cmd(self, retro, func, **args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            func(mock.Mock(**args))
        return out.getvalue()

    def test_pack_prints_per_harness_blocks_and_unranked_note(self):
        retro = self.load_with_ledger([
            base_row(tool_errors=2),
            base_row(transcript="2026/08/x.jsonl", harness="codex",
                     project_key="cx-1", turns=3,
                     ineligible=["tool_errors", "queued_prompts",
                                 "permission_mode_changes"]),
        ])
        self.run_cmd(retro, retro.cmd_pack, days=7, sessions=8)
        pack = next(self.work.glob("pack-*.md")).read_text(encoding="utf-8")
        self.assertIn("### claude", pack)
        self.assertIn("### codex", pack)
        self.assertIn("not friction-ranked", pack)

    def test_pack_ranked_loop_keeps_heading_with_no_quotable_moments(self):
        # Regression pin: the ranked-loop guard skips a session only when its
        # transcript file is missing, never because moments() came back
        # empty. A readable transcript with nothing quotable (here: only an
        # assistant record, no user turn) must still get its "### <date>"
        # heading and counter line.
        claude_home = Path(self.tmp.name) / "cc"
        transcript = claude_home / "projects" / "p" / "s.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps({"type": "assistant",
                        "message": {"content": "no user turn follows"}}) + "\n",
            encoding="utf-8")
        patcher = mock.patch.dict(os.environ,
                                  {"CLAUDE_CONFIG_DIR": str(claude_home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        retro = self.load_with_ledger([base_row(tool_errors=1)])
        self.run_cmd(retro, retro.cmd_pack, days=7, sessions=8)
        pack = next(self.work.glob("pack-*.md")).read_text(encoding="utf-8")
        today = datetime.now(timezone.utc).date().isoformat()
        self.assertIn(f"### {today} ·", pack)
        self.assertIn("correction candidates", pack)

    def test_skills_prints_per_harness_columns(self):
        retro = self.load_with_ledger([
            base_row(skills_used=["p:doctor"], skill_runs=1),
            base_row(transcript="2026/08/x.jsonl", harness="codex",
                     skills_used=["doctor"], skill_runs=1),
        ])
        text = self.run_cmd(retro, retro.cmd_skills, days=0)
        self.assertIn("| doctor | 1 | 1 |", text)
        self.assertIn("## Never fired — claude", text)
        self.assertIn("## Never fired — codex", text)

    def test_subagents_excludes_codex_from_failure_table_and_guards_empty(self):
        retro = self.load_with_ledger([
            base_row(population="subagent", eligible=["invalid_tool_input"]),
            base_row(transcript="2026/08/y.jsonl", harness="codex",
                     population="subagent", parent_session_id="pp",
                     ineligible=["tool_errors", "queued_prompts",
                                 "permission_mode_changes"]),
        ])
        text = self.run_cmd(retro, retro.cmd_subagents, days=0,
                            exclude_session=[])
        self.assertIn("codex subagent rows are excluded", text)
        self.assertIn("## How codex runs answered", text)
        # Excluding the parent id drops the codex row entirely (D4.6):
        # its per-harness sections must vanish, not just shrink.
        text = self.run_cmd(retro, retro.cmd_subagents, days=0,
                            exclude_session=["pp"])
        self.assertNotIn("## How codex runs answered", text)
        self.assertNotIn("codex subagent rows are excluded", text)

    def test_effect_restricts_harness_and_renders(self):
        # cmd_effect is otherwise driven by no test anywhere, while two
        # tasks change its call contracts (split dict, totals pair,
        # per-counter denominators, --harness).
        retro = self.load_with_ledger([
            base_row(date="2026-01-05", tool_errors=1, turns=10),
            base_row(transcript="p/s2.jsonl", session_id="s2",
                     date="2026-03-05", tool_errors=3, turns=10),
            base_row(transcript="2026/08/x.jsonl", harness="codex",
                     session_id="cx", project_key="cx-1",
                     date="2026-03-05", turns=5,
                     ineligible=["tool_errors", "queued_prompts",
                                 "permission_mode_changes"]),
        ])
        text = self.run_cmd(retro, retro.cmd_effect, since="2026-02-01",
                            days=0, harness="claude")
        self.assertIn("harness=claude", text)
        self.assertIn("tokens_out", text)   # M4 guard: the row must render
        # The codex row must not enter the after-side population: only s2
        # qualifies, so the After side must show exactly 1 session, not 2.
        self.assertIn("After:  1 sessions", text)

    def test_effect_harness_all_omits_turn_normalised_rows(self):
        retro = self.load_with_ledger([
            base_row(date="2026-01-05", tool_errors=1, turns=10),
            base_row(transcript="p/s2.jsonl", session_id="s2",
                     date="2026-03-05", tool_errors=3, turns=10),
            base_row(transcript="2026/08/x.jsonl", harness="codex",
                     session_id="cx", project_key="cx-1",
                     date="2026-03-05", tool_errors=2, turns=5,
                     ineligible=["queued_prompts",
                                 "permission_mode_changes"]),
        ])
        text = self.run_cmd(retro, retro.cmd_effect, since="2026-02-01",
                            days=0, harness="all")
        self.assertIn("harness=all", text)
        self.assertIn("turn-normalised rows omitted for mixed harnesses", text)
        self.assertNotIn("turns per session", text)
        self.assertNotIn("| tokens_out |", text)
        header = next(line for line in text.splitlines()
                      if line.startswith("| signal |"))
        delim = next(line for line in text.splitlines()
                     if line.startswith("|---"))
        self.assertEqual(header.count("|"), delim.count("|"))

    def test_pack_quotes_approvals_and_codex_moments(self):
        codex_home = Path(self.tmp.name) / "cx"
        import fixtures as fx
        from test_retro_codex import T
        from test_retro_extract import write_rollout
        rel = "2026/08/01/rollout-a.jsonl"
        write_rollout(codex_home / "sessions", rel, [
            fx.rollout_meta(T), fx.rollout_user("do the thing", T),
            fx.rollout_assistant("x" * 400, T),
            fx.rollout_user("no, wrong file", T),
            fx.rollout_assistant("y" * 400, T),
            fx.rollout_user("perfect", T),
            fx.rollout_assistant("done", T),
        ])
        retro = self.load_with_ledger([
            base_row(transcript=rel, harness="codex", project_key="cx-1",
                     date="2026-08-01", correction_candidates=1,
                     approval_turns=1, user_prompts=3,
                     ineligible=["tool_errors", "queued_prompts",
                                 "permission_mode_changes"]),
        ])
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            self.run_cmd(retro, retro.cmd_pack, days=3650, sessions=8)
        pack = max(self.work.glob("pack-*.md")).read_text(encoding="utf-8")
        self.assertIn("candidate-sampled", pack)
        self.assertIn("**approval**", pack)
        self.assertIn("**correction**", pack)


if __name__ == "__main__":
    unittest.main()
