"""P5 blocks cross-source usage claims without a closed pairing contract."""

import sys
import json
import tempfile
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
        "accounting_version": "claude-v2" if source == "claude" else "%s-v1" % source,
        "accounting_profile_version": 3,
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
        ], self.profile, dimensions=("output_tokens",))
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

    def test_counts_require_observed_nonnegative_integers(self):
        invalid = (None, True, False, -1, -0.5, 0.5, 1.0, '0', '', [], {},
                   float('nan'), float('inf'))
        for field in ('input_tokens', 'cached_input_tokens', 'output_tokens'):
            for value in invalid:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        validate_usage_comparison([
                            row('claude', 'p1', **{field: value}), row('codex', 'p1')])
            first = row('claude', 'p1')
            del first[field]
            with self.subTest(field=field, missing=True), self.assertRaises(ValueError):
                validate_usage_comparison([first, row('codex', 'p1')])

    def test_observed_zero_counts_remain_eligible(self):
        zero = dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
        report = validate_usage_comparison([row('claude', 'p1', **zero), row('codex', 'p1', **zero)],
                                           dimensions=('output_tokens',))
        self.assertTrue(report['comparison_allowed'])
        self.assertEqual(report['paired_experiment'], 'not_executed')

    def test_profile_version_must_be_an_integer_without_coercion(self):
        for value in (3.0, 3.9, '3', True, None, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_usage_comparison([
                    row('claude', 'p1', accounting_profile_version=value), row('codex', 'p1')])

    def test_pair_metadata_must_be_nonempty_text(self):
        for field in ('task_family', 'difficulty', 'cache_treatment', 'paired_case_id'):
            for value in (False, 0, 1.5, [], {}, '   '):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_usage_comparison([
                        row('claude', 'p1', **{field: value}),
                        row('codex', 'p1', **{field: value})])

    def test_profile_file_rejects_coerced_versions_and_missing_source_contracts(self):
        source = PLUGIN_ROOT / 'profiles' / 'usage-accounting.json'
        original = json.loads(source.read_text())
        for field, value in (('schema_version', True), ('schema_version', 1.9),
                             ('profile_version', 2.9), ('profile_version', '2'),
                             ('sources', []), ('sources', {'first': {}, 'second': {}})):
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'profile.json'
                path.write_text(json.dumps({**original, field: value}))
                with self.assertRaises(ValueError):
                    load_usage_accounting_profile(path)

    def test_raw_input_dimensions_with_different_meanings_are_not_authorized(self):
        rows = [row('claude', 'p1', input_tokens=100, cached_input_tokens=900),
                row('codex', 'p1', input_tokens=1000, cached_input_tokens=900)]
        with self.assertRaisesRegex(ValueError, 'dimension'):
            validate_usage_comparison(rows)
        report = validate_usage_comparison(rows, dimensions=('cached_input_tokens',))
        self.assertEqual(report['comparison_dimensions'], ['cached_input_tokens'])
        self.assertTrue(report['comparison_allowed'])

    def test_codex_cache_is_a_subset_but_claude_cache_is_separate(self):
        with self.assertRaisesRegex(ValueError, 'subset'):
            validate_usage_comparison([
                row('claude', 'p1'),
                row('codex', 'p1', input_tokens=0, cached_input_tokens=1)],
                dimensions=('output_tokens',))
        report = validate_usage_comparison([
            row('claude', 'p1', input_tokens=0, cached_input_tokens=900),
            row('codex', 'p1', input_tokens=900, cached_input_tokens=900)],
            dimensions=('output_tokens',))
        self.assertEqual(report['comparison_dimensions'], ['output_tokens'])

    def test_dimension_request_requires_known_unique_nonempty_fields(self):
        for dimensions in ((), 'output_tokens', ('unknown',), ('output_tokens',)*2,
                           (None,), ([],)):
            with self.subTest(dimensions=dimensions), self.assertRaises(ValueError):
                validate_usage_comparison([row('claude', 'p1'), row('codex', 'p1')],
                                          dimensions=dimensions)

    def test_historical_profile_without_dimension_authority_fails_closed(self):
        original = json.loads((PLUGIN_ROOT / 'profiles' / 'usage-accounting.json').read_text())
        original.pop('comparable_dimensions')
        original['profile_version'] = 2
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profile.json'
            path.write_text(json.dumps(original))
            historical = load_usage_accounting_profile(path)
        with self.assertRaisesRegex(ValueError, 'dimension'):
            validate_usage_comparison([
                row('claude', 'p1', accounting_profile_version=2),
                row('codex', 'p1', accounting_profile_version=2)],
                historical, dimensions=('output_tokens',))

    def test_mixed_versions_or_pair_metadata_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "accounting version"):
            validate_usage_comparison([
                row("claude", "p1", accounting_version="claude-v1"),
                row("codex", "p1")], self.profile)
        with self.assertRaisesRegex(ValueError, "profile version"):
            validate_usage_comparison([
                row("claude", "p1", accounting_profile_version=1),
                row("codex", "p1")], self.profile)
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
