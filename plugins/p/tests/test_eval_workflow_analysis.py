import sys
import unittest
import itertools
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.errors import WorkflowError
from retro_eval.workflow.analysis import (classifier_metrics, paired_effect,
    missingness_bounds, standardized_binary, decision, confirmation_status, detector_effect)


def pair(key, a, b, group=None):
    return {'unit': key, 'split_group': group or key, 'baseline': a, 'intervention': b,
            'artifact_digest': 'same-snapshot', 'comparator_artifact_digest': 'same-snapshot',
            'scope_digest': 'same-scope', 'comparator_scope_digest': 'same-scope'}


def arm(success, unknown, pending=0):
    return [{'unit': str(i), 'status': state} for i, state in enumerate(
        ['success'] * success + ['unknown'] * unknown + ['pending'] * pending)]


class AnalysisTests(unittest.TestCase):
    def test_A01_selective_success_is_not_capture(self):
        result = classifier_metrics([{'truth': True, 'prediction': True}] +
                                    [{'truth': True, 'prediction': None}] * 9)
        self.assertEqual(result['selective_recall'], 1)
        self.assertEqual(result['all_truth_capture'], 0.1)
        self.assertEqual(result['coverage'], 0.1)
        self.assertFalse(result['whole_population_validity'])

    def test_A02_all_abstain(self):
        result = classifier_metrics([{'truth': True, 'prediction': None}] * 10)
        self.assertEqual(result['coverage'], 0)
        self.assertIsNone(result['precision'])
        self.assertIsNone(result['selective_recall'])

    def test_A03_empty_arms(self):
        result = missingness_bounds([], [])
        self.assertIsNone(result['effect_bounds'])
        self.assertIsNone(result['baseline']['observed_rate'])
        self.assertEqual(result['reason'], 'empty_population')

    def test_A04_retries_are_not_independent_units(self):
        result = paired_effect([pair(str(i), 12, 8, group='one-case') for i in range(10)])
        self.assertEqual(result['independent_units'], 1)
        self.assertEqual(result['paired_attempts'], 10)
        self.assertIsNone(result['interval'])

    def test_A05_matching_requires_artifact_and_scope(self):
        row = pair('one', 12, 8)
        row['comparator_artifact_digest'] = 'different'
        result = paired_effect([row])
        self.assertEqual(result['independent_units'], 0)
        self.assertEqual(result['excluded'][0]['reason'], 'unmatched_artifact_or_scope')

    def test_A06_favorable_mean_cannot_override_severe_regression(self):
        a, b = [12, 15, 8, 20, 10, 14, 9, 18], [8, 10, 7, 12, 9, 10, 8, 11]
        result = paired_effect([pair(str(i), x, y) for i, (x, y) in enumerate(zip(a, b))])
        self.assertEqual(result['effect'], -3.875)
        self.assertEqual(decision(analysis=result, severe_regression=True)['disposition'], 'revert')

    def test_A07_task_mix_reverses_sign(self):
        strata = [
            {'baseline_n': 20, 'baseline_success': 18, 'intervention_n': 80, 'intervention_success': 64},
            {'baseline_n': 80, 'baseline_success': 32, 'intervention_n': 20, 'intervention_success': 6}]
        result = standardized_binary(strata)
        self.assertAlmostEqual(result['raw_difference'], 0.2)
        self.assertAlmostEqual(result['standardized_difference'], -0.1)
        self.assertEqual(result['claim_type'], 'observational')

    def test_A08_H10_missing_arm_evidence_is_retained(self):
        result = missingness_bounds(arm(7, 3), arm(8, 2))
        self.assertAlmostEqual(result['effect_bounds'][0], -0.2)
        self.assertAlmostEqual(result['effect_bounds'][1], 0.3)
        self.assertEqual(result['intervention']['assigned'], 10)
        self.assertIn('not_confidence_interval', result['bounds_type'])

    def test_A09_pending_followup_not_false_zero(self):
        result = missingness_bounds(arm(7, 3), arm(8, 0, 2))
        self.assertEqual(result['intervention']['pending'], 2)
        self.assertEqual(result['intervention']['reason'], 'followup_pending')

    def test_A11_nonsignificant_quality_cannot_establish_safety(self):
        result = decision(analysis={'interval': [-5, -2]}, quality_interval=[-1, 2],
                          quality_harm_margin=0.1, design_valid=True, measurement_valid=True)
        self.assertEqual(result['disposition'], 'insufficient_evidence')

    def test_A12_unknown_interval_method_is_descriptive(self):
        result = paired_effect([pair('one', 12, 8)], method='unavailable-backend')
        self.assertEqual(result['effect'], -4)
        self.assertIsNone(result['interval'])
        self.assertEqual(decision(analysis=result)['disposition'], 'insufficient_evidence')

    def test_A13_reused_discovery_not_confirmation(self):
        result = confirmation_status(['family-a'], ['family-a', 'family-b'])
        self.assertFalse(result['independent_confirmation'])
        self.assertEqual(result['reuse_role'], 'regression_only')

    def test_A14_direct_repair_and_A15_explicit_preference(self):
        self.assertEqual(decision(analysis={}, mechanical_repair=True)['disposition'], 'repair')
        result = decision(analysis={}, explicit_preference=True)
        self.assertEqual(result['disposition'], 'provisional')
        self.assertEqual(result['benefit_status'], 'unknown')

    def test_A16_unchanged_detector_differential_error(self):
        result = detector_effect({'assigned': 100, 'detected': 8}, {'assigned': 100, 'detected': 4},
            arm_calibration={'complete_truth': True, 'baseline_true': 10, 'intervention_true': 10})
        self.assertEqual(result['detected_rate_difference'], -0.04)
        self.assertEqual(result['true_rate_difference'], 0)
        self.assertEqual(result['claim_type'], 'descriptive_detection')

    def test_review_invalid_intervals_and_negative_margins_rejected(self):
        for invalid in ([10, -1], [0, 0], [float('-inf'), -1], [-1], [True, 2]):
            for field in ('benefit', 'quality'):
                with self.subTest(invalid=invalid, field=field), self.assertRaises(WorkflowError):
                    decision(analysis={'interval': invalid if field == 'benefit' else [-5, -2]},
                        quality_interval=invalid if field == 'quality' else [-0.01, 0.01],
                        quality_harm_margin=0.02, design_valid=True, measurement_valid=True)
        with self.assertRaises(WorkflowError):
            decision(analysis={'interval': [0.1, 0.2]}, practical_benefit=-1)

    def test_supported_keep_is_reachable_with_appropriate_bounds(self):
        result = paired_effect([pair(str(i), 10, 5) for i in range(1000)],
                               method='hoeffding', value_bounds=[-10, 10])
        self.assertEqual(result['coverage_scope'], 'single_prespecified_look')
        result = decision(analysis=result, quality_interval=[-0.01, 0.01],
            quality_harm_margin=0.02, practical_benefit=1, design_valid=True, measurement_valid=True)
        self.assertEqual(result['disposition'], 'keep')

    def test_decision_flags_do_not_coerce_strings_numbers_or_missing_values(self):
        valid = dict(analysis={'interval': [-5, -2]}, quality_interval=[-0.01, 0.01],
                     quality_harm_margin=0.02, practical_benefit=1,
                     design_valid=True, measurement_valid=True)
        for flag in ('design_valid', 'measurement_valid', 'severe_regression',
                     'mechanical_repair', 'explicit_preference'):
            for value in ('false', 'true', 0, 1, None, [], {}):
                with self.subTest(flag=flag, value=value), self.assertRaises(WorkflowError):
                    decision(**{**valid, flag: value})

    def test_finite_observations_cannot_produce_nonfinite_analysis(self):
        for a, b in ((-1e308, 1e308), (1e308, -1e308), (10 ** 1000, 0)):
            with self.subTest(a=a, b=b), self.assertRaises(WorkflowError):
                paired_effect([pair('one', a, b)])
        with self.assertRaises(WorkflowError):
            paired_effect([pair('one', 1, 2)], method='hoeffding', value_bounds=[-1e308, 1e308])

    def test_grouped_effect_matches_rational_oracle_and_ignores_order(self):
        observations = [('a', 9, 3), ('a', 5, 4), ('b', 2, 8), ('c', 8, 8)]
        groups = {}
        for group, a, b in observations:
            groups.setdefault(group, []).append(Fraction(b) - Fraction(a))
        expected = sum(sum(v) / len(v) for v in groups.values()) / len(groups)
        rows = [pair(str(i), a, b, group) for i, (group, a, b) in enumerate(observations)]
        for order in itertools.permutations(rows):
            result = paired_effect(order)
            self.assertAlmostEqual(result['effect'], float(expected))
            self.assertEqual(result['independent_units'], 3)
        exchanged = [{**r, 'baseline': r['intervention'], 'intervention': r['baseline']} for r in rows]
        self.assertAlmostEqual(paired_effect(exchanged)['effect'], -float(expected))

    def test_identical_within_family_replication_does_not_improve_precision(self):
        rows = [pair(str(i), 9, i, str(i)) for i in range(5)]
        original = paired_effect(rows, method='hoeffding', value_bounds=[-10, 10])
        repeated = paired_effect([dict(r, unit=r['unit'] + '-' + str(j)) for r in rows for j in range(7)],
                                 method='hoeffding', value_bounds=[-10, 10])
        for key in ('effect', 'interval', 'independent_units'):
            self.assertEqual(original[key], repeated[key])

    def test_more_confidence_and_larger_benefit_requirement_are_conservative(self):
        rows = [pair(str(i), 10, 5) for i in range(100)]
        intervals = [paired_effect(rows, method='hoeffding', value_bounds=[-10, 10],
                                   confidence=c)['interval'] for c in (0.8, 0.9, 0.95, 0.99)]
        for smaller, larger in zip(intervals, intervals[1:]):
            self.assertLessEqual(larger[0], smaller[0])
            self.assertGreaterEqual(larger[1], smaller[1])
        results = [decision(analysis={'interval': [-5, -2]}, quality_interval=[-0.01, 0.01],
                            quality_harm_margin=0.02, practical_benefit=m,
                            design_valid=True, measurement_valid=True)['disposition']
                   for m in (0, 1, 2, 3)]
        self.assertEqual(results, ['keep', 'keep', 'insufficient_evidence', 'insufficient_evidence'])


if __name__ == '__main__':
    unittest.main()
