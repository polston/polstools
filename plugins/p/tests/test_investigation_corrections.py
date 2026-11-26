"""Correction history, recovery and acceptance at actual storage boundaries."""
import json
from pathlib import Path
import tempfile
import unittest

import test_investigation_state as fixtures
from test_investigation_state import frame, action
from retro_eval.investigation import Investigation
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.records import Record, RecordRef
from retro_eval.workflow.errors import WorkflowError, RevisionConflict
from retro_eval.workflow.investigation_records import contract_digest, correction_digest
from retro_eval.workflow.investigation_state import projection_key


class CorrectionTests(unittest.TestCase):
    setUp = fixtures.InvestigationTests.setUp
    tearDown = fixtures.InvestigationTests.tearDown
    record = fixtures.InvestigationTests.record
    artifact = fixtures.InvestigationTests.artifact

    def result(self, wrong='Only B passed', key='inspect'):
        self.record('action', action(key))
        return self.record('result', {'action_id': key, 'status': 'succeeded',
            'usage': {'actions': 1}, 'evidence': [self.artifact(b'{"A":true,"B":true}')],
            'producer': 'agent', 'origin': 'agent', 'observation': wrong})

    def correction(self, target, interpretation='Both A and B passed', **updates):
        return {'contract_sha256': correction_digest(), 'target': target.ref.to_dict(),
            'interpretation': interpretation, 'source': 'reviewer', 'reason': 'Read actual output',
            'evidence': [self.artifact(b'{"A":true,"B":true}')], **updates}

    def accept(self, evidence, key='behavior'):
        return self.record('acceptance', {'criterion': key, 'status': 'pass', 'frame_seq': 0,
            'evidence': evidence, 'inputs': [], 'procedure': 'inspect evidence',
            'verifier': 'reviewer', 'claim_type': 'software', 'limitations': []})

    def test_reopen_preserves_both_mistakes_and_accounting(self):
        originals = []
        for key, wrong, right in [('comparison', 'Only B passed', 'Both A and B passed'),
                                   ('count', 'Five errors, one failure', 'Six errors, no failures')]:
            result = self.result(wrong, key)
            originals.append((result, result.sha256))
            self.record('correction', self.correction(result, right))
        before = self.api.checkpoint(self.root)
        self.store.close()
        self.store = Registry(Path(self.temp.name) / 'store')
        self.api = Investigation(self.store, clock=lambda: self.now)
        recovered = self.api.checkpoint(self.root)
        self.assertEqual(before, recovered)
        self.assertEqual(recovered['grants']['root']['spent'], {'actions': 2})
        self.assertEqual(recovered['grants']['root']['deadline_ms'], 2000)
        for result, sha in originals:
            self.assertEqual(self.store.get(result.ref).sha256, sha)
            act = recovered['actions'][result.payload['data']['action_id']]
            self.assertEqual(act['result'], result.payload['data'])
            self.assertNotEqual(act['current_interpretation']['interpretation'], act['result']['observation'])
            self.assertEqual(len(act['current_interpretation']['history']), 1)
        self.assertFalse(self.api.admit(self.root, 0, action('comparison'))['admitted'])

    def test_dependent_acceptance_stales_and_can_be_revalidated(self):
        result = self.result()
        self.accept([result.ref.to_dict()])
        self.assertTrue(self.api.audit(self.root)['ready'])
        correction = self.record('correction', self.correction(result))
        self.assertFalse(self.api.audit(self.root)['ready'])
        self.assertFalse(self.store.applicability([result.ref])['current'])
        with self.assertRaises(WorkflowError):
            self.accept([result.ref.to_dict()])
        self.accept([correction.ref.to_dict()])
        self.assertTrue(self.api.audit(self.root)['ready'])
        next_correction = self.record('correction', self.correction(correction, 'Evidence insufficient'))
        self.assertFalse(self.api.audit(self.root)['ready'])
        self.assertFalse(self.store.applicability([correction.ref])['current'])
        self.assertTrue(self.store.applicability([next_correction.ref])['current'])

    def test_unrelated_acceptance_and_execution_receipt_remain_valid(self):
        result = self.result()
        artifact = self.artifact(b'independent validation')
        self.accept([artifact])
        self.record('correction', self.correction(result))
        self.assertTrue(self.api.audit(self.root)['ready'])
        self.assertEqual(self.api.checkpoint(self.root)['actions']['inspect']['state'], 'succeeded')

    def test_repeated_competing_and_stale_writes_rejected_atomically(self):
        result = self.result(); d = self.correction(result)
        seq = self.api.checkpoint(self.root)['sequence']
        first = self.record('correction', d)
        after = self.api.checkpoint(self.root)
        with self.assertRaises(RevisionConflict):
            self.api.record(self.root, seq, 'correction', d)
        for data in (d, {**d, 'interpretation': 'competing claim'}):
            with self.assertRaises(WorkflowError):
                self.record('correction', data)
            self.assertEqual(after, self.api.checkpoint(self.root))
        self.record('correction', self.correction(first, 'explicit reconsideration'))
        view = self.api.checkpoint(self.root)['actions']['inspect']['current_interpretation']
        self.assertEqual(len(view['history']), 2)
        self.assertEqual(view['original_ref'], result.ref.to_dict())

    def test_invalid_targets_evidence_and_accounting_edits_rejected(self):
        result = self.result()
        other = self.api.begin('other', frame(), deadline_ms=2000, limits={}, authority=[],
            capabilities={'execution': {'mode': 'advisory', 'scope': 'fixture'}})
        missing = {**result.ref.to_dict(), 'key': 'missing'}
        data = self.correction(result)
        bad_selector = {**self.artifact(b'{}'), 'selector': {'kind': 'json_pointer', 'pointer': '/absent'}}
        attempts = [dict(target=missing), dict(target=other.to_dict()), dict(target=[]),
                    dict(evidence=[]), dict(evidence=[bad_selector]),
                    dict(contract_sha256='0' * 64), dict(usage={'actions': 0}),
                    dict(status='failed'), dict(source='')]
        before = self.api.checkpoint(self.root)
        for update in attempts:
            with self.subTest(update=update), self.assertRaises((WorkflowError, ValueError, TypeError)):
                self.record('correction', {**data, **update})
            self.assertEqual(before, self.api.checkpoint(self.root))
        # A real result in another root is still not a legal target.
        self.api.record(other, 0, 'action', action(reservation={}, authority=[]))
        foreign = self.api.record(other, 1, 'result', {'action_id': 'inspect','status':'succeeded',
            'usage':{},'evidence':[self.artifact()],'producer':'agent','origin':'agent','observation':'foreign'})
        with self.assertRaises(WorkflowError):
            self.record('correction', self.correction(foreign))

    def test_closed_and_past_bound_correction_never_reopens_execution(self):
        result = self.result(); self.accept([result.ref.to_dict()])
        self.record('finish', {'status':'complete','reason':'accepted','next_action':'handoff'})
        self.now = 3000
        before = self.api.checkpoint(self.root)
        self.record('correction', self.correction(result))
        after = self.api.checkpoint(self.root)
        self.assertEqual(after['status'], 'complete')
        self.assertFalse(after['completion_current'])
        self.assertFalse(after['audit']['ready'])
        self.assertEqual(after['grants'], before['grants'])
        with self.assertRaises(WorkflowError):
            self.record('action', action('more'))

    def test_export_import_rebuilds_corrections_and_invalidates_transitive_claim(self):
        result = self.result()
        decision = self.record('decision', {'id':'dependent','disposition':'select','reason':'result',
            'evidence':[result.ref.to_dict()],'limitations':[],'next_action':'accept','revisit':'new evidence'})
        self.accept([decision.ref.to_dict()])
        self.record('correction', self.correction(result))
        bundle = self.store.export_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            other = Registry(Path(tmp)/'copy')
            try:
                other.import_bundle(bundle)
                api = Investigation(other, clock=lambda:self.now)
                self.assertEqual(api.checkpoint(self.root), self.api.checkpoint(self.root))
                self.assertFalse(api.audit(self.root)['ready'])
                other.db.execute('UPDATE metadata SET value=? WHERE key=?',
                    ('{}', projection_key(self.root)))
                other.db.commit()
                self.assertEqual(api.checkpoint(self.root), self.api.checkpoint(self.root))
            finally: other.close()

    def test_legacy_policy_digest_unchanged_and_existing_root_continues(self):
        # Pinned pre-extension digest: adding correction support cannot silently
        # change the contract of an already recorded base-v1 root.
        original = json.loads((Path(__file__).resolve().parents[1]/'skills/investigating-and-improving/contract-v1.json').read_text())
        from retro_eval.workflow.records import canonical, digest
        self.assertEqual(contract_digest(), '9e1cd4297ec5c74bff826321a52ccd8dc6073ed18a5ee5da6a6b01b7f5450fed')
        self.assertEqual(contract_digest(), digest(canonical(original)))
        self.assertNotIn('correction', original['events'])
        self.assertEqual(self.store.get(self.root).payload['policy_sha256'], contract_digest())
        result = self.result()
        self.record('correction', self.correction(result))
        self.assertEqual(self.store.get(self.root).payload['policy_sha256'], contract_digest())

    def test_support_cannot_rely_on_the_claim_being_corrected(self):
        result = self.result()
        decision = self.record('decision', {'id':'dependent','disposition':'select','reason':'result',
            'evidence':[result.ref.to_dict()],'limitations':[],'next_action':'accept','revisit':'new evidence'})
        for ref in (result.ref, decision.ref):
            with self.assertRaises(WorkflowError):
                self.record('correction', self.correction(result, evidence=[ref.to_dict()]))

    def make_event(self, typ, data):
        state = self.api.checkpoint(self.root)
        return Record.create('investigation_event',
            projection_key(self.root).rsplit(':',1)[1]+':'+str(state['sequence']+1),
            {'version':1,'root_ref':self.root.to_dict(),'sequence':state['sequence']+1,
             'previous_sha256':state['tip_sha256'],'at_ms':self.now,'event_type':typ,'data':data},
            study_scope=self.root.study_scope, producer='test',producer_version='1')

    def test_generic_import_cannot_bypass_target_or_selector_validation(self):
        result = self.result()
        invalid_selector = {**self.artifact(b'{}'), 'selector':{'kind':'json_pointer','pointer':'/absent'}}
        event = self.make_event('correction', self.correction(result, evidence=[invalid_selector]))
        before = self.api.checkpoint(self.root)
        with self.assertRaises(WorkflowError):
            self.store.import_records([event])
        self.assertEqual(before,self.api.checkpoint(self.root))
        good = self.record('correction', self.correction(result))
        competing = self.make_event('correction', self.correction(result, 'competing'))
        with self.assertRaises(WorkflowError):
            self.store.import_records([competing])
        # Nor can import assert passing acceptance of an already corrected claim.
        obsolete = self.make_event('acceptance', {'criterion':'behavior','status':'pass','frame_seq':0,
            'evidence':[result.ref.to_dict()],'inputs':[],'procedure':'obsolete',
            'verifier':'test','claim_type':'software','limitations':[]})
        with self.assertRaises(WorkflowError):
            self.store.import_records([obsolete])
        self.assertTrue(self.store.applicability([good.ref])['current'])

    def test_generic_append_rejects_self_support(self):
        result = self.result()
        d = self.correction(result)
        event = self.make_event('correction', d)
        d['evidence'] = [event.ref.to_dict()]
        event = self.make_event('correction', d)
        with self.assertRaises(WorkflowError):
            self.store.append(event, None)

    def test_historical_completion_imports_but_is_no_longer_current(self):
        result = self.result(); self.accept([result.ref.to_dict()])
        self.record('finish', {'status':'complete','reason':'then accepted','next_action':'report'})
        self.record('correction', self.correction(result))
        with tempfile.TemporaryDirectory() as tmp:
            store = Registry(Path(tmp)/'import')
            try:
                store.import_bundle(self.store.export_bundle())
                state = Investigation(store,clock=lambda:self.now).checkpoint(self.root)
                self.assertEqual(state['status'],'complete')
                self.assertFalse(state['completion_current'])
                self.assertFalse(state['audit']['ready'])
            finally: store.close()

    def _completion_dependency(self, scope):
        result = self.result()
        if scope == 'frame':
            changed = self.api.checkpoint(self.root)['frame']
            changed['inputs'] = [result.ref.to_dict()]
            self.record('frame', {'frame': changed, 'basis': [self.artifact()],
                                  'source': 'requester'})
        else:
            self.record('question', fixtures.InvestigationTests.question(self))
            self.record('answer', {'question_id': 'preference', 'frame_seq': 0,
                'response': 'direct', 'respondent': 'requester', 'origin': 'requester',
                'evidence': [result.ref.to_dict()]})
        self.record('acceptance', {'criterion': 'behavior', 'status': 'pass',
            'frame_seq': self.api.checkpoint(self.root)['frame_seq'],
            'evidence': [self.artifact(b'independent check')], 'inputs': [],
            'procedure': 'check', 'verifier': 'reviewer', 'claim_type': 'software',
            'limitations': []})
        return result

    def _assert_completion_cannot_follow_corrected_dependency(self, scope):
        result = self._completion_dependency(scope)
        self.record('correction', self.correction(result))
        finish = {'status': 'complete', 'reason': 'accepted', 'next_action': 'report'}
        before = self.api.checkpoint(self.root)
        with self.assertRaises(WorkflowError):
            self.record('finish', finish)
        with self.assertRaises(WorkflowError):
            self.store.import_records([self.make_event('finish', finish)])
        self.assertEqual(self.api.checkpoint(self.root), before)

    def test_import_completion_rejects_previously_corrected_frame_input(self):
        self._assert_completion_cannot_follow_corrected_dependency('frame')

    def test_import_completion_rejects_previously_corrected_required_answer(self):
        self._assert_completion_cannot_follow_corrected_dependency('question')

    def _assert_completion_before_dependency_correction_survives(self, scope):
        result = self._completion_dependency(scope)
        self.record('finish', {'status': 'complete', 'reason': 'then accepted',
                               'next_action': 'report'})
        self.record('correction', self.correction(result))
        with tempfile.TemporaryDirectory() as tmp:
            store = Registry(Path(tmp) / 'import')
            try:
                store.import_bundle(self.store.export_bundle())
                state = Investigation(store, clock=lambda: self.now).checkpoint(self.root)
                self.assertEqual(state['status'], 'complete')
                self.assertFalse(state['completion_current'])
                self.assertFalse(state['audit']['ready'])
            finally:
                store.close()

    def test_completion_before_frame_correction_retains_history(self):
        self._assert_completion_before_dependency_correction_survives('frame')

    def test_completion_before_answer_correction_retains_history(self):
        self._assert_completion_before_dependency_correction_survives('question')

    def _dependent_event(self, scope, result):
        if scope == 'resume':
            self.record('finish', {'status': 'stopped', 'reason': 'requester pause',
                                   'next_action': 'await direction'})
            return 'resume', {'source': 'requester', 'reason': 'continue',
                              'evidence': [result.ref.to_dict()]}
        answer = {'question_id': 'preference', 'frame_seq': 0, 'response': 'direct',
                  'respondent': 'requester', 'origin': 'requester',
                  'evidence': [result.ref.to_dict()]}
        if scope in ('answer', 'action-answer'):
            self.record('question', fixtures.InvestigationTests.question(self))
        if scope == 'answer':
            return 'answer', answer
        if scope == 'action-answer':
            self.record('answer', answer)
        return 'action', action('next',
            inputs=[result.ref.to_dict()] if scope == 'action-input' else [],
            questions=['preference'] if scope == 'action-answer' else [])

    def _assert_known_correction_rejects_dependency(self, scope):
        result = self.result()
        typ, data = self._dependent_event(scope, result)
        self.record('correction', self.correction(result))
        before = self.api.checkpoint(self.root)
        with self.assertRaises(WorkflowError):
            self.record(typ, data)
        with self.assertRaises(WorkflowError):
            self.store.import_records([self.make_event(typ, data)])
        self.assertEqual(self.api.checkpoint(self.root), before)

    def test_import_action_rejects_corrected_input(self):
        self._assert_known_correction_rejects_dependency('action-input')

    def test_import_action_rejects_corrected_answer(self):
        self._assert_known_correction_rejects_dependency('action-answer')

    def test_import_answer_rejects_corrected_source(self):
        self._assert_known_correction_rejects_dependency('answer')

    def test_import_resume_rejects_corrected_source(self):
        self._assert_known_correction_rejects_dependency('resume')

    def test_later_correction_preserves_prior_admission_answer_and_resume(self):
        for scope in ('action-input', 'action-answer', 'answer', 'resume'):
            with self.subTest(scope=scope):
                fixture = CorrectionTests()
                fixture.setUp()
                try:
                    result = fixture.result()
                    typ, data = fixture._dependent_event(scope, result)
                    fixture.record(typ, data)
                    fixture.record('correction', fixture.correction(result))
                    before = fixture.api.checkpoint(fixture.root)
                    with tempfile.TemporaryDirectory() as tmp:
                        store = Registry(Path(tmp) / 'import')
                        try:
                            store.import_bundle(fixture.store.export_bundle())
                            after = Investigation(store, clock=lambda: fixture.now).checkpoint(fixture.root)
                            self.assertEqual(after, before)
                        finally:
                            store.close()
                finally:
                    fixture.tearDown()

    def test_later_settlement_keeps_its_own_interpretation(self):
        self.record('action', action())
        old = self.record('result', {'action_id':'inspect','status':'uncertain','usage':{'actions':0},
            'evidence':[],'producer':'agent','origin':'agent','observation':'No result seen'})
        self.record('correction',self.correction(old,'Partial result seen'))
        new = self.record('result', {'action_id':'inspect','status':'succeeded','usage':{'actions':1},
            'evidence':[self.artifact()],'producer':'tool','origin':'helper','observation':'Final result'})
        prior = self.api.checkpoint(self.root)['interpretations'][old.ref.key]['current_ref']
        self.record('correction', self.correction(self.store.get(RecordRef.from_dict(prior)),
                                                'Historical partial result clarified'))
        state = self.api.checkpoint(self.root)
        self.assertEqual(state['actions']['inspect']['current_interpretation']['current_ref'],new.ref.to_dict())
        self.assertEqual(state['actions']['inspect']['state'],'succeeded')
