import contextlib
import importlib.util
import io
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("retro_legacy", PLUGIN_ROOT / "bin" / "retro.py")
retro = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retro)


class LegacyLabelReportTests(unittest.TestCase):
    def test_candidate_only_turn_labels_do_not_rank_sessions(self):
        candidate_only = {
            "correction_candidates": 3, "interrupts": 2,
            "permission_mode_changes": 0, "tool_errors": 0,
        }
        self.assertFalse(retro.legacy_turn_labels_allow("decision_support"))
        self.assertEqual(0, retro.friction_score(candidate_only))
        operational = dict(candidate_only, permission_mode_changes=1, tool_errors=2)
        self.assertEqual(5, retro.friction_score(operational))

    def test_settled_report_recomputes_current_rule_instead_of_stale_prediction(self):
        sample = {
            "kind": "turn", "label": "correction", "predicted": "none",
            "reply_chars": 20, "prior_chars": 500,
            "said": "no, use the previous value", "stratum_population": 1,
            "stratum_sampled": 1,
        }
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            retro.report_labels([sample])
        correction_row = next(line for line in output.getvalue().splitlines()
                              if line.startswith("| correction |"))
        self.assertEqual("| correction | 1.00 | 1.00 | 1 |", correction_row)


if __name__ == "__main__":
    unittest.main()
