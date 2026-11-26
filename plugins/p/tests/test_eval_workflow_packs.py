import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.packs import Pack, PackRegistry, evaluate_review, evaluate_design, diagnosis
from retro_eval.workflow.errors import WorkflowError


class PackTests(unittest.TestCase):
    def test_extension_registration_and_disabled_pack(self):
        custom = Mock(return_value={'status': 'completed', 'candidates': []})
        disabled = Mock(side_effect=AssertionError('disabled pack ran'))
        registry = PackRegistry([Pack('custom', '1', custom), Pack('disabled', '1', disabled)])
        result = registry.run(['custom'], {})
        self.assertEqual(result[0]['pack'], 'custom')
        self.assertEqual(custom.call_count, 1)
        disabled.assert_not_called()

    def test_missing_capability_has_explicit_abstention(self):
        callback = Mock()
        registry = PackRegistry([Pack('requires-usage', '1', callback, ('usage',))])
        result = registry.run(['requires-usage'], {'capabilities': {'usage': 'available'}})
        self.assertEqual(result[0]['status'], 'abstained')
        callback.assert_not_called()

    def test_review_true_denominator_duplicate_and_severe_singleton(self):
        result = evaluate_review([
            {'defect_key': 'ordinary', 'reviewer': 'one'},
            {'defect_key': 'ordinary', 'reviewer': 'two'},
            {'defect_key': 'severe', 'reviewer': 'one'},
            {'defect_key': 'false', 'reviewer': 'two'},
            {'defect_key': 'unresolved', 'reviewer': 'one'},
        ], {'ordinary': {'truth': True, 'severity': 'minor'},
            'severe': {'truth': True, 'severity': 'severe', 'usefulness': True},
            'missed': {'truth': True, 'severity': 'major'},
            'false': {'truth': False, 'harmful_fix_verified': True}}, effort_seconds=120)
        self.assertEqual(result['confirmed'], 2)
        self.assertEqual(result['false_positives'], 1)
        self.assertEqual(result['unknown'], 1)
        self.assertEqual(result['escaped_defects'], ['missed'])
        self.assertEqual(result['recall'], 2 / 3)
        severe = next(r for r in result['findings'] if r['defect_key'] == 'severe')
        self.assertTrue(severe['retain_severe_singleton'])
        self.assertEqual(result['harmful_fixes'], 1)

    def test_independently_confirmed_reports_do_not_establish_review_recall(self):
        findings = [{'defect_key': 'reported', 'reviewer': 'one'}]
        oracle = {'reported': {'truth': True, 'severity': 'severe'}}
        for scope in (None, 'reported_findings', 'unknown'):
            result = evaluate_review(findings, oracle, oracle_kind='independent_natural',
                                     oracle_scope=scope)
            self.assertEqual(result['confirmed'], 1)
            self.assertTrue(result['findings'][0]['retain_severe_singleton'])
            self.assertEqual(result['known_defect_capture'], 1)
            self.assertIsNone(result['recall'])
            self.assertIsNone(result['recall_scope'])
            self.assertEqual(result['recall_unavailable_reason'],
                             'oracle_selection_does_not_establish_denominator')
            self.assertFalse(result['natural_population_claim'])
        oracle['reference-miss'] = {'truth': True}
        result = evaluate_review(findings, oracle, oracle_kind='independent_natural',
                                 oracle_scope='independent_reference_set')
        self.assertEqual(result['recall'], 0.5)
        self.assertEqual(result['recall_scope'], 'independent_reference_set')
        self.assertEqual(result['escaped_defects'], ['reference-miss'])
        self.assertFalse(result['natural_population_claim'])

    def test_review_reference_scope_validation_and_empty_denominator(self):
        for kind, scope in (('seeded', 'independent_reference_set'),
                            ('independent_natural', 'seed_set'), ('seeded', True),
                            ('seeded', []), ('seeded', 'complete')):
            with self.subTest(kind=kind, scope=scope), self.assertRaises(WorkflowError):
                evaluate_review([], {}, oracle_kind=kind, oracle_scope=scope)
        empty = evaluate_review([], {})
        self.assertIsNone(empty['recall'])
        self.assertIsNone(empty['known_defect_capture'])
        self.assertEqual(empty['recall_scope'], 'seed_set')
        self.assertEqual(empty['recall_unavailable_reason'], 'no_known_positives')
        for unknown in (None, 'true', 1):
            result = evaluate_review([{'defect_key': 'found', 'reviewer': 'one'}],
                {'found': {'truth': True}, 'unjudged': {'truth': unknown}},
                oracle_kind='independent_natural', oracle_scope='independent_reference_set')
            self.assertEqual(result['confirmed'], 1)
            self.assertEqual(result['oracle_unknown_truth'], 1)
            self.assertEqual(result['known_defect_capture'], 1)
            self.assertIsNone(result['recall'])
            self.assertEqual(result['recall_unavailable_reason'], 'oracle_truth_incomplete')

    def test_design_worked_B_challenge_and_counterchallenge(self):
        challenge = {'prepared_independently': True, 'withheld_until_design_frozen': True,
                     'evidence_refs': [{'fixture': 'future-change'}]}
        def alternatives(a, b):
            return [{'key': 'simple', 'initial_effort': 20, 'change_effort': a,
                     'current_acceptance': 'pass', 'future_acceptance': 'pass'},
                    {'key': 'modular', 'initial_effort': 28, 'change_effort': b,
                     'current_acceptance': 'pass', 'future_acceptance': 'pass'}]
        stream = evaluate_design(alternatives(40, 18), challenge)
        one_off = evaluate_design(alternatives(5, 9), challenge)
        self.assertEqual(stream['least_measured_effort'], 'modular')
        self.assertEqual(one_off['least_measured_effort'], 'simple')
        self.assertIsNone(stream['maintenance_judgment'])
        with self.assertRaises(WorkflowError):
            evaluate_design(alternatives(40, 18), {**challenge, 'withheld_until_design_frozen': False})

    def test_design_unknown_cost_preserves_working_alternative_without_ranking(self):
        challenge = {'prepared_independently': True, 'withheld_until_design_frozen': True,
                     'evidence_refs': [{'fixture': 'future-change'}]}
        alternatives = [
            {'key': 'existing', 'initial_effort': None, 'change_effort': 12,
             'current_acceptance': 'pass', 'future_acceptance': 'pass'},
            {'key': 'new', 'initial_effort': 30, 'change_effort': 5,
             'current_acceptance': 'pass', 'future_acceptance': 'pass'}]
        result = evaluate_design(alternatives, challenge)
        self.assertIsNone(result['least_measured_effort'])
        self.assertEqual(result['effort_comparison'], 'incomplete')
        self.assertTrue(result['alternatives'][0]['eligible'])
        self.assertIsNone(result['alternatives'][0]['total_effort'])
        self.assertEqual(result['alternatives'][0]['change_effort'], 12)
        alternatives[0]['initial_effort'] = 0
        result = evaluate_design(alternatives, challenge)
        self.assertEqual(result['least_measured_effort'], 'existing')
        self.assertEqual(result['effort_comparison'], 'complete')
        alternatives[0].pop('initial_effort')
        self.assertIsNone(evaluate_design(alternatives, challenge)['least_measured_effort'])
        alternatives[0]['future_acceptance'] = 'fail'
        self.assertEqual(evaluate_design(alternatives, challenge)['least_measured_effort'], 'new')
        alternatives[1]['future_acceptance'] = 'unknown'
        self.assertEqual(evaluate_design(alternatives, challenge)['effort_comparison'],
                         'no_eligible_alternatives')

    def test_design_unknown_cost_does_not_hide_invalid_observed_cost(self):
        challenge = {'prepared_independently': True, 'withheld_until_design_frozen': True,
                     'evidence_refs': [{'fixture': 'future-change'}]}
        for invalid in (-1, True, float('nan'), float('inf'), 'unknown'):
            with self.subTest(cost=invalid), self.assertRaises(WorkflowError):
                evaluate_design([{'key': 'invalid', 'initial_effort': None,
                    'change_effort': invalid, 'current_acceptance': 'pass',
                    'future_acceptance': 'pass'}], challenge)

    def test_design_provenance_requires_explicit_true_and_total_stays_finite(self):
        challenge = {'prepared_independently': True, 'withheld_until_design_frozen': True,
                     'evidence_refs': [{'fixture': 'future-change'}]}
        alternative = {'key': 'design', 'initial_effort': 1, 'change_effort': 2,
                       'current_acceptance': 'pass', 'future_acceptance': 'pass'}
        for field in ('prepared_independently', 'withheld_until_design_frozen'):
            for invalid in (None, False, 1, 'false', 'unknown', ['yes']):
                with self.subTest(field=field, value=invalid), self.assertRaises(WorkflowError):
                    evaluate_design([alternative], {**challenge, field: invalid})
        with self.assertRaises(WorkflowError):
            evaluate_design([{**alternative, 'initial_effort': 1e308,
                              'change_effort': 1e308}], challenge)

    def test_summary_retrieval_does_not_authenticate_execution(self):
        context = {'source': 'authored', 'source_key': 'one', 'records': [],
            'excerpts': {'prompts': {}, 'tools': {'check': {'tool_input': 'run unittest',
                                                         'tool_result': 'Ran 4 tests\nOK'}}},
            'excerpts_ref': {'sha256': 'a' * 64, 'content_class': 'private_redacted'}}
        candidate = diagnosis(context)['candidates'][0]
        self.assertFalse(candidate['positive_practice'])
        self.assertIn('Execution is unverified', candidate['interpretation'])
        self.assertEqual(candidate['benefit_status'], 'unestablished')
        self.assertEqual(candidate['status'], 'candidate')


if __name__ == '__main__':
    unittest.main()
