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
        # The codex row must not enter the after-side population.
        self.assertNotIn("cx-1", text)

if __name__ == "__main__":
    unittest.main()
