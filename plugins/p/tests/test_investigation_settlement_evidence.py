"""Final settlement needs a resolvable receipt, independent of its currentness."""
from contextlib import closing
from pathlib import Path
import tempfile
import unittest

import test_investigation_state as fixtures
import test_investigation_corrections as corrections
from test_eval_workflow_records import case
from retro_eval.investigation import Investigation
from retro_eval.workflow.errors import WorkflowError
from retro_eval.workflow.registry import Registry


class SettlementEvidenceTests(unittest.TestCase):
    setUp = fixtures.InvestigationTests.setUp
    tearDown = fixtures.InvestigationTests.tearDown
    record = fixtures.InvestigationTests.record
    artifact = fixtures.InvestigationTests.artifact
    make_event = corrections.CorrectionTests.make_event

    def evidence(self, pointer='/absent'):
        return {**self.artifact(b'{"observed":false}'),
                'selector': {'kind': 'json_pointer', 'pointer': pointer}}

    def result(self, status, evidence, key='inspect'):
        return {'action_id': key, 'status': status, 'usage': {'actions': 0},
                'evidence': evidence, 'producer': 'authored', 'origin': 'agent',
                'observation': 'receipt for declared operations'}

    def reject_live_and_import(self, result):
        before = self.api.checkpoint(self.root)
        with self.assertRaises(WorkflowError):
            self.api.settle(self.root, before['sequence'], result)
        self.assertEqual(before, self.api.checkpoint(self.root))
        with self.assertRaises(WorkflowError):
            self.store.import_records([self.make_event('result', result)])
        self.assertEqual(before, self.api.checkpoint(self.root))

    def test_final_results_cannot_release_allowance_with_absent_content(self):
        for status in ('succeeded', 'failed'):
            with self.subTest(status=status):
                self.record('action', fixtures.action(status))
                self.reject_live_and_import(self.result(status, [self.evidence()], key=status))

    def test_imported_final_results_need_resolvable_receipts(self):
        for status in ('succeeded', 'failed'):
            with self.subTest(status=status):
                self.record('action', fixtures.action(status))
                before = self.api.checkpoint(self.root)
                event = self.make_event('result', self.result(status, [self.evidence()], key=status))
                with self.assertRaises(WorkflowError):
                    self.store.import_records([event])
                self.assertEqual(before, self.api.checkpoint(self.root))

    def test_transitive_unresolved_receipt_cannot_release_allowance(self):
        research = self.record('research', {'question': 'inspect',
            'sources': [self.evidence()], 'alternatives': [], 'counterevidence': [],
            'finding': 'content not resolved', 'next_action': 'retrieve receipt'})
        self.record('action', fixtures.action())
        self.reject_live_and_import(self.result('succeeded', [research.ref.to_dict()]))

    def test_incomplete_results_retain_reservation_and_unresolved_observations(self):
        self.record('action', fixtures.action())
        for status in ('uncertain', 'interrupted'):
            self.record('result', self.result(status, [self.evidence()]))
            state = self.api.checkpoint(self.root)
            self.assertEqual(state['grants']['root']['held'], {'actions': 1})
            self.assertFalse(state['audit']['ready'])
        self.assert_roundtrip()

    def assert_roundtrip(self):
        before = self.api.checkpoint(self.root)
        with tempfile.TemporaryDirectory() as tmp, closing(Registry(Path(tmp) / 'import')) as store:
            store.import_bundle(self.store.export_bundle())
            self.assertEqual(Investigation(store, clock=lambda: self.now).checkpoint(self.root), before)

    def test_false_value_is_a_resolvable_receipt(self):
        self.record('action', fixtures.action())
        self.record('result', self.result('failed', [self.evidence('/observed')]))
        self.assertEqual(self.api.checkpoint(self.root)['grants']['root']['held'], {'actions': 0})
        self.assert_roundtrip()

    def test_older_revision_can_record_actual_execution(self):
        original = case()
        self.store.append(original, None)
        self.record('action', fixtures.action())
        self.store.append(case(revision=2), 1)
        self.record('result', self.result('succeeded', [original.ref.to_dict()]))
        self.assertEqual(self.api.checkpoint(self.root)['grants']['root']['held'], {'actions': 0})
        self.assert_roundtrip()


if __name__ == '__main__':
    unittest.main()
