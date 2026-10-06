"""Window arguments and harness-coverage statements in retro's reports."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_retro_extract import load_retro
from test_retro_reporting import base_row


class RetroRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.claude_home = Path(self.tmp.name) / "cc"

    def write_ledger(self, rows):
        with open(self.work / "metrics.jsonl", "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def run_retro(self, *argv):
        env = {"RETRO_HOME": str(self.work),
               "CLAUDE_CONFIG_DIR": str(self.claude_home),
               "CODEX_HOME": str(Path(self.tmp.name) / "absent-cx"),
               "RETRO_ANTIGRAVITY_HOME": str(Path(self.tmp.name) / "absent-agy"),
               "CLAUDE_CODE_SESSION_ID": ""}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), \
                mock.patch("sys.argv", ["retro", *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            retro = load_retro()
            # CLAUDE_DIR ignores CLAUDE_CONFIG_DIR; keep it inside the temp dir.
            retro.CLAUDE_DIR = self.claude_home
            try:
                retro.main()
                code = 0
            except SystemExit as stop:
                code = stop.code
        return code, out.getvalue()


class DaysValidation(RetroRun):
    def setUp(self):
        super().setUp()
        self.write_ledger([base_row(turns=3, user_prompts=1)])

    def packs(self):
        return sorted(p.name for p in self.work.glob("pack-*.md"))

    def test_pack_rejects_a_window_of_zero_or_fewer_days(self):
        for days in ("0", "-3"):
            code, _ = self.run_retro("pack", "--days", days)
            self.assertEqual(2, code, days)
        self.assertEqual([], self.packs())

    def test_pack_accepts_a_positive_window(self):
        code, _ = self.run_retro("pack", "--days", "1")
        self.assertIn(code, (0, 1))
        self.assertEqual(1, len(self.packs()))

    def test_negative_windows_are_rejected_where_zero_means_all_history(self):
        for command in ("skills", "subagents", "effect"):
            code, _ = self.run_retro(command, "--days", "-1")
            self.assertEqual(2, code, command)

    def test_zero_still_means_all_history_for_skills(self):
        code, _ = self.run_retro("skills", "--days", "0")
        self.assertIn(code, (0, 1))


if __name__ == "__main__":
    unittest.main()
