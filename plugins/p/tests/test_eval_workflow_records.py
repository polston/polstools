"""Authored acceptance oracles for immutable records and delivery semantics."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.errors import WorkflowError, UnsupportedVersion
from retro_eval.workflow.records import Record, canonical, strict_loads, evaluate_delivery

TIME = '2026-01-01T00:00:00Z'


def case(key='case-a', criteria=None, revision=1):
    if criteria is None:
        criteria = [('build', True), ('behavior', True)]
    return Record.create('case', key, {
        'intent': 'Deliver the requested change', 'task_family': 'authored',
        'split_group': key, 'origin': 'retrospective', 'lifecycle': 'open',
        'contract': {'version': revision, 'criteria': [
            {'key': k, 'description': k, 'verification_method': 'artifact-check',
             'required': required} for k, required in criteria]}},
        revision=revision, created_at=TIME)


def outcome(c, key, status, refs=None):
    return Record.create('outcome', key, {'case_ref': c.ref.to_dict(),
        'contract_version': c.payload['contract']['version'],
        'criterion_key': key, 'status': status, 'verifier_type': 'test-oracle',
        'verifier_version': '1', 'verified_at': TIME,
        'evidence_refs': [{'valid': True}] if refs is None else refs,
        'applicability_rule': 'fixture-declared' if status == 'not_applicable' else None},
        created_at=TIME)


def delivery(c, *rows):
    return evaluate_delivery(c, rows, lambda r: r.get('valid', False))


def analysis_payload(**values):
    return {'method': 'authored-descriptive', 'method_version': '1',
            'code_version': 'authored', 'seed': 0, 'input_refs': [],
            'assignment_refs': [], 'exposure_refs': [], 'outcome_refs': [],
            'evidence_refs': [], 'exclusions': [], 'intervals': [],
            'limitations': ['Authored fixture, no population claim.'],
            'estimates': {}, **values}


class RecordsTests(unittest.TestCase):
    def test_every_kind_rejects_empty_payload(self):
        from retro_eval.workflow.records import KINDS
        for kind in KINDS:
            with self.subTest(kind=kind), self.assertRaises(WorkflowError):
                Record.create(kind, 'empty', {})

    def test_F12_F13_no_required_basis(self):
        for criteria in ([], [('build', False)]):
            c = case(criteria=criteria)
            result = delivery(c, outcome(c, 'build', 'pass'))
            self.assertEqual((result.status, result.reason),
                             ('unknown', 'no_applicable_acceptance_basis'))

    def test_F14_all_inapplicable(self):
        c = case()
        self.assertEqual(delivery(c, *(outcome(c, k, 'not_applicable')
            for k in ('build', 'behavior'))).reason, 'no_applicable_acceptance_basis')

    def test_F15_unknown_applicability(self):
        c = case()
        self.assertEqual(delivery(c, outcome(c, 'build', 'pass')).status, 'unknown')

    def test_F16_required_failure_dominates_unknown(self):
        c = case(criteria=[('build', True), ('behavior', True), ('scope', True)])
        result = delivery(c, outcome(c, 'build', 'pass'), outcome(c, 'behavior', 'fail'))
        self.assertEqual(result.status, 'fail')
        self.assertIn(('scope', 'unknown'), result.criteria)

    def test_F17_exact_contract_and_evidence(self):
        c = case()
        rows = [outcome(c, k, 'pass') for k in ('build', 'behavior')]
        self.assertEqual(delivery(c, *rows).status, 'pass')
        self.assertEqual(delivery(case(revision=2), *rows).status, 'unknown')
        self.assertEqual(delivery(c, rows[0], outcome(c, 'behavior', 'pass', [])).status, 'unknown')
        self.assertEqual(delivery(c, rows[0], outcome(c, 'behavior', 'pass', [{'valid': False}])).status, 'unknown')

    def test_F18_F24_unavailable_and_observed_zero(self):
        payload = {'measure_id': 'usage', 'measure_version': '1', 'unit': 'tokens',
                   'method': 'captured-field', 'availability': 'not_supported', 'value': None}
        self.assertIsNone(Record.create('observation', 'usage', payload).payload['value'])
        payload.update(availability='observed', value=0)
        self.assertEqual(Record.create('observation', 'usage', payload).payload['value'], 0)
        payload.update(availability='not_observed')
        with self.assertRaises(WorkflowError):
            Record.create('observation', 'usage', payload)

    def test_F29_invalid_numeric_types(self):
        for patch in ({'revision': True}, {'record_format': True},
                      {'payload': {**case().payload, 'count': True}},
                      {'payload': {**case().payload, 'duration_ms': -1}},
                      {'payload': {**case().payload, 'sampling_weight': float('nan')}},
                      {'payload': {**case().payload, 'sampling_weight': float('inf')}}):
            with self.subTest(patch=patch), self.assertRaises(WorkflowError):
                Record.from_dict({**case().to_dict(), **patch})
        for raw in ('NaN', 'Infinity', '-Infinity', '1e999'):
            with self.assertRaises(WorkflowError):
                strict_loads(raw)

    def test_F30_duplicate_keys(self):
        with self.assertRaises(WorkflowError):
            strict_loads('{"contract":{"version":1,"version":2}}')

    def test_F31_canonical_encoding(self):
        self.assertEqual(canonical({'b': 1.0, 'a': 'é'}), b'{"a":"\\u00e9","b":1.0}')
        self.assertNotEqual(canonical(1), canonical(1.0))
        self.assertNotEqual(canonical(-0.0), canonical(0.0))

    def test_F11_unknown_format(self):
        value = case().to_dict()
        value['record_format'] = 999
        with self.assertRaises(UnsupportedVersion):
            Record.from_dict(value)

    def test_record_cannot_be_mutated_through_nested_payload(self):
        c = case()
        old = c.encoded
        p = c.payload
        p['contract']['criteria'].clear()
        self.assertEqual(c.encoded, old)
        self.assertEqual(len(c.payload['contract']['criteria']), 2)

    def test_prospective_requires_explicit_acceptance(self):
        value = case(criteria=[]).to_dict()
        value['payload']['origin'] = 'prospective'
        with self.assertRaises(WorkflowError):
            Record.from_dict(value)


if __name__ == '__main__':
    unittest.main()
