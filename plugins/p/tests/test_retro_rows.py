"""Schema 7 row fields and the population/eligibility machinery."""

import unittest

from test_retro_extract import load_retro


class Totals(unittest.TestCase):
    def test_totals_returns_sums_and_eligible_row_counts(self):
        retro = load_retro()
        rows = [
            {"tool_errors": 2, "turns": 5, "ineligible": []},
            {"tool_errors": 0, "turns": 7,
             "ineligible": ["tool_errors", "queued_prompts",
                            "permission_mode_changes"]},
        ]
        sums, eligible = retro.totals(rows)
        self.assertEqual(2, sums["tool_errors"])
        self.assertEqual(12, sums["turns"])
        self.assertEqual(1, eligible["tool_errors"])
        self.assertEqual(2, eligible["turns"])


class SplitPopulation(unittest.TestCase):
    def test_dict_keyed_by_population(self):
        retro = load_retro()
        # A row with no population key (pre-rename writer; cannot occur
        # after a rebuild, but the reader must not crash) counts as main.
        rows = [{"population": "main"}, {"population": "subagent"},
                {"population": "automation"}, {}]
        split = retro.split_population(rows)
        self.assertEqual(2, len(split["main"]))
        self.assertEqual(1, len(split["subagent"]))
        self.assertEqual(1, len(split["automation"]))
        self.assertEqual(0, len(split["unknown"]))


class ProjectKey(unittest.TestCase):
    def test_stored_column_wins_and_old_rule_is_fallback(self):
        retro = load_retro()
        self.assertEqual("cx-ab12cd34",
                         retro.project_key({"project_key": "cx-ab12cd34",
                                            "transcript": "2026/08/x.jsonl"}))
        self.assertEqual("projA",
                         retro.project_key({"transcript": "projA/s1.jsonl"}))


class SelfExclusion(unittest.TestCase):
    def test_codex_session_env_vars_are_read(self):
        import os
        from unittest import mock
        retro = load_retro()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "tid-1",
                                          "CODEX_SESSION_ID": "sid-1",
                                          "CLAUDE_CODE_SESSION_ID": "cid-1"}):
            ids = retro.reporting_session_ids(["extra-1"])
        self.assertEqual({"extra-1", "tid-1", "sid-1", "cid-1"}, ids)


if __name__ == "__main__":
    unittest.main()
