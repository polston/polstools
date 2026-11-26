"""Authored failures at public state/storage boundaries, not behavioral claims."""
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.investigation import Investigation
from retro_eval.workflow.errors import WorkflowError, RevisionConflict
from retro_eval.workflow.records import Record, canonical
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.investigation_state import projection_key
from test_eval_workflow_records import case


def frame():
    return {'subject': 'authored artifact', 'objective': 'Verify the artifact',
            'requester': 'requester', 'constraints': ['preserve behavior'],
            'preferences': [], 'inputs': [], 'criteria': [
                {'key': 'behavior', 'description': 'artifact behaves correctly',
                 'claim_type': 'software', 'verification_method': 'executable check', 'required': True}]}


def action(key='inspect', **updates):
    return {'id': key, 'grant': 'root', 'kind': 'inspect', 'method': 'source inspection',
            'purpose': 'distinguish explanations', 'prediction': 'source shows mechanism',
            'falsifier': 'mechanism absent', 'alternatives': ['different mechanism'],
            'authority': ['read'], 'reservation': {'actions': 1}, 'questions': [],
            'inputs': [], **updates}


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Registry(Path(self.temp.name) / 'store')
        self.now = 1000
        self.api = Investigation(self.store, clock=lambda: self.now)
        self.root = self.api.begin('authored', frame(), deadline_ms=2000,
            limits={'actions': 10}, authority=['read'], capabilities={
                'deadline': {'mode': 'helper_enforced', 'scope': 'admission only'}})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def record(self, typ, data):
        return self.api.record(self.root, self.api.checkpoint(self.root)['sequence'], typ, data)

    def artifact(self, data=b'authored execution observation'):
        return self.store.publish_artifact(data, 'public_synthetic')

    def test_backup_precedes_first_write_and_survives_reopen(self):
        receipt = json.loads(self.store.db.execute("SELECT value FROM metadata WHERE key='investigation:migration'").fetchone()[0])
        backup = sqlite3.connect(self.store.root / 'backups' / receipt['backup'])
        try:
            self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(backup.execute('SELECT count(*) FROM records').fetchone()[0], 0)
        finally:
            backup.close()
        self.store.close()
        self.store = Registry(Path(self.temp.name) / 'store')
        self.api = Investigation(self.store, clock=lambda: self.now)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 2)
        self.assertEqual(self.api.checkpoint(self.root)['sequence'], 0)

    def test_failed_initial_publication_rolls_back_format_and_records(self):
        with RegistryContext(Path(self.temp.name) / 'rollback') as store:
            api = Investigation(store, clock=lambda: self.now)
            with patch.object(store, '_link', side_effect=RuntimeError('interrupted')):
                with self.assertRaises(RuntimeError):
                    api.begin('new', frame(), deadline_ms=2000, limits={}, authority=[],
                              capabilities={'deadline': {'mode': 'advisory', 'scope': 'native work'}})
            self.assertEqual(store.db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(store.heads(), [])
            self.assertTrue(list((store.root / 'backups').glob('*.sqlite')))

    def test_grant_exact_limit_and_child_settlement(self):
        self.record('grant', {'id': 'child', 'parent': 'root', 'recipient': 'worker',
            'limits': {'actions': 10}, 'authority': ['read'], 'deadline_ms': 1900})
        with self.assertRaises(WorkflowError):
            self.record('action', action())
        self.record('action', action(grant='child'))
        self.record('result', {'action_id': 'inspect', 'status': 'succeeded',
            'usage': {'actions': 1}, 'evidence': [self.artifact()], 'producer': 'authored tool',
            'origin': 'helper', 'observation': 'read finished'})
        self.record('grant_close', {'id': 'child', 'reason': 'work settled'})
        g = self.api.checkpoint(self.root)['grants']['root']
        self.assertEqual(g['held']['actions'], 0)
        self.assertEqual(g['spent']['actions'], 1)

    def test_sequence_conflict_and_idempotent_admission(self):
        self.assertTrue(self.api.admit(self.root, 0, action())['admitted'])
        self.assertFalse(self.api.admit(self.root, 0, action())['admitted'])
        with self.assertRaises(RevisionConflict):
            self.api.admit(self.root, 0, action(purpose='different work'))
        with self.assertRaises(RevisionConflict):
            self.api.record(self.root, 0, 'action', action('another'))
        self.assertEqual(self.api.checkpoint(self.root)['grants']['root']['held'], {'actions': 1})

    def test_uncertain_result_retains_allocation_and_usage_never_disappears(self):
        self.record('action', action(reservation={'actions': 10}))
        result = {'action_id': 'inspect', 'status': 'uncertain', 'usage': {'actions': 5},
                  'evidence': [], 'producer': 'harness', 'origin': 'harness', 'observation': 'connection lost'}
        self.record('result', result)
        with self.assertRaises(WorkflowError):
            self.record('action', action('another'))
        with self.assertRaises(WorkflowError):
            self.record('result', {**result, 'usage': {'actions': 4}})
        self.assertEqual(self.api.checkpoint(self.root)['grants']['root']['held'], {'actions': 10})

    def test_deadline_equality_and_recovery_cannot_renew(self):
        self.now = 2000
        with self.assertRaises(WorkflowError):
            self.record('action', action())
        self.assertEqual(self.api.checkpoint(self.root)['remaining_ms'], 0)
        self.record('finish', {'status': 'bound', 'reason': 'deadline reached', 'next_action': 'report missing evidence'})
        self.assertFalse(self.api.audit(self.root)['ready'])

    def test_negative_boolean_and_unknown_fields_rejected(self):
        for invalid in (-1, True, 0.5):
            with self.subTest(invalid=invalid), self.assertRaises(WorkflowError):
                self.record('action', action(reservation={'actions': invalid}))
        with self.assertRaises(WorkflowError):
            self.record('action', {**action(), 'pretend_permission': True})
        self.assertEqual(self.api.checkpoint(self.root)['sequence'], 0)

    def test_new_progress_preserves_history_but_input_change_stales_claim(self):
        original = case()
        self.store.append(original, None)
        self.record('acceptance', {'criterion': 'behavior', 'status': 'pass', 'frame_seq': 0,
            'evidence': [self.artifact()], 'inputs': [original.ref.to_dict()],
            'procedure': 'authored check', 'verifier': 'authored test', 'claim_type': 'software', 'limitations': []})
        self.assertTrue(self.api.audit(self.root)['ready'])
        self.record('research', {'question': 'any hidden regression?', 'sources': [],
            'alternatives': ['unchanged behavior'], 'counterevidence': [],
            'finding': 'additional verification remains', 'next_action': 'probe boundary'})
        self.assertTrue(self.api.audit(self.root)['ready'])
        self.store.append(case(revision=2), 1)
        checkpoint = self.api.checkpoint(self.root)
        self.assertEqual(checkpoint['sequence'], 2)
        self.assertFalse(checkpoint['audit']['ready'])
        self.assertIsNotNone(self.store.get(original.ref))

    def test_complete_requires_observed_acceptance_not_finished_action(self):
        with self.assertRaises(WorkflowError):
            self.record('finish', {'status': 'complete', 'reason': 'looks good', 'next_action': 'report'})
        self.record('acceptance', {'criterion': 'behavior', 'status': 'pass', 'frame_seq': 0,
            'evidence': [self.artifact()], 'inputs': [], 'procedure': 'authored check',
            'verifier': 'authored test', 'claim_type': 'software', 'limitations': []})
        self.record('finish', {'status': 'complete', 'reason': 'criterion checked', 'next_action': 'report'})
        self.assertEqual(self.api.checkpoint(self.root)['status'], 'complete')

    def test_import_rebuilds_tampered_projection(self):
        self.record('action', action())
        bundle = self.store.export_bundle()
        self.assertEqual(bundle['bundle_format'], 2)
        import base64
        for row in bundle['metadata']:
            if row['key'] == projection_key(self.root):
                row['value_base64'] = base64.b64encode(b'{"sequence":999,"status":"complete"}').decode()
        with RegistryContext(Path(self.temp.name) / 'imported') as other:
            other.import_bundle(bundle)
            state = Investigation(other, clock=lambda: self.now).checkpoint(self.root)
            self.assertEqual(state['sequence'], 1)
            self.assertEqual(state['status'], 'active')
            self.assertEqual(state['grants']['root']['held'], {'actions': 1})

    def test_import_rejects_broken_chain_atomically(self):
        self.record('action', action())
        bundle = self.store.export_bundle()
        for row in bundle['records']:
            if row['kind'] == 'investigation_event':
                row['payload']['sequence'] = 2
                row['payload']['previous_sha256'] = '0' * 64
        with RegistryContext(Path(self.temp.name) / 'bad-import') as other:
            with self.assertRaises(WorkflowError):
                other.import_bundle(bundle)
            self.assertEqual(other.heads(), [])
            self.assertEqual(other.db.execute('PRAGMA user_version').fetchone()[0], 1)

    def test_immutable_root_and_multibyte_identity(self):
        root = self.store.get(self.root).to_dict()
        root.update(revision=2, supersedes_revision=1)
        with self.assertRaises(WorkflowError):
            Record.from_dict(root)
        self.record('decision', {'id': 'résumé', 'disposition': 'unresolved',
            'reason': 'missing evidence', 'evidence': [], 'limitations': [],
            'next_action': 'inspect', 'revisit': 'new evidence'})
        self.assertIn('résumé', self.api.checkpoint(self.root)['decisions'])
        self.assertGreater(len(canonical({'text': 'é'})), len(canonical({'text': 'e'})))

    def question(self, **patches):
        return {'id': 'preference', 'topic': 'notice tone', 'recipient': 'requester',
            'category': 'taste', 'question': 'Direct or warm?', 'impact': 'select final wording',
            'attempts': ['verified both preserve required facts'],
            'alternatives': ['direct notice', 'warm notice'], 'exemption': '',
            'frame_seq': 0, 'required': True, **patches}

    def test_question_dependencies_and_independent_work(self):
        self.record('question', self.question())
        with self.assertRaises(WorkflowError):
            self.record('action', action(questions=['preference']))
        self.record('action', action('independent'))
        with self.assertRaises(WorkflowError):
            self.record('question', self.question(id='duplicate', topic=' Notice   TONE '))
        answer = {'question_id': 'preference', 'frame_seq': 0,
            'response': 'direct', 'respondent': 'requester', 'origin': 'simulated',
            'evidence': [self.artifact(b'authored requester response fixture')]}
        with self.assertRaises(WorkflowError):
            self.record('answer', answer)
        self.record('answer', {**answer, 'origin': 'requester'})
        self.record('action', action('dependent', questions=['preference']))

    def test_late_answer_requires_reconciliation_and_no_permission_expansion(self):
        self.record('question', self.question())
        new_frame = frame()
        new_frame['subject'] = 'changed artifact'
        self.record('frame', {'frame': new_frame, 'basis': [self.artifact()], 'source': 'requester'})
        with self.assertRaises(WorkflowError):
            self.record('answer', {'question_id': 'preference', 'frame_seq': 0,
                'response': 'direct', 'respondent': 'requester', 'origin': 'requester',
                'evidence': [self.artifact()]})
        with self.assertRaises(WorkflowError):
            self.record('action', action(authority=['publish']))
        with self.assertRaises(WorkflowError):
            self.record('grant', {'id': 'expanded', 'parent': 'root', 'recipient': 'worker',
                'limits': {'actions': 2}, 'authority': ['publish'], 'deadline_ms': 1900})

    def test_concurrent_writers_cannot_both_spend_same_allowance(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)
        path = self.store.root
        def run(key):
            with RegistryContext(path) as store:
                api = Investigation(store, clock=lambda: 1000)
                seq = api.checkpoint(self.root)['sequence']
                barrier.wait()
                try:
                    api.admit(self.root, seq, action(key, reservation={'actions': 7}))
                    return True
                except WorkflowError:
                    return False
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(run, ['first', 'second']))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.api.checkpoint(self.root)['grants']['root']['held'], {'actions': 7})

    def test_overrun_is_observed_and_blocks_new_work(self):
        self.record('action', action())
        self.record('result', {'action_id': 'inspect', 'status': 'failed',
            'usage': {'actions': 2}, 'evidence': [self.artifact()], 'producer': 'authored tool',
            'origin': 'helper', 'observation': 'observed overrun'})
        state = self.api.checkpoint(self.root)
        self.assertEqual(state['grants']['root']['spent']['actions'], 2)
        self.assertTrue(state['overruns'])
        with self.assertRaises(WorkflowError):
            self.record('action', action('retry'))

    def test_cross_scope_event_cannot_escape_replay(self):
        event = self.record('action', action()).to_dict()
        event['study_scope'] = 'unrelated'
        with self.assertRaises(WorkflowError):
            self.store.append(Record.from_dict(event), None)

    def test_membership_does_not_inherit_staleness_from_originating_frame(self):
        old = case()
        self.store.append(old, None)
        f = frame()
        f['inputs'] = [old.ref.to_dict()]
        root = self.api.begin('subject-versions', f, deadline_ms=2000, limits={}, authority=[],
            capabilities={'deadline': {'mode': 'advisory', 'scope': 'native work'}})
        self.store.append(case(revision=2), 1)
        event = self.api.record(root, 0, 'research', {'question': 'new subject state?',
            'sources': [case(revision=2).ref.to_dict()], 'alternatives': [],
            'counterevidence': [], 'finding': 'new input available', 'next_action': 'reframe'})
        self.assertTrue(self.store.applicability([event.ref])['current'])
        self.assertFalse(self.store.applicability([root])['current'])

    def test_historical_membership_still_checks_artifact_integrity(self):
        ref = self.artifact()
        f = frame()
        f['inputs'] = [ref]
        root = self.api.begin('integrity', f, deadline_ms=2000, limits={}, authority=[],
            capabilities={'deadline': {'mode': 'advisory', 'scope': 'native work'}})
        event = self.api.record(root, 0, 'research', {'question': 'state?', 'sources': [],
            'alternatives': [], 'counterevidence': [], 'finding': 'checked', 'next_action': 'probe'})
        self.store.artifact_path(ref['sha256']).write_bytes(b'changed')
        self.assertFalse(self.store.applicability([event.ref])['current'])

    def test_missing_selected_target_cannot_satisfy_acceptance(self):
        ref = self.artifact(b'{"present":1}')
        ref['selector'] = {'kind': 'json_pointer', 'pointer': '/absent'}
        acceptance = {'criterion': 'behavior', 'status': 'pass', 'frame_seq': 0,
            'evidence': [ref], 'inputs': [], 'procedure': 'selected value check',
            'verifier': 'authored oracle', 'claim_type': 'software', 'limitations': []}
        with self.assertRaises(WorkflowError):
            self.record('acceptance', acceptance)
        ref['selector']['pointer'] = '/present'
        self.record('acceptance', acceptance)
        self.assertTrue(self.api.audit(self.root)['ready'])

    def test_child_cannot_omit_enforced_action_dimension(self):
        self.record('grant', {'id': 'empty', 'parent': 'root', 'recipient': 'worker',
            'limits': {}, 'authority': ['read'], 'deadline_ms': 1900})
        with self.assertRaises(WorkflowError):
            self.record('action', action(grant='empty', reservation={}))
        with self.assertRaises(WorkflowError):
            self.record('action', action(grant='empty'))

    def test_bound_handoff_allows_late_settlement_without_dispatch(self):
        self.record('grant', {'id': 'child', 'parent': 'root', 'recipient': 'worker',
            'limits': {'actions': 2}, 'authority': ['read'], 'deadline_ms': 1900})
        self.record('action', action(grant='child'))
        self.now = 2000
        self.record('finish', {'status': 'bound', 'reason': 'deadline', 'next_action': 'reconcile receipt'})
        self.record('result', {'action_id': 'inspect', 'status': 'succeeded',
            'usage': {'actions': 1}, 'evidence': [self.artifact()], 'producer': 'late native receipt',
            'origin': 'harness', 'observation': 'previously dispatched work finished'})
        self.record('grant_close', {'id': 'child', 'reason': 'receipt reconciled'})
        with self.assertRaises(WorkflowError):
            self.record('action', action('new'))
        state = self.api.checkpoint(self.root)
        self.assertEqual(state['status'], 'bound')
        self.assertEqual(state['grants']['root']['spent']['actions'], 1)

    def test_current_frame_inputs_invalidate_acceptance_without_duplication(self):
        old = case()
        self.store.append(old, None)
        f = frame()
        f['inputs'] = [old.ref.to_dict()]
        self.record('frame', {'frame': f, 'basis': [self.artifact()], 'source': 'requester'})
        self.record('acceptance', {'criterion': 'behavior', 'status': 'pass', 'frame_seq': 1,
            'evidence': [self.artifact()], 'inputs': [], 'procedure': 'authored check',
            'verifier': 'test', 'claim_type': 'software', 'limitations': []})
        self.assertTrue(self.api.audit(self.root)['ready'])
        self.store.append(case(revision=2), 1)
        self.assertFalse(self.api.audit(self.root)['ready'])
        with self.assertRaises(WorkflowError):
            self.record('finish', {'status': 'complete', 'reason': 'done', 'next_action': 'report'})

    def test_record_mediated_selector_cannot_make_false_acceptance(self):
        ref = self.artifact(b'{"present":1}')
        ref['selector'] = {'kind': 'json_pointer', 'pointer': '/absent'}
        try:
            event = self.record('research', {'question': 'selected target?', 'sources': [ref],
                'alternatives': [], 'counterevidence': [], 'finding': 'claimed observation', 'next_action': 'verify'})
        except WorkflowError:
            return  # Rejecting the invalid source at publication is also valid.
        with self.assertRaises(WorkflowError):
            self.record('acceptance', {'criterion': 'behavior', 'status': 'pass', 'frame_seq': 0,
                'evidence': [event.ref.to_dict()], 'inputs': [], 'procedure': 'selected check',
                'verifier': 'test', 'claim_type': 'software', 'limitations': []})

    def test_material_scope_cannot_be_reframed_by_agent(self):
        for field, value in [('subject', 'different subject'), ('constraints', []),
                             ('preferences', ['ignore requested tone'])]:
            changed = frame()
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(WorkflowError):
                self.record('frame', {'frame': changed, 'basis': [self.artifact()], 'source': 'agent'})

    def test_blocked_resume_keeps_budget_deadline_and_pending_question(self):
        self.record('question', self.question())
        self.record('action', action('independent'))
        self.record('result', {'action_id': 'independent', 'status': 'succeeded',
            'usage': {'actions': 1}, 'evidence': [self.artifact()], 'producer': 'tool',
            'origin': 'harness', 'observation': 'independent work finished'})
        self.record('finish', {'status': 'blocked', 'reason': 'await requester preference', 'next_action': 'resume on answer'})
        before = self.api.checkpoint(self.root)
        with self.assertRaises(WorkflowError):
            self.record('resume', {'source': 'agent', 'reason': 'guess', 'evidence': [self.artifact()]})
        self.record('resume', {'source': 'requester', 'reason': 'requested continuation', 'evidence': [self.artifact()]})
        after = self.api.checkpoint(self.root)
        self.assertEqual(before['grants'], after['grants'])
        self.assertEqual(after['questions']['preference']['state'], 'pending')
        self.record('answer', {'question_id': 'preference', 'frame_seq': 0,
            'response': 'direct', 'respondent': 'requester', 'origin': 'requester', 'evidence': [self.artifact()]})
        self.record('action', action('dependent', questions=['preference']))
        self.record('finish', {'status': 'stopped', 'reason': 'requester stopped', 'next_action': 'reconcile'})
        self.now = 2000
        with self.assertRaises(WorkflowError):
            self.record('resume', {'source': 'requester', 'reason': 'too late', 'evidence': [self.artifact()]})

    def test_answer_requires_resolvable_and_still_current_evidence(self):
        self.record('question', self.question())
        ref = self.artifact(b'{}')
        ref['selector'] = {'kind': 'json_pointer', 'pointer': '/missing'}
        answer = {'question_id': 'preference', 'frame_seq': 0, 'response': 'direct',
            'respondent': 'requester', 'origin': 'requester', 'evidence': [ref]}
        with self.assertRaises(WorkflowError):
            self.record('answer', answer)
        old = case()
        self.store.append(old, None)
        self.record('answer', {**answer, 'evidence': [old.ref.to_dict()]})
        self.store.append(case(revision=2), 1)
        with self.assertRaises(WorkflowError):
            self.record('action', action(questions=['preference']))
        self.assertIn('preference', self.api.audit(self.root)['pending_questions'])

    def test_result_claim_inherits_action_input_versions(self):
        old = case()
        self.store.append(old, None)
        self.record('action', action(inputs=[old.ref.to_dict()]))
        result = self.record('result', {'action_id': 'inspect', 'status': 'succeeded',
            'usage': {'actions': 1}, 'evidence': [self.artifact()], 'producer': 'tool',
            'origin': 'harness', 'observation': 'checked input'})
        self.record('acceptance', {'criterion': 'behavior', 'status': 'pass', 'frame_seq': 0,
            'evidence': [result.ref.to_dict()], 'inputs': [], 'procedure': 'result check',
            'verifier': 'test', 'claim_type': 'software', 'limitations': []})
        self.assertTrue(self.api.audit(self.root)['ready'])
        self.store.append(case(revision=2), 1)
        self.assertFalse(self.api.audit(self.root)['ready'])
        self.assertEqual(self.api.checkpoint(self.root)['sequence'], 3)

    def test_nonobject_span_rows_are_invalid_evidence_not_runtime_errors(self):
        ref = self.artifact(b'null\n{"span_id":"authored"}\n')
        ref['selector'] = {'kind': 'normalized_span', 'span_id': 'authored'}
        with self.assertRaises(WorkflowError):
            self.record('acceptance', {'criterion': 'behavior', 'status': 'pass', 'frame_seq': 0,
                'evidence': [ref], 'inputs': [], 'procedure': 'span observation',
                'verifier': 'test', 'claim_type': 'software', 'limitations': []})


class RegistryContext:
    def __init__(self, path):
        self.store = Registry(path)

    def __enter__(self):
        return self.store

    def __exit__(self, *args):
        self.store.close()
