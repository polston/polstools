"""P5 blocks cross-source usage claims without a closed pairing contract."""

import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.usage_comparability import (  # noqa: E402
    load_usage_accounting_profile, validate_usage_comparison,
)


def row(source, case, **overrides):
    values = {
        "source": source,
        "task_family": "repository-test",
        "difficulty": "medium",
        "cache_treatment": "cold",
        "accounting_version": "%s-v1" % source,
        "accounting_profile_version": 1,
        "paired_case_id": case,
        "input_tokens": 100,
        "cached_input_tokens": 0,
        "output_tokens": 10,
    }
    values.update(overrides)
    return values


class UsageComparabilityTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_usage_accounting_profile()

    def test_complete_paired_contract_passes_without_running_experiment(self):
        report = validate_usage_comparison([
            row("claude", "p1"), row("codex", "p1"),
            row("claude", "p2"), row("codex", "p2"),
        ], self.profile)
        self.assertTrue(report["comparison_allowed"])
        self.assertEqual(2, report["paired_cases"])
        self.assertEqual("not_executed", report["paired_experiment"])

    def test_each_required_comparability_field_is_enforced(self):
        required = (
            "task_family", "difficulty", "cache_treatment",
            "accounting_version", "paired_case_id",
        )
        for field in required:
            rows = [row("claude", "p1"), row("codex", "p1")]
            rows[0][field] = ""
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, field):
                    validate_usage_comparison(rows, self.profile)

    def test_mixed_versions_or_pair_metadata_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "accounting version"):
            validate_usage_comparison([
                row("claude", "p1", accounting_version="claude-v0"),
                row("codex", "p1")], self.profile)
        with self.assertRaisesRegex(ValueError, "pair metadata"):
            validate_usage_comparison([
                row("claude", "p1"),
                row("codex", "p1", difficulty="hard")], self.profile)
        with self.assertRaisesRegex(ValueError, "paired source coverage"):
            validate_usage_comparison([row("claude", "p1")], self.profile)
        with self.assertRaisesRegex(ValueError, "exactly one row per source"):
            validate_usage_comparison([
                row("claude", "p1"), row("claude", "p1"),
                row("codex", "p1")], self.profile)


if __name__ == "__main__":
    unittest.main()
