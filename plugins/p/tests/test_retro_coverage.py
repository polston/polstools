"""Window arguments and harness-coverage statements in retro's reports."""

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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


def agy_row(date, **over):
    row = base_row(harness="antigravity", population="unknown",
                   population_source="not_observable", date=date,
                   transcript="c%s/.system_generated/logs/transcript.jsonl" % date,
                   turns=4, user_prompts=2)
    row.update(over)
    return row


def not_covered(text, harness):
    """The count on a report's `not covered: <harness> <n>` line, or None."""
    match = re.search(r"^not covered: %s (\d+)" % harness, text, re.M)
    return int(match.group(1)) if match else None


def not_inspected(text):
    match = re.search(r"^not inspected: (.+?)(?: -|$)", text, re.M)
    return set(match.group(1).split(", ")) if match else set()


class AntigravityCoverage(RetroRun):
    def days_ago(self, n):
        return (datetime.now(timezone.utc).date() - timedelta(days=n)).isoformat()

    def test_effect_refuses_antigravity_without_printing_zero_cohorts(self):
        self.write_ledger([agy_row(self.days_ago(20 + i)) for i in range(15)]
                          + [agy_row(self.days_ago(i)) for i in range(15)])
        code, out = self.run_retro("effect", "--since", self.days_ago(10),
                                   "--harness", "antigravity")
        self.assertEqual(2, code)
        self.assertEqual("", out)

    def test_subagents_names_the_antigravity_rows_it_cannot_classify(self):
        self.write_ledger([agy_row(self.days_ago(1)), agy_row(self.days_ago(2)),
                           base_row(population="subagent",
                                    transcript="p/s/subagents/agent-0.jsonl")])
        _, out = self.run_retro("subagents", "--days", "30")
        self.assertEqual(2, not_covered(out, "antigravity"))

    def test_subagents_says_so_even_when_no_subagent_row_exists(self):
        self.write_ledger([agy_row(self.days_ago(1))])
        _, out = self.run_retro("subagents", "--days", "30")
        self.assertEqual(1, not_covered(out, "antigravity"))

    def test_subagents_prints_no_coverage_line_without_antigravity_rows(self):
        self.write_ledger([base_row(population="subagent",
                                    transcript="p/s/subagents/agent-0.jsonl")])
        _, out = self.run_retro("subagents", "--days", "30")
        self.assertIsNone(not_covered(out, "antigravity"))


class RuleSourceScope(RetroRun):
    def run_rules(self, *homes):
        env = {"CODEX_HOME": str(Path(self.tmp.name) / "cx"),
               "RETRO_ANTIGRAVITY_HOME": str(Path(self.tmp.name) / "agy")}
        for name in homes:
            Path(env[name]).mkdir()
        with mock.patch.dict(os.environ, env):
            retro = load_retro()
            retro.RULE_SOURCES = ()
            retro.CLAUDE_DIR = Path(self.tmp.name) / "no-config"
            out = io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                retro.cmd_rules(mock.Mock())
                rules = out.getvalue()
                out.seek(0)
                out.truncate()
                retro.print_candidates()
                dates = out.getvalue()
        return rules, dates

    def test_harnesses_present_on_the_machine_are_named_as_not_inspected(self):
        rules, dates = self.run_rules("CODEX_HOME", "RETRO_ANTIGRAVITY_HOME")
        self.assertEqual({"codex", "antigravity"}, not_inspected(rules))
        self.assertEqual({"codex", "antigravity"}, not_inspected(dates))

    def test_absent_harnesses_are_not_listed(self):
        rules, dates = self.run_rules("CODEX_HOME")
        self.assertEqual({"codex"}, not_inspected(rules))
        self.assertEqual({"codex"}, not_inspected(dates))


if __name__ == "__main__":
    unittest.main()
