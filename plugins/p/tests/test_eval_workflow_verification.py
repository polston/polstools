"""A resolved artifact alone cannot turn an imported success claim into delivery."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.records import Record, canonical, RecordRef
from retro_eval.workflow.packets import make_packet
from test_eval_workflow_records import case, outcome, TIME


class VerificationTests(unittest.TestCase):
    def _receipt_delivery(self, kind, status, **fields):
        with tempfile.TemporaryDirectory() as temp:
            store = Registry(Path(temp))
            try:
                c = case(criteria=[('check', True)])
                store.append(c, None)
                artifact = store.publish_artifact(b'authored receipt fixture', 'public_synthetic')
                receipt = {'verification_format': 1, 'case_ref': c.ref.to_dict(),
                    'criterion_key': 'check', 'contract_version': 1, 'verifier_type': kind,
                    'verifier_version': '1', 'procedure_id': 'artifact-check', 'status': status,
                    'environment': 'authored fixture without a human participant',
                    'verified_at': TIME, 'artifact_refs': [artifact], **fields}
                ref = store.publish_artifact(canonical(receipt), 'public_synthetic')
                ref['selector'] = {'kind': 'json_pointer', 'pointer': ''}
                payload = outcome(c, 'check', status, [artifact]).payload
                payload.update(verifier_type=kind, verification_receipt=ref)
                store.append(Record.create('outcome', 'check', payload, created_at=TIME), None)
                return make_packet(store, [c])['cases'][0]['delivery']
            finally:
                store.close()

    def test_human_receipts_require_consistent_boolean_attribution(self):
        for accepted, status, expected in ((True, 'pass', 'pass'), (False, 'fail', 'fail'),
                (False, 'pass', 'unknown'), ('true', 'pass', 'unknown'), (1, 'pass', 'unknown')):
            with self.subTest(accepted=accepted, status=status):
                result = self._receipt_delivery('human', status,
                    assessor_type='human', accepted=accepted)
                self.assertEqual(result['status'], expected)
        result = self._receipt_delivery('human', 'pass', assessor_type='agent', accepted=True)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(self._receipt_delivery('human', 'pass', assessor_type='human')['status'], 'unknown')

    def test_artifact_receipts_preserve_value_types_and_reject_contradictions(self):
        for expected, observed, claim, delivery in (
                (False, 0, 'pass', 'unknown'), (False, 0, 'fail', 'fail'),
                ({'a': 1, 'b': None}, {'b': None, 'a': 1}, 'pass', 'pass'),
                (0, 0, 'fail', 'unknown'), ('é', 'e\u0301', 'fail', 'fail')):
            with self.subTest(expected=expected, observed=observed, claim=claim):
                result = self._receipt_delivery('artifact', claim, expected=expected, observed=observed)
                self.assertEqual(result['status'], delivery)
        self.assertEqual(self._receipt_delivery('artifact', 'pass', expected=0)['status'], 'unknown')

    def test_inapplicability_receipt_does_not_certify_all_inapplicable_delivery(self):
        matched = self._receipt_delivery('artifact', 'not_applicable', applicability_rule='fixture-declared')
        mismatched = self._receipt_delivery('artifact', 'not_applicable', applicability_rule='different-rule')
        self.assertEqual(matched['criteria'][0][1], 'not_applicable')
        self.assertEqual(matched['reason'], 'no_applicable_acceptance_basis')
        self.assertEqual(matched['status'], 'unknown')
        self.assertEqual(mismatched['criteria'][0][1], 'unknown')
        self.assertEqual(mismatched['status'], 'unknown')

    def test_same_bytes_do_not_allow_substitution_of_another_selected_check(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Registry(Path(temp))
            self.addCleanup(store.close)
            c = case(criteria=[('build', True)])
            store.append(c, None)
            formats = [
                (canonical({'required': {'exit': 1}, 'other': {'exit': 0}}),
                 {'kind': 'json_pointer', 'pointer': '/required'},
                 {'kind': 'json_pointer', 'pointer': '/other'}),
                (b'{"exit":1}\n{"exit":0}\n',
                 {'kind': 'jsonl_pointer', 'line': 1, 'pointer': ''},
                 {'kind': 'jsonl_pointer', 'line': 2, 'pointer': ''}),
                (b'{"span_id":"required","exit":1}\n{"span_id":"other","exit":0}\n',
                 {'kind': 'normalized_span', 'span_id': 'required'},
                 {'kind': 'normalized_span', 'span_id': 'other'}),
            ]
            for index, (data, selected, unrelated) in enumerate(formats):
                output = store.publish_artifact(data, 'public_synthetic')
                bound = {**output, 'selector': selected}
                for revision, selector in enumerate((unrelated, selected), 1):
                    with self.subTest(format=index, revision=revision):
                        status = 'pass' if revision == 1 else 'fail'
                        receipt = {'verification_format': 1, 'case_ref': c.ref.to_dict(),
                            'criterion_key': 'build', 'contract_version': 1, 'verifier_type': 'code',
                            'verifier_version': '1', 'procedure_id': 'artifact-check', 'status': status,
                            'environment': 'authored binding fixture', 'verified_at': TIME,
                            'artifact_refs': [{**output, 'selector': selector}], 'executed': True,
                            'command_identity': 'authored-check', 'exit_code': revision - 1}
                        ref = store.publish_artifact(canonical(receipt), 'public_synthetic')
                        ref['selector'] = {'kind': 'json_pointer', 'pointer': ''}
                        p = outcome(c, 'build', status, [bound]).payload
                        p.update(verifier_type='code', verification_receipt=ref)
                        record = Record.create('outcome', 'binding-' + str(index), p,
                                               revision=revision, created_at=TIME)
                        store.append(record, revision - 1 or None)
                        from retro_eval.workflow.verification import verify_outcome
                        self.assertEqual(verify_outcome(c, c.payload['contract']['criteria'][0],
                                                       record, store), revision == 2)

    def test_omitted_whole_selector_matches_explicit_whole_selector(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Registry(Path(temp))
            self.addCleanup(store.close)
            c = case(criteria=[('build', True)])
            store.append(c, None)
            output = store.publish_artifact(b'authored check output', 'public_synthetic')
            bound = {k: v for k, v in output.items() if k != 'selector'}
            receipt = {'verification_format': 1, 'case_ref': c.ref.to_dict(),
                'criterion_key': 'build', 'contract_version': 1, 'verifier_type': 'code',
                'verifier_version': '1', 'procedure_id': 'artifact-check', 'status': 'pass',
                'environment': 'authored binding fixture', 'verified_at': TIME,
                'artifact_refs': [output], 'executed': True,
                'command_identity': 'authored-check', 'exit_code': 0}
            ref = store.publish_artifact(canonical(receipt), 'public_synthetic')
            ref['selector'] = {'kind': 'json_pointer', 'pointer': ''}
            p = outcome(c, 'build', 'pass', [bound]).payload
            p.update(verifier_type='code', verification_receipt=ref)
            store.append(Record.create('outcome', 'whole', p, created_at=TIME), None)
            self.assertEqual(make_packet(store, [c])['cases'][0]['delivery']['status'], 'pass')

    def test_F37_corrected_outcome_invalidates_historical_packet_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Registry(Path(temp))
            self.addCleanup(store.close)
            c = case(criteria=[('build', True)])
            unrelated = case('unrelated', criteria=[])
            store.import_records([c, unrelated])
            output = store.publish_artifact(b'authored execution result', 'public_synthetic')
            def verified(status, revision):
                receipt = {'verification_format': 1, 'case_ref': c.ref.to_dict(),
                    'criterion_key': 'build', 'contract_version': 1, 'verifier_type': 'code',
                    'verifier_version': '1', 'procedure_id': 'artifact-check', 'status': status,
                    'environment': 'authored fixture', 'verified_at': TIME,
                    'artifact_refs': [output], 'executed': True,
                    'command_identity': 'authored-check', 'exit_code': 0 if status == 'pass' else 1}
                ref = store.publish_artifact(canonical(receipt), 'public_synthetic')
                ref['selector'] = {'kind': 'json_pointer', 'pointer': ''}
                p = outcome(c, 'build', status, [output]).payload
                p.update(verifier_type='code', verification_receipt=ref,
                         revision_reason='Authored correction probe', evidence_refs=[output])
                return Record.create('outcome', 'build', p, revision=revision, created_at=TIME)
            store.append(verified('pass', 1), None)
            old = make_packet(store, [c])
            encoded = canonical(old)
            self.assertEqual(old['cases'][0]['delivery']['status'], 'pass')
            store.append(verified('fail', 2), 1)
            roots = [RecordRef.from_dict(r) for r in old['cases'][0]['input_refs']]
            self.assertFalse(store.applicability(roots)['current'])
            self.assertEqual(canonical(old), encoded)
            self.assertEqual(make_packet(store, [c])['cases'][0]['delivery']['status'], 'fail')
            self.assertTrue(store.applicability([unrelated.ref])['current'])

    def test_claimed_command_and_irrelevant_receipt_cannot_verify_delivery(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Registry(Path(temp))
            try:
                c = case(criteria=[('build', True)])
                store.append(c, None)
                output = store.publish_artifact(b'authored successful build result', 'public_synthetic')
                o = outcome(c, 'build', 'pass', [output])
                store.append(o, None)
                self.assertEqual(make_packet(store, [c])['cases'][0]['delivery']['status'], 'unknown')
                receipt = {'verification_format': 1, 'case_ref': c.ref.to_dict(),
                    'criterion_key': 'build', 'contract_version': 1, 'verifier_type': 'code',
                    'verifier_version': '1', 'procedure_id': 'artifact-check', 'status': 'pass',
                    'environment': 'authored isolated fixture', 'verified_at': TIME,
                    'artifact_refs': [output], 'executed': True,
                    'command_identity': 'authored-build-check', 'exit_code': 0}
                receipt_ref = store.publish_artifact(canonical(receipt), 'public_synthetic')
                receipt_ref['selector'] = {'kind': 'json_pointer', 'pointer': ''}
                value = o.to_dict()
                value.update(revision=2, supersedes_revision=1)
                value['payload'].update(verifier_type='code', verification_receipt=receipt_ref)
                store.append(Record.from_dict(value), 1)
                self.assertEqual(make_packet(store, [c])['cases'][0]['delivery']['status'], 'pass')
                receipt['executed'] = False
                bad_ref = store.publish_artifact(canonical(receipt), 'public_synthetic')
                bad_ref['selector'] = {'kind': 'json_pointer', 'pointer': ''}
                value.update(revision=3, supersedes_revision=2)
                value['payload']['verification_receipt'] = bad_ref
                store.append(Record.from_dict(value), 2)
                self.assertEqual(make_packet(store, [c])['cases'][0]['delivery']['status'], 'unknown')
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
