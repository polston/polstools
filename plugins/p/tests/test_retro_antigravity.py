"""Synthetic Antigravity exports: discovery, step metrics and report evidence."""

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


class AntigravityHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        env = {"RETRO_HOME": str(self.base / "evidence"),
               "RETRO_ANTIGRAVITY_HOME": str(self.base / "agy"),
               "CLAUDE_CONFIG_DIR": str(self.base / "absent-claude"),
               "CODEX_HOME": str(self.base / "absent-codex")}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        self.addCleanup(patch.stop)
        self.retro = load_retro()
        self.root = self.retro.antigravity_sessions_dir()
        self.logs = self.root / "example-session" / ".system_generated" / "logs"
        self.logs.mkdir(parents=True)
        self.now = datetime.now(timezone.utc).isoformat()

    def record(self, index, kind, content="", **fields):
        rec = {"step_index": index, "type": kind, "content": content,
               "source": "USER_EXPLICIT" if kind == "USER_INPUT" else "MODEL",
               "status": "DONE", "created_at": self.now}
        rec.update(fields)
        return rec

    def write(self, records, name="transcript.jsonl"):
        path = self.logs / name
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return path

    def extract(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, self.retro.cmd_extract(mock.Mock(rebuild=False)))
        return self.retro.load_rows()

    def test_structural_detection_and_supported_metrics(self):
        call = {"name": "read_file", "args": {"path": "example.txt"}}
        path = self.write([
            self.record(0, "USER_INPUT", "Please inspect the example."),
            self.record(1, "PLANNER_RESPONSE", "A detailed explanation. " * 20,
                        tool_calls=[call]),
            self.record(2, "GENERIC", "Tool output is not assistant prose."),
            self.record(3, "USER_INPUT", "No, inspect the other example."),
            self.record(4, "PLANNER_RESPONSE", "The example is fixed. " * 20, tool_calls=[call]),
            self.record(5, "USER_INPUT", "looks good"),
        ])
        outcome, row = self.retro.measure_outcome(path, "antigravity", self.root)
        self.assertEqual(self.retro.MEASURED, outcome)
        self.assertEqual("antigravity", row["harness"])
        self.assertEqual("unknown", row["population"])
        self.assertEqual("not_observable", row["population_source"])
        self.assertEqual(3, row["user_prompts"])
        self.assertEqual(2, row["turns"])
        self.assertEqual(2, row["tool_calls"])
        self.assertEqual(1, row["repeat_calls"])
        self.assertEqual(1, row["correction_candidates"])
        self.assertEqual(1, row["approval_turns"])
        self.assertEqual(0, self.retro.friction_score(row))
        for metric in ("tokens_out", "tool_errors", "interrupts", "skill_runs"):
            self.assertIn(metric, row["ineligible"])
        _, eligible = self.retro.totals([row])
        self.assertEqual(0, eligible["tokens_out"])
        self.assertEqual(1, eligible["turns"])

    def test_full_export_wins_and_incremental_upgrade_replaces_short_row(self):
        short = [self.record(0, "USER_INPUT", "inspect")]
        self.write(short)
        first = self.extract()
        self.assertEqual(1, len(first))
        full = short + [self.record(1, "PLANNER_RESPONSE", "Complete response.")]
        self.write(full, "transcript_full.jsonl")
        (self.root / "history.jsonl").write_text(json.dumps(short[0]) + "\n")
        rows = self.extract()
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0]["transcript"].endswith("transcript_full.jsonl"))
        self.assertEqual(1, rows[0]["turns"])
        self.assertEqual(rows, self.extract())

    def test_snapshots_count_once_and_malformed_partial_lines_do_not_crash(self):
        path = self.write([
            self.record(0, "USER_INPUT", "inspect"),
            self.record(1, "PLANNER_RESPONSE", "partial", status="RUNNING"),
            self.record(1, "PLANNER_RESPONSE", "Final response."),
            None, [], {"step_index": "not-an-index"},
        ])
        with path.open("a") as f:
            f.write('{"step_index":')
        row = self.retro.measure_antigravity(path, self.root)
        self.assertEqual(1, row["turns"])
        self.assertEqual("text", row["ending"])

    def test_system_messages_and_tool_results_do_not_become_user_prompts(self):
        path = self.write([
            self.record(0, "USER_INPUT", "injected", source="SYSTEM"),
            self.record(1, "SYSTEM_MESSAGE", "setup", source="SYSTEM"),
            self.record(2, "GENERIC", "tool result"),
        ])
        self.assertIsNone(self.retro.measure_antigravity(path, self.root))

    def test_pack_includes_unclassified_antigravity_moments_with_context(self):
        before = "The assistant proposes an unnecessarily broad change. " * 8
        self.write([self.record(0, "USER_INPUT", "inspect the example"),
                    self.record(1, "PLANNER_RESPONSE", before),
                    self.record(2, "USER_INPUT", "No, fix only this example.")])
        rows = self.extract()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, self.retro.cmd_pack(mock.Mock(days=7, sessions=8)))
        pack = next(self.retro.WORK_DIR.glob("pack-*.md")).read_text(encoding="utf-8")
        self.assertIn("Antigravity moments — candidate-sampled, not ranked", pack)
        self.assertIn("population is not observable", pack)
        self.assertIn("unavailable, not measured zeros", pack)
        self.assertIn("No, fix only this example.", pack)
        self.assertIn("assistant, just before", pack)
        self.assertNotIn("_No sessions in window._", pack)
        self.assertEqual([], self.retro.split_population(rows)["main"])

    def test_leading_nonrecords_and_system_steps_do_not_hide_export(self):
        for prefix in ([None, []] * 12,
                       [self.record(i, "SYSTEM_MESSAGE", "setup", source="SYSTEM")
                        for i in range(24)]):
            with self.subTest(prefix_kind=type(prefix[0]).__name__):
                path = self.write(prefix + [self.record(24, "USER_INPUT", "inspect")])
                outcome, row = self.retro.measure_outcome(path, "antigravity", self.root)
                self.assertEqual(self.retro.MEASURED, outcome)
                self.assertEqual(1, row["user_prompts"])

    def test_malformed_snapshot_cannot_erase_valid_step(self):
        path = self.write([self.record(0, "USER_INPUT", "inspect"),
                           {"step_index": 0}, {"step_index": False},
                           self.record(-1, "USER_INPUT", "invalid index")])
        row = self.retro.measure_antigravity(path, self.root)
        self.assertIsNotNone(row)
        self.assertEqual(1, row["user_prompts"])

    def test_fallback_to_unchanged_short_export_recovers_its_row_and_moments(self):
        records = [self.record(0, "USER_INPUT", "inspect"),
                   self.record(1, "PLANNER_RESPONSE", "An explanation. " * 30),
                   self.record(2, "USER_INPUT", "No, fix the example.")]
        self.write(records)
        self.extract()
        full = self.write(records, "transcript_full.jsonl")
        self.extract()
        full.unlink()
        rows = self.extract()
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0]["transcript"].endswith("/transcript.jsonl"))
        self.assertEqual(1, len(self.retro.moments(rows[0])))

    def test_missing_file_is_unreadable(self):
        outcome, row = self.retro.measure_outcome(self.logs / "missing.jsonl",
                                                 "antigravity", self.root)
        self.assertEqual(self.retro.UNREADABLE, outcome)
        self.assertIsNone(row)

    def test_absent_roots_fail_instead_of_producing_empty_success(self):
        with mock.patch.dict(os.environ, {"RETRO_ANTIGRAVITY_HOME": str(self.base / "missing")}):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
                self.extract()
        self.assertEqual(2, stop.exception.code)

    def test_skills_does_not_claim_unobservable_attribution_is_dormant(self):
        self.write([self.record(0, "USER_INPUT", "inspect")])
        self.extract()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.retro.cmd_skills(mock.Mock(days=7))
        self.assertIn("Antigravity skill attribution and active inventory: not observable", out.getvalue())
        self.assertNotIn("No observed attribution — antigravity", out.getvalue())


if __name__ == "__main__":
    unittest.main()
