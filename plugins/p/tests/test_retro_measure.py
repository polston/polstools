"""Characterisation of measure() over a synthetic Claude transcript.

Pins the counters, identity fields, and ending that the Codex ingestion
change must not move. Written BEFORE that change, deliberately."""

import importlib.util
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fixtures import build_corpus, claude_assistant, claude_user

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def load_retro():
    spec = importlib.util.spec_from_file_location(
        "retro_measure_under_test", PLUGIN_ROOT / "bin" / "retro.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_session(root):
    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    rows = [
        claude_user("please do the thing", t0),
        claude_assistant("x" * 400, t0 + timedelta(minutes=1),
                         tools=[("Read", {"file_path": "/tmp/a"})]),
        # Short reply after a long turn, corrective wording: a correction.
        claude_user("no, wrong file", t0 + timedelta(minutes=2)),
        # Long enough to clear CORRECTION_MIN_PRIOR_CHARS (200) — a short
        # turn before "sure" classifies as nothing, not as an approval.
        claude_assistant("z" * 400, t0 + timedelta(minutes=3)),
        # Whole-reply affirmative after a long turn: an approval.
        claude_user("sure", t0 + timedelta(minutes=4)),
        claude_assistant("finished", t0 + timedelta(minutes=5)),
    ]
    build_corpus(root, [{"project": "projA", "session": "s1", "rows": rows}])
    return root / "projA" / "s1.jsonl"


class MeasureCharacterisation(unittest.TestCase):
    def setUp(self):
        self.retro = load_retro()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = write_session(Path(self.tmp.name))

    def measure_row(self):
        return self.retro.measure(self.path)

    def test_counters_pin(self):
        row = self.measure_row()
        self.assertEqual(3, row["turns"])
        self.assertEqual(3, row["user_prompts"])
        self.assertEqual(1, row["tool_calls"])
        self.assertEqual(1, row["correction_candidates"])
        self.assertEqual(1, row["approval_turns"])
        self.assertEqual(0, row["repeat_calls"])
        self.assertEqual(0, row["interrupts"])
        self.assertEqual("text", row["ending"])

    def test_identity_pin(self):
        row = self.measure_row()
        self.assertEqual("sess-claude-1", row["session_id"])
        self.assertEqual("main", row["git_branch"])
        self.assertEqual("2026-08-01", row["date"])
        self.assertEqual(30, row["tokens_in"])
        self.assertEqual(60, row["tokens_out"])
        self.assertEqual(15, row["cache_read"])

    def test_subagent_layout_is_recognised(self):
        sub_rows = [claude_user("go", datetime(2026, 8, 1, tzinfo=timezone.utc)),
                    claude_assistant("ok", datetime(2026, 8, 1, tzinfo=timezone.utc))]
        build_corpus(Path(self.tmp.name), [{
            "project": "projA", "session": "s1",
            "subagent": True, "rows": sub_rows}])
        sub_path = (Path(self.tmp.name) / "projA" / "s1" / "subagents"
                    / "agent-0.jsonl")
        row = self.retro.measure(sub_path)
        # The temp-root fallback makes `rel` the bare filename, so the
        # path-prefix rule cannot see `subagents/`. Task 3 replaces this
        # with a root-relative test through measure(path, harness, root).
        self.assertFalse(row["is_subagent"])


class CurrentShapes(unittest.TestCase):
    """Pin today's totals/split_population shapes. Task 3 REWRITES these
    tests against the new contracts; until then they are the regression
    net the spec's Verification 1 requires."""

    def setUp(self):
        self.retro = load_retro()

    def test_totals_returns_one_counter_today(self):
        agg = self.retro.totals([{"turns": 5, "tokens_out": 7},
                                 {"turns": 2, "tokens_out": 1}])
        self.assertEqual(7, agg["turns"])
        self.assertEqual(8, agg["tokens_out"])

    def test_split_population_returns_main_sub_tuple_today(self):
        main, sub = self.retro.split_population(
            [{"is_subagent": False}, {"is_subagent": True}])
        self.assertEqual(1, len(main))
        self.assertEqual(1, len(sub))


if __name__ == "__main__":
    unittest.main()
