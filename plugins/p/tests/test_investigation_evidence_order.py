"""Same-run evidence must exist before the claim it supports."""
import tempfile
from pathlib import Path
import unittest

import test_investigation_corrections as fixtures
from test_investigation_state import frame, action
from retro_eval.investigation import Investigation
from retro_eval.workflow.errors import WorkflowError
from retro_eval.workflow.records import Record, RecordRef
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.investigation_state import projection_key


class EvidenceOrderTests(unittest.TestCase):
    setUp = fixtures.CorrectionTests.setUp
    tearDown = fixtures.CorrectionTests.tearDown
    record = fixtures.CorrectionTests.record
    artifact = fixtures.CorrectionTests.artifact
    make_event = fixtures.CorrectionTests.make_event

    def event_ref(self, sequence):
        return RecordRef(kind='investigation_event',
            key=projection_key(self.root).rsplit(':', 1)[1] + ':' + str(sequence),
            revision=1, study_scope=self.root.study_scope)

    def acceptance(self, evidence):
        return {'criterion': 'behavior', 'status': 'pass', 'frame_seq': 0,
                'evidence': evidence, 'inputs': [], 'procedure': 'authored check',
                'verifier': 'authored', 'claim_type': 'software', 'limitations': []}

    def research(self, evidence):
        return {'question': 'inspect support', 'sources': evidence,
                'alternatives': [], 'counterevidence': [], 'finding': 'observed',
                'next_action': 'inspect'}

    def test_self_support_cannot_enter_through_import_or_generic_append(self):
        data = self.acceptance([self.event_ref(1).to_dict()])
        before = self.api.checkpoint(self.root)
        with self.assertRaises(WorkflowError):
            self.record('acceptance', data)
        event = self.make_event('acceptance', data)
        with self.assertRaises(WorkflowError):
            self.store.import_records([event])
        with self.assertRaises(WorkflowError):
            self.store.append(event, None)
        self.assertEqual(self.api.checkpoint(self.root), before)

    def test_import_cannot_use_later_same_run_support(self):
        first = self.make_event('acceptance', self.acceptance([self.event_ref(2).to_dict()]))
        second = Record.create('investigation_event', self.event_ref(2).key,
            {'version': 1, 'root_ref': self.root.to_dict(), 'sequence': 2,
             'previous_sha256': first.sha256, 'at_ms': self.now,
             'event_type': 'research', 'data': self.research([self.artifact()])},
            study_scope=self.root.study_scope, producer='authored', producer_version='1')
        before = self.api.checkpoint(self.root)
        with self.assertRaises(WorkflowError):
            self.store.import_records([second, first])
        self.assertEqual(self.api.checkpoint(self.root), before)

    def test_live_research_cannot_cite_itself(self):
        before = self.api.checkpoint(self.root)
        with self.assertRaises(WorkflowError):
            self.record('research', self.research([self.event_ref(1).to_dict()]))
        self.assertEqual(self.api.checkpoint(self.root), before)

    def test_import_root_cannot_depend_on_itself_or_its_future_event(self):
        original = self.store.get(self.root)
        for own_root in (True, False):
            with self.subTest(own_root=own_root), tempfile.TemporaryDirectory() as tmp:
                payload = original.payload
                payload['frame']['inputs'] = [(self.root if own_root else self.event_ref(1)).to_dict()]
                root = Record.create('investigation', self.root.key, payload,
                    study_scope=self.root.study_scope, producer='authored', producer_version='1')
                records = [root]
                if not own_root:
                    records.append(self.make_event('research', self.research([])))
                store = Registry(Path(tmp) / 'import')
                try:
                    with self.assertRaises(WorkflowError):
                        store.import_records(records)
                    self.assertEqual(store.heads(), [])
                finally:
                    store.close()

    def _cross_root_result_support(self, future):
        other = self.api.begin('other', frame(), deadline_ms=2000,
            limits={'actions': 10}, authority=['read'],
            capabilities={'execution': {'mode': 'advisory', 'scope': 'authored'}})

        def ref(root, sequence):
            return RecordRef(kind='investigation_event',
                key=projection_key(root).rsplit(':', 1)[1] + ':' + str(sequence),
                revision=1, study_scope=root.study_scope)

        def event(root, sequence, previous, typ, data):
            return Record.create('investigation_event', ref(root, sequence).key,
                {'version': 1, 'root_ref': root.to_dict(), 'sequence': sequence,
                 'previous_sha256': previous, 'at_ms': self.now,
                 'event_type': typ, 'data': data}, study_scope=root.study_scope,
                producer='authored', producer_version='1')

        acceptance = self.acceptance([ref(other, 2).to_dict()])
        research = self.research([self.artifact()])
        first = event(self.root, 1, None, 'acceptance' if future else 'research',
                      acceptance if future else research)
        second = event(self.root, 2, first.sha256, 'research' if future else 'acceptance',
                       research if future else acceptance)
        support = second if future else first
        admission = event(other, 1, None, 'action', action(inputs=[support.ref.to_dict()]))
        result = event(other, 2, admission.sha256, 'result',
            {'action_id': 'inspect', 'status': 'succeeded', 'usage': {'actions': 1},
             'evidence': [self.artifact()], 'producer': 'authored', 'origin': 'agent',
             'observation': 'observed'})
        return [result, second, admission, first]

    def test_result_support_cannot_hide_future_inputs_through_another_run(self):
        records = self._cross_root_result_support(future=True)
        before = self.api.checkpoint(self.root)
        with self.assertRaises(WorkflowError):
            self.store.import_records(records)
        self.assertEqual(self.api.checkpoint(self.root), before)

    def test_result_support_can_use_earlier_inputs_from_another_run(self):
        self.store.import_records(self._cross_root_result_support(future=False))
        self.assertTrue(self.api.audit(self.root)['ready'])

    def test_earlier_evidence_and_artifacts_survive_reversed_bundle_order(self):
        first = self.record('research', self.research([self.artifact()]))
        self.record('acceptance', self.acceptance([first.ref.to_dict(), self.artifact()]))
        self.record('finish', {'status': 'complete', 'reason': 'accepted', 'next_action': 'report'})
        before = self.api.checkpoint(self.root)
        self.assertTrue(before['completion_current'])
        with tempfile.TemporaryDirectory() as tmp:
            store = Registry(Path(tmp) / 'import')
            try:
                bundle = self.store.export_bundle()
                bundle['records'].reverse()
                store.import_bundle(bundle)
                self.assertEqual(Investigation(store, clock=lambda: self.now).checkpoint(self.root), before)
            finally:
                store.close()
