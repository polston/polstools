"""Counter definitions in the Claude reducer and the extract summary line."""

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fixtures import build_corpus, claude_assistant, claude_user

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def load_retro():
    spec = importlib.util.spec_from_file_location(
        "retro_counters_under_test", PLUGIN_ROOT / "bin" / "retro.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def skilled(text, when, skill):
    record = claude_assistant(text, when, tools=[("Read", {"file_path": "a"})])
    if skill:
        record["attributionSkill"] = skill
    return record


def tool_result(when):
    return {"type": "user", "timestamp": when.isoformat().replace("+00:00", "Z"),
            "sessionId": "sess-claude-1", "toolUseResult": {"ok": True},
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu-0", "content": "ok"}]}}


class SkillRuns(unittest.TestCase):
    def setUp(self):
        self.retro = load_retro()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def measure(self, rows):
        build_corpus(self.root, [{"project": "p", "session": "s", "rows": rows}])
        return self.retro.measure(self.root / "p" / "s.jsonl", "claude", self.root)

    def at(self, seconds):
        return T0 + timedelta(seconds=seconds)

    def test_tool_results_and_prompts_inside_a_run_do_not_split_it(self):
        row = self.measure([
            claude_user("start", self.at(0)),
            skilled("one", self.at(1), "p:doctor"),
            tool_result(self.at(2)),
            skilled("two", self.at(3), "p:doctor"),
            claude_user("and the rest", self.at(4)),
            skilled("three", self.at(5), "p:doctor"),
        ])
        self.assertEqual(1, row["skill_runs"])

    def test_an_unattributed_assistant_turn_ends_the_run(self):
        row = self.measure([
            claude_user("start", self.at(0)),
            skilled("one", self.at(1), "p:doctor"),
            tool_result(self.at(2)),
            skilled("plain", self.at(3), None),
            skilled("again", self.at(4), "p:doctor"),
        ])
        self.assertEqual(2, row["skill_runs"])

    def test_a_different_skill_starts_a_new_run(self):
        row = self.measure([
            claude_user("start", self.at(0)),
            skilled("one", self.at(1), "p:doctor"),
            tool_result(self.at(2)),
            skilled("two", self.at(3), "p:update"),
        ])
        self.assertEqual(2, row["skill_runs"])
        self.assertEqual(["p:doctor", "p:update"], row["skills_used"])

    def test_a_ledger_holding_the_old_definition_is_refused(self):
        """Rows written under schema 7 counted skill_runs the old way. A
        reader must refuse them rather than sum two definitions."""
        work = self.root / "work"
        work.mkdir()
        (work / "metrics.jsonl").write_text(
            json.dumps({"transcript": "p/s.jsonl", "schema": 7,
                        "skill_runs": 48}) + "\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"RETRO_HOME": str(work)}):
            retro = load_retro()
        with self.assertRaises(SystemExit) as stop, \
                redirect_stdout(io.StringIO()), \
                mock.patch("sys.stderr", io.StringIO()):
            retro.load_rows()
        self.assertEqual(retro.EXIT_CANNOT_RUN, stop.exception.code)


if __name__ == "__main__":
    unittest.main()
