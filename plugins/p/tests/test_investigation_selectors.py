"""Immutable selector validity is separate from historical revision freshness."""
import tempfile
from pathlib import Path
import unittest

import test_investigation_corrections as fixtures
import test_investigation_state as state_fixtures
from test_investigation_state import action
from test_eval_workflow_records import case
from retro_eval.investigation import Investigation
from retro_eval.workflow.errors import WorkflowError
from retro_eval.workflow.registry import Registry


class SelectorReplayTests(unittest.TestCase):
    setUp = fixtures.CorrectionTests.setUp
    tearDown = fixtures.CorrectionTests.tearDown
    record = fixtures.CorrectionTests.record
    artifact = fixtures.CorrectionTests.artifact
    make_event = fixtures.CorrectionTests.make_event

    def evidence(self, valid=False):
        return {**self.artifact(b'{"observed":false}'),
                'selector': {'kind': 'json_pointer',
                             'pointer': '/observed' if valid else '/absent'}}

    def acceptance(self, evidence, status='pass', frame_seq=0):
        return {'criterion': 'behavior', 'status': status, 'frame_seq': frame_seq,
                'evidence': evidence, 'inputs': [], 'procedure': 'inspect',
                'verifier': 'authored', 'claim_type': 'software', 'limitations': []}

    def assert_rejected_live_and_import(self, typ, data):
        before = self.api.checkpoint(self.root)
        with self.assertRaises(WorkflowError):
            self.record(typ, data)
        with self.assertRaises(WorkflowError):
            self.store.import_records([self.make_event(typ, data)])
        self.assertEqual(self.api.checkpoint(self.root), before)

    def test_absent_selector_cannot_support_passing_acceptance(self):
        self.assert_rejected_live_and_import('acceptance', self.acceptance([self.evidence()]))

    def test_invalid_transitive_selector_cannot_support_passing_acceptance(self):
        research = self.record('research', {'question': 'inspect', 'sources': [self.evidence()],
            'alternatives': [], 'counterevidence': [], 'finding': 'unresolved',
            'next_action': 'inspect source'})
        self.assert_rejected_live_and_import('acceptance', self.acceptance([research.ref.to_dict()]))

    def test_invalid_frame_selector_prevents_imported_completion(self):
        changed = self.api.checkpoint(self.root)['frame']
        changed['inputs'] = [self.evidence()]
        update = self.record('frame', {'frame': changed, 'basis': [self.artifact()],
                                       'source': 'requester'})
        self.record('acceptance', self.acceptance([self.artifact()],
                                                 frame_seq=update.payload['sequence']))
        self.assert_rejected_live_and_import('finish',
            {'status': 'complete', 'reason': 'accepted', 'next_action': 'report'})

    def test_invalid_selector_cannot_answer_question(self):
        self.record('question', state_fixtures.InvestigationTests.question(self))
        self.assert_rejected_live_and_import('answer', {'question_id': 'preference',
            'frame_seq': 0, 'response': 'direct', 'respondent': 'requester',
            'origin': 'requester', 'evidence': [self.evidence()]})

    def test_invalid_selector_cannot_authorize_resume(self):
        self.record('finish', {'status': 'stopped', 'reason': 'pause', 'next_action': 'await direction'})
        self.assert_rejected_live_and_import('resume', {'source': 'requester',
            'reason': 'continue', 'evidence': [self.evidence()]})

    def test_invalid_selector_cannot_support_action_inputs(self):
        self.assert_rejected_live_and_import('action', action(inputs=[self.evidence()]))

    def imported_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Registry(Path(tmp) / 'import')
            try:
                store.import_bundle(self.store.export_bundle())
                return Investigation(store, clock=lambda: self.now).checkpoint(self.root)
            finally:
                store.close()

    def test_unknown_acceptance_can_preserve_unresolved_evidence(self):
        self.record('acceptance', self.acceptance([self.evidence()], status='unknown'))
        before = self.api.checkpoint(self.root)
        self.assertFalse(before['audit']['ready'])
        self.assertEqual(self.imported_checkpoint(), before)

    def test_false_selected_value_is_resolved_evidence(self):
        self.record('acceptance', self.acceptance([self.evidence(valid=True)]))
        self.record('finish', {'status': 'complete', 'reason': 'observed false', 'next_action': 'report'})
        self.assertTrue(self.imported_checkpoint()['completion_current'])

    def test_later_revision_preserves_historical_completion(self):
        first = case()
        self.store.append(first, None)
        self.record('acceptance', self.acceptance([first.ref.to_dict()]))
        self.record('finish', {'status': 'complete', 'reason': 'accepted', 'next_action': 'report'})
        self.store.append(case(revision=2), 1)
        state = self.imported_checkpoint()
        self.assertEqual(state['status'], 'complete')
        self.assertFalse(state['completion_current'])
