"""Malformed evidence stays unknown; selectors never silently change meaning."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.evidence import resolve_evidence
from retro_eval.workflow.packets import make_packet
from retro_eval.workflow.records import Record, canonical
from retro_eval.workflow.registry import Registry
from retro_eval.proposal_report import _json_pointer
from test_eval_workflow_records import case, outcome, TIME


class SelectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Registry(Path(self.temp.name))
        self.addCleanup(self.store.close)

    def test_pointer_standard_examples_and_invalid_tokens(self):
        # RFC 6901 sections 3–5: decoding order, object keys and array grammar.
        data = {'foo': ['bar', 'baz'], '': 0, 'a/b': 1, 'm~n': 8,
                '~1': 'literal', '01': 'object key', '١': 'unicode key', 'a~2b': 9}
        valid = {'': data, '/foo': ['bar', 'baz'], '/foo/0': 'bar', '/': 0,
                 '/a~1b': 1, '/m~0n': 8, '/~01': 'literal', '/01': 'object key',
                 '/١': 'unicode key', '/a~02b': 9}
        for pointer, expected in valid.items():
            with self.subTest(pointer=pointer):
                self.assertEqual(_json_pointer(data, pointer), expected)
        for pointer in (None, [], False, 0, 'foo', '/foo/01', '/foo/١', '/foo/-',
                        '/foo/-1', '/foo/+1', '/foo/2', '/a~2b', '/m~n', '/bad~'):
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                _json_pointer(data, pointer)

    def test_malformed_selector_shapes_do_not_escape_the_resolution_boundary(self):
        ref = self.store.publish_artifact(b'{"value":1}\n', 'public_synthetic')
        malformed = [None, [], 'reference', 1, False]
        malformed += [{**ref, 'selector': value} for value in (None, [], '', 1, False)]
        malformed += [{**ref, 'selector': selector} for selector in (
            {'kind': 'json_pointer', 'pointer': None},
            {'kind': 'jsonl_pointer', 'line': True, 'pointer': ''},
            {'kind': 'normalized_span', 'span_id': None},
            {'kind': 'normalized_span', 'span_id': ''},
            {'kind': 'normalized_span', 'span_id': 1})]
        for reference in malformed:
            with self.subTest(reference=reference):
                self.assertEqual(resolve_evidence(reference, self.store).status, 'invalid')
        # A missing span identity must not accidentally select an unrelated row.
        self.assertEqual(resolve_evidence(ref, self.store).status, 'resolved')

    def test_invalid_selected_evidence_preserves_history_and_packet_unknown(self):
        c = case(criteria=[('build', True)])
        self.store.append(c, None)
        ref = self.store.publish_artifact(b'{"checks":[true,true]}', 'public_synthetic')
        for revision, selector in enumerate((None,
                {'kind': 'json_pointer', 'pointer': '/checks/01'}), 1):
            evidence = {**ref, 'selector': selector}
            record = outcome(c, 'build', 'pass', [evidence])
            value = record.to_dict()
            value.update(revision=revision, supersedes_revision=revision - 1 or None)
            record = Record.from_dict(value)
            self.store.append(record, revision - 1 or None)
            packet = make_packet(self.store, [c])
            self.assertEqual(packet['cases'][0]['delivery']['status'], 'unknown')
            self.assertFalse(packet['cases'][0]['current_applicability']['current'])
            self.assertEqual(self.store.get(record.ref).encoded, record.encoded)

    def test_malformed_nested_receipt_artifact_is_unknown_not_a_packet_crash(self):
        c = case(criteria=[('build', True)])
        self.store.append(c, None)
        output = self.store.publish_artifact(b'authored result', 'public_synthetic')
        receipt = {'verification_format': 1, 'case_ref': c.ref.to_dict(),
            'criterion_key': 'build', 'contract_version': 1, 'verifier_type': 'code',
            'verifier_version': '1', 'procedure_id': 'artifact-check', 'status': 'pass',
            'environment': 'authored malformed receipt', 'verified_at': TIME,
            'artifact_refs': [None], 'executed': True, 'command_identity': 'authored-check',
            'exit_code': 0}
        ref = self.store.publish_artifact(canonical(receipt), 'public_synthetic')
        ref['selector'] = {'kind': 'json_pointer', 'pointer': ''}
        p = outcome(c, 'build', 'pass', [output]).payload
        p.update(verifier_type='code', verification_receipt=ref)
        self.store.append(Record.create('outcome', 'build', p, created_at=TIME), None)
        self.assertEqual(make_packet(self.store, [c])['cases'][0]['delivery']['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
