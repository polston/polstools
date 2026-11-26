"""Storage integration tests use disposable external stores, never real history."""
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.errors import EvidenceError, PolicyDenied, RevisionConflict, StoreBusy
from retro_eval.workflow.records import Record, canonical
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.runner import Runner
from retro_eval.workflow.evidence import resolve_evidence, judge_input
from test_eval_workflow_records import case, outcome, TIME, analysis_payload


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Registry(self.root / 'first')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_F03_exact_duplicate_and_F04_conflict(self):
        c = case()
        self.store.append(c, None)
        self.store.append(c, None)
        self.assertEqual(len(self.store.heads()), 1)
        changed = c.to_dict()
        changed['payload']['intent'] = 'Different requested change'
        with self.assertRaises(RevisionConflict):
            self.store.append(Record.from_dict(changed), None)
        self.assertEqual(self.store.get(c.ref), c)

    def test_failed_writable_construction_closes_connection(self):
        connections = []
        connect = sqlite3.connect
        def capture(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connections.append(connection)
            return connection
        with self.store.transaction():
            with patch('retro_eval.workflow.registry.sqlite3.connect', side_effect=capture):
                with self.assertRaises(StoreBusy):
                    Registry(self.root / 'first', timeout=0.01)
        try:
            self.assertEqual(len(connections), 1)
            with self.assertRaisesRegex(sqlite3.ProgrammingError, 'closed database'):
                connections[0].execute('SELECT 1')
        finally:
            for connection in connections:
                connection.close()
        self.store.append(case(), None)
        self.assertEqual(self.store.heads(), [case()])

    def test_F05_stale_update_and_bounded_writer(self):
        self.store.append(case(), None)
        other = Registry(self.root / 'first', timeout=0.01)
        try:
            self.store.append(case(revision=2), 1)
            value = case(revision=2).to_dict()
            value['payload']['intent'] = 'Second writer'
            with self.assertRaises(RevisionConflict):
                other.append(Record.from_dict(value), 1)
            with self.store.transaction():
                with self.assertRaises(StoreBusy):
                    other.append(case(revision=3), 2)
            self.assertEqual(other.heads()[0], case(revision=2))
        finally:
            other.close()

    def test_history_retains_revisions_and_authenticates_superseded_bytes(self):
        first, second = case(), case(revision=2)
        self.store.import_records([first, second])
        self.assertEqual(self.store.heads('case'), [second])
        self.assertEqual(self.store.history('case', first.ref.study_scope), [first, second])
        self.assertEqual(self.store.history('analysis'), [])
        self.assertEqual(self.store.history('case', 'absent'), [])
        self.store.db.execute('UPDATE records SET digest=? WHERE revision=1', ('0' * 64,))
        self.assertEqual(self.store.heads('case'), [second])
        with self.assertRaises(EvidenceError):
            self.store.history('case')

    def test_F06_precommit_failure_preserves_view_and_retry(self):
        self.store.append(case(), None)
        ref = self.store.publish_artifact(b'orphan until a record commits', 'public_synthetic')
        change = Record.create('analysis', 'new', analysis_payload(evidence_refs=[ref]), created_at=TIME)
        original = self.store._link
        def fail_after_link(record):
            original(record)
            raise RuntimeError('simulated process failure before commit')
        with patch.object(self.store, '_link', fail_after_link):
            with self.assertRaises(RuntimeError):
                self.store.append(change, None)
        self.assertIsNone(self.store.get(change.ref))
        self.assertEqual(len(self.store.heads()), 1)
        self.store.append(change, None)
        self.assertEqual(self.store.get(change.ref), change)

    def test_F08_missing_and_F09_corrupt_evidence(self):
        ref = self.store.publish_artifact(b'{"check":"pass"}', 'public_synthetic')
        c = case()
        self.store.append(c, None)
        check = outcome(c, 'build', 'pass', [ref])
        self.store.append(check, None)
        self.store.artifact_path(ref['sha256']).write_bytes(b'{"check":"fail"}')
        self.assertEqual(resolve_evidence(ref, self.store).status, 'invalid')
        self.assertFalse(self.store.applicability([check.ref])['current'])
        self.assertEqual(self.store.get(check.ref), check)
        self.store.artifact_path(ref['sha256']).unlink()
        self.assertEqual(resolve_evidence(ref, self.store).status, 'invalid')

    def test_F10_round_trip_linked_revisions(self):
        artifact = self.store.publish_artifact(b'checked output', 'private_redacted')
        records = []
        for key in ('case-a', 'case-b'):
            for revision in (1, 2):
                c = case(key, revision=revision)
                records.append(c)
            records.append(Record.create('attempt', key + '-attempt', {
                'case_ref': c.ref.to_dict(), 'harness': 'authored',
                'evidence_refs': [artifact]}, created_at=TIME))
        self.store.import_records(reversed(records))
        bundle = self.store.export_bundle()
        other = Registry(self.root / 'second')
        try:
            other.import_bundle(bundle)
            other.import_bundle(bundle)
            self.assertEqual(other.export_bundle(), bundle)
            self.assertEqual(other.id_salt, self.store.id_salt)
        finally:
            other.close()

    def test_F23_F33_privacy_enforced_before_provider(self):
        ref = self.store.publish_artifact(b'redacted private sample', 'private_redacted')
        ref['content_class'] = 'public_synthetic'
        provider = Mock()
        with self.assertRaises(PolicyDenied):
            provider(judge_input(ref, self.store, external=True).data)
        provider.assert_not_called()
        self.assertEqual(judge_input(ref, self.store).content_class, 'private_redacted')
        # Identical bytes published again cannot lower the classification.
        downgraded = self.store.publish_artifact(b'redacted private sample', 'public_synthetic')
        self.assertEqual(downgraded['content_class'], 'private_redacted')

    def test_judge_authority_flags_are_boolean_and_raw_stays_disallowed(self):
        ref = self.store.publish_artifact(b'authored private example', 'private_redacted')
        for name in ('external', 'allow_private_external'):
            for value in ('false', 'true', 0, 1, None, [], {}):
                with self.subTest(name=name, value=value):
                    kwargs = {'external': True, 'allow_private_external': False, name: value}
                    with self.assertRaises(PolicyDenied):
                        judge_input(ref, self.store, **kwargs)
        self.assertEqual(judge_input(ref, self.store, external=True,
            allow_private_external=True).content_class, 'private_redacted')
        public = self.store.publish_artifact(b'authored public example', 'public_synthetic')
        self.assertEqual(judge_input(public, self.store, external=True).content_class,
                         'public_synthetic')
        raw = self.store.publish_artifact(b'authored raw example', 'private_raw')
        with self.assertRaises(PolicyDenied):
            judge_input(raw, self.store, external=True, allow_private_external=True)

    def test_F34_locator_escape(self):
        ref = self.store.publish_artifact(b'original', 'public_synthetic')
        target = self.root / 'outside'
        target.write_bytes(b'original')
        path = self.store.artifact_path(ref['sha256'])
        path.unlink()
        path.symlink_to(target)
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('must not read')):
            self.assertEqual(resolve_evidence(ref, self.store).status, 'policy_denied')

    def test_private_bundle_cannot_self_authorize_external_disclosure(self):
        ref = self.store.publish_artifact(b'authored import privacy probe', 'public_synthetic')
        other = Registry(self.root / 'untrusted-import')
        try:
            other.import_bundle(self.store.export_bundle())
            provider = Mock()
            with self.assertRaises(PolicyDenied):
                provider(judge_input(ref, other, external=True).data)
            provider.assert_not_called()
            self.assertEqual(judge_input(ref, other).content_class, 'private_metadata')
        finally:
            other.close()

    def test_F35_hash_and_selector_use_same_bytes(self):
        ref = self.store.publish_artifact(canonical({'check': 'pass'}), 'public_synthetic')
        ref['selector'] = {'kind': 'json_pointer', 'pointer': '/check'}
        original = self.store.read_artifact
        def change_after_capture(reference, allowed):
            captured = original(reference, allowed)
            self.store.artifact_path(ref['sha256']).write_bytes(canonical({'check': 'fail'}))
            return captured
        with patch.object(self.store, 'read_artifact', change_after_capture):
            self.assertEqual(resolve_evidence(ref, self.store).value, 'pass')
        self.assertEqual(resolve_evidence(ref, self.store).status, 'invalid')

    def test_F37_exact_dependency_revision_and_unrelated_scope(self):
        c = case()
        unrelated = case('unrelated')
        self.store.import_records([c, unrelated])
        check = Record.create('analysis', 'report', analysis_payload(input_refs=[c.ref.to_dict()]), created_at=TIME)
        self.store.append(check, None)
        self.store.append(case(revision=2), 1)
        applicability = self.store.applicability([check.ref])
        self.assertFalse(applicability['current'])
        self.assertEqual(applicability['checked_records'], 2)
        self.assertEqual(applicability['reasons'][0]['reason'], 'superseded_input')
        self.assertTrue(self.store.applicability([unrelated.ref])['current'])
        self.assertEqual(self.store.get(check.ref), check)

    def test_missing_dependency_rolls_back_record(self):
        record = Record.create('attempt', 'attempt', {
            'case_ref': case().ref.to_dict(), 'harness': 'authored'}, created_at=TIME)
        with self.assertRaises(EvidenceError):
            self.store.append(record, None)
        self.assertEqual(self.store.heads(), [])

    def test_attempt_parentage_cycle_rejects_entire_import(self):
        c = case()
        left = {'study_scope': 'default', 'kind': 'attempt', 'key': 'left', 'revision': 1}
        right = {**left, 'key': 'right'}
        attempts = [Record.create('attempt', own['key'], {'case_ref': c.ref.to_dict(),
                    'harness': 'authored', 'parent_attempt_ref': parent}, created_at=TIME)
                    for own, parent in ((left, right), (right, left))]
        with self.assertRaises(EvidenceError):
            self.store.import_records([c, *attempts])
        self.assertEqual(self.store.heads(), [])

    def test_review_failed_import_cannot_upgrade_artifact_classification(self):
        ref = self.store.publish_artifact(b'authored shareable data', 'public_synthetic')
        self.store.append(case(), None)
        bundle = self.store.export_bundle()
        bundle['artifacts'][0]['content_class'] = 'private_raw'
        bundle['records'][0]['payload']['intent'] = 'Conflicting revision'
        with self.assertRaises(RevisionConflict):
            self.store.import_bundle(bundle)
        self.assertEqual(self.store.read_artifact(ref, {'public_synthetic'})[1], 'public_synthetic')
        self.assertEqual(self.store.get(case().ref), case())

    def test_review_round_trip_retains_deadline_uncertain_call_and_planning_state(self):
        runner = Runner(self.store, clock=lambda: 100)
        run = runner.begin({'limits': {'runtime_seconds': 10, 'provider_calls': 1}})['run_id']
        with self.assertRaises(ConnectionError):
            runner.unit(run, 'provider', 'v1', Mock(side_effect=ConnectionError('no receipt')), provider=True)
        with self.store.transaction():
            self.store.db.execute("INSERT INTO metadata VALUES('automatic_state',?)", (canonical({'seen_candidates': ['one']}),))
        bundle = self.store.export_bundle()
        other = Registry(self.root / 'restore')
        try:
            other.import_bundle(bundle)
            self.assertEqual(other.export_bundle(), bundle)
            restored = Runner(other, clock=lambda: 112)
            self.assertEqual(restored.begin({'limits': {'runtime_seconds': 10, 'provider_calls': 1}})['deadline'], 110)
            provider = Mock()
            result = restored.unit(run, 'provider', 'v1', provider, provider=True)
            self.assertEqual(result['charge_status'], 'unknown')
            provider.assert_not_called()
            self.assertEqual(restored.status(run)['used']['provider_calls'], 1)
        finally:
            other.close()

    def test_corrupted_registry_does_not_export_as_valid(self):
        c = case()
        self.store.append(c, None)
        value = c.to_dict()
        value['payload']['intent'] = 'Undeclared mutation'
        with self.store.transaction():
            self.store.db.execute('UPDATE records SET body=?', (canonical(value),))
        with self.assertRaises(EvidenceError):
            self.store.export_bundle()


if __name__ == '__main__':
    unittest.main()
