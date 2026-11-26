"""Replay labels and discovery exposure stay bound to the displayed evidence."""
import copy
import shutil
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.errors import WorkflowError
from retro_eval.workflow.inspection import inspect_run
from retro_eval.workflow.pilots import compare, prepare
from retro_eval.workflow.records import Record, RecordRef, canonical, strict_loads
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.runner import Runner
from retro_eval.workflow.service import run_automatic
from test_eval_workflow_records import TIME
from test_eval_workflow_sources import jsonl


def source(root, name='sample', *, plain=False):
    path = root / name / '.system_generated/logs/transcript_full.jsonl'
    path.parent.mkdir(parents=True)
    rows = [{'step_index': 0, 'type': 'USER_INPUT', 'source': 'USER_EXPLICIT',
             'created_at': TIME, 'content': 'Start the task.'}]
    if not plain:
        rows += [
            {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'source': 'MODEL',
             'created_at': TIME, 'content': 'An authored context sentence. ' * 20},
            {'step_index': 2, 'type': 'USER_INPUT', 'source': 'USER_EXPLICIT', 'created_at': TIME,
             'content': '<USER_REQUEST>What time is it?</USER_REQUEST><ADDITIONAL_METADATA>info</ADDITIONAL_METADATA>'}]
    path.write_bytes(jsonl(rows))


class PilotIntegrityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        source(self.root / 'sources')
        run_automatic(self.root / 'store', roots={'antigravity': self.root / 'sources'}, captured_at=TIME)
        self.store = Registry(self.root / 'store')
        self.addCleanup(self.store.close)
        self.registration = prepare(self.store, created_at=TIME)
        self.ref = RecordRef.from_dict(self.registration['pilot_ref'])
        self.labels = strict_loads(Path(self.registration['label_packet']).read_bytes())
        for row in self.labels['units']:
            row.update(label=False, reason='Authored ordinary request or question.')

    def membership(self):
        return {table: self.store.db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                for table in ('artifacts', 'records', 'heads')}

    def test_copied_source_cannot_restore_freshness_after_discovery(self):
        inspect_run(self.store)
        self.assertEqual(prepare(self.store)['status'], 'insufficient_evidence')
        shutil.copytree(self.root / 'sources' / 'sample',
                        self.root / 'sources' / 'copied')
        run_automatic(self.root / 'store', roots={'antigravity': self.root / 'sources'},
                      captured_at='2026-01-01T00:01:00Z')
        self.assertEqual(prepare(self.store)['status'], 'insufficient_evidence')
        restored = Registry(self.root / 'restored-copy')
        self.addCleanup(restored.close)
        restored.import_bundle(self.store.export_bundle())
        self.assertEqual(prepare(restored)['status'], 'insufficient_evidence')
        source(self.root / 'sources', 'different', plain=True)
        run_automatic(self.root / 'store', roots={'antigravity': self.root / 'sources'},
                      captured_at='2026-01-01T00:02:00Z')
        fresh = prepare(self.store)
        self.assertEqual(fresh['status'], 'registered')
        self.assertEqual(fresh['units'], 1)

    def test_copy_exposure_propagates_through_retained_changed_snapshots(self):
        inspect_run(self.store)
        sources = self.root / 'sources'
        shutil.copytree(sources / 'sample', sources / 'copy-one')
        run_automatic(self.root / 'store', roots={'antigravity': sources},
                      captured_at='2026-01-01T00:01:00Z')
        changed = sources / 'copy-one' / '.system_generated/logs/transcript_full.jsonl'
        changed.write_bytes(changed.read_bytes() + jsonl([{
            'step_index': 3, 'type': 'USER_INPUT', 'source': 'USER_EXPLICIT',
            'created_at': TIME, 'content': 'An additional authored request.'}]))
        shutil.copytree(sources / 'copy-one', sources / 'copy-two')
        run_automatic(self.root / 'store', roots={'antigravity': sources},
                      captured_at='2026-01-01T00:02:00Z')
        self.assertEqual(prepare(self.store)['status'], 'insufficient_evidence')

    def test_alias_discovered_after_registration_prevents_comparison(self):
        sources = self.root / 'sources'
        shutil.copytree(sources / 'sample', sources / 'copied')
        run_automatic(self.root / 'store', roots={'antigravity': sources},
                      captured_at='2026-01-01T00:01:00Z')
        original_group = self.store.get(self.ref).payload['split_groups'][0]
        copied = next(c for c in self.store.heads('case')
                      if c.payload['split_group'] != original_group)
        selection = Record.create('selection', 'copied-discovery', {
            'purpose': 'discovery', 'selected_refs': [copied.ref.to_dict()],
            'population': 'authored copied source', 'selection_rule': 'authored_alias_exposure',
            'rule_version': '1', 'unit': 'source', 'selected_at': TIME,
            'strata': [], 'limitations': [], 'exclusions': []})
        self.store.append(selection, None)
        before = self.membership()
        with self.assertRaisesRegex(WorkflowError, 'discovery exposure'):
            compare(self.store, self.ref, self.labels)
        self.assertEqual(self.membership(), before)

    def test_modified_display_or_identity_rejected_before_publication(self):
        changes = {
            'user_turn': 'No, that is incorrect.', 'context': 'Different context.',
            'context_chars': 12345, 'context_truncated': True,
            'split_group': 'different-group',
            'case_ref': {**self.labels['units'][0]['case_ref'], 'key': 'different'},
            'evidence_ref': {**self.labels['units'][0]['evidence_ref'],
                             'selector': {'kind': 'json_pointer', 'pointer': '/absent'}},
        }
        before = self.membership()
        for field, value in changes.items():
            with self.subTest(field=field):
                labels = copy.deepcopy(self.labels)
                labels['units'][0][field] = value
                with self.assertRaisesRegex(WorkflowError, 'registered evidence'):
                    compare(self.store, self.ref, labels)
                self.assertEqual(self.membership(), before)

    def test_missing_fields_and_changed_rubric_do_not_become_blind_labels(self):
        before = self.membership()
        for field in ('label', 'user_turn', 'context_chars', 'evidence_ref'):
            with self.subTest(field=field):
                labels = copy.deepcopy(self.labels)
                del labels['units'][0][field]
                with self.assertRaises(WorkflowError):
                    compare(self.store, self.ref, labels)
        labels = copy.deepcopy(self.labels)
        labels['rubric'] = 'Label every ordinary question as a correction.'
        with self.assertRaises(WorkflowError):
            compare(self.store, self.ref, labels)
        self.assertEqual(self.membership(), before)

    def test_inspection_after_registration_prevents_blind_comparison(self):
        inspect_run(self.store)
        before = self.membership()
        with self.assertRaisesRegex(WorkflowError, 'discovery'):
            compare(self.store, self.ref, self.labels)
        self.assertEqual(self.membership(), before)
        self.assertEqual(prepare(self.store)['status'], 'insufficient_evidence')

    def test_changed_labels_after_prediction_exposure_are_not_new_blind_truth(self):
        original = compare(self.store, self.ref, self.labels, created_at=TIME)
        before = self.membership()
        changed = copy.deepcopy(self.labels)
        changed['units'][0]['label'] = True
        with self.assertRaisesRegex(WorkflowError, 'predictions'):
            compare(self.store, self.ref, changed)
        self.assertEqual(self.membership(), before)
        inspect_run(self.store)
        replay = compare(self.store, self.ref, self.labels)
        self.assertEqual(replay['analysis_ref'], original['analysis_ref'])
        self.assertEqual(len(self.store.heads(kind='analysis')), 1)

    def test_corrected_selection_cannot_erase_observed_discovery(self):
        inspected = inspect_run(self.store)
        selected = self.store.get(RecordRef.from_dict(inspected['selection_ref']))
        payload = selected.payload
        payload['selected_refs'] = []
        self.store.append(Record.create('selection', selected.ref.key, payload,
            study_scope=selected.ref.study_scope, revision=2), 1)
        copied = Registry(self.root / 'corrected-selection-import')
        self.addCleanup(copied.close)
        copied.import_bundle(self.store.export_bundle())
        for store in (self.store, copied):
            with self.subTest(root=store.root):
                self.assertEqual(prepare(store)['status'], 'insufficient_evidence')
                with self.assertRaisesRegex(WorkflowError, 'discovery exposure'):
                    compare(store, self.ref, self.labels)

    def test_corrected_analysis_does_not_replace_original_comparison_on_replay(self):
        receipt = compare(self.store, self.ref, self.labels)
        original = self.store.get(RecordRef.from_dict(receipt['analysis_ref']))
        payload = original.payload
        payload['input_refs'] = []
        payload['limitations'].append('Attributed correction, not a new classifier execution.')
        self.store.append(Record.create('analysis', original.ref.key, payload,
            study_scope=original.ref.study_scope, revision=2), 1)
        self.assertEqual(prepare(self.store)['status'], 'insufficient_evidence')
        with self.assertRaisesRegex(WorkflowError, 'no longer applicable'):
            compare(self.store, self.ref, self.labels)

    def test_corrected_assessment_cannot_restore_blindness(self):
        payload = {'case_ref': self.labels['units'][0]['case_ref'],
            'rubric_version': 'authored', 'protocol_version': '1',
            'assessor_type': 'agent', 'presentation_mode': 'assisted', 'label': False}
        assessment = Record.create('assessment', 'reviewed', payload)
        self.store.append(assessment, None)
        self.assertEqual(prepare(self.store)['status'], 'insufficient_evidence')
        payload['presentation_mode'] = 'blind'
        self.store.append(Record.create('assessment', 'reviewed', payload, revision=2), 1)
        self.assertEqual(prepare(self.store)['status'], 'insufficient_evidence')
        with self.assertRaisesRegex(WorkflowError, 'discovery exposure'):
            compare(self.store, self.ref, self.labels)

    def test_invalidated_inputs_rejected_without_publishing_labels(self):
        old = self.store.get(RecordRef.from_dict(self.labels['units'][0]['case_ref']))
        self.store.append(Record.create('case', old.ref.key, old.payload,
            study_scope=old.ref.study_scope, revision=old.ref.revision + 1, created_at=TIME), old.ref.revision)
        before = self.membership()
        with self.assertRaisesRegex(WorkflowError, 'applicable'):
            compare(self.store, self.ref, self.labels)
        self.assertEqual(self.membership(), before)

    def test_explicit_abstention_and_attribution_preserved_with_portable_records(self):
        self.labels['units'][0].update(label=None, reason='Intent is ambiguous.', assessor_type='human')
        result = compare(self.store, self.ref, self.labels, created_at=TIME)
        self.assertEqual(result['estimates']['metrics']['baseline']['unknown_truth'], 1)
        self.assertEqual(result['assigned_units'], 2)
        self.assertEqual(result['decision']['disposition'], 'insufficient_evidence')
        exported = self.store.export_bundle()
        imported = Registry(self.root / 'imported')
        self.addCleanup(imported.close)
        imported.import_bundle(exported)
        analysis_ref = RecordRef.from_dict(result['analysis_ref'])
        self.assertEqual(imported.get(analysis_ref), self.store.get(analysis_ref))
        self.assertEqual(compare(imported, self.ref, self.labels)['analysis_ref'], result['analysis_ref'])

    def test_missing_stored_labels_cannot_be_replayed_as_an_applicable_comparison(self):
        result = compare(self.store, self.ref, self.labels)
        analysis = self.store.get(RecordRef.from_dict(result['analysis_ref']))
        self.store.artifact_path(analysis.payload['evidence_refs'][0]['sha256']).unlink()
        with self.assertRaisesRegex(WorkflowError, 'no longer applicable'):
            compare(self.store, self.ref, self.labels)

    def test_competing_submissions_publish_only_one_first_comparison(self):
        barrier = Barrier(2)
        def submit(value):
            store = Registry(self.root / 'store')
            labels = copy.deepcopy(self.labels)
            labels['units'][0]['label'] = value
            try:
                barrier.wait(timeout=5)
                try:
                    compare(store, self.ref, labels)
                    return 'accepted'
                except WorkflowError as exc:
                    self.assertIn('predictions', str(exc))
                    return 'rejected'
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(submit, (True, False))), ['accepted', 'rejected'])
        self.assertEqual(len(self.store.heads(kind='analysis')), 1)

    def test_cli_rejects_mismatched_packet_without_transcript_output(self):
        packet = copy.deepcopy(self.labels)
        packet['units'][0]['user_turn'] = 'Changed display sentinel.'
        path = self.root / 'completed-labels.json'
        path.write_bytes(canonical(packet))
        result = subprocess.run([sys.executable, '-B',
            str(Path(__file__).resolve().parents[1] / 'bin/retro-eval-workflow'),
            '--store', str(self.root / 'store'), 'pilot', 'compare', '--labels', str(path)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertIn('registered evidence', result.stderr)
        self.assertNotIn('Changed display sentinel', result.stderr)
        self.assertEqual(self.store.heads(kind='analysis'), [])


class InspectionExposureTests(unittest.TestCase):
    def test_resumed_inspection_records_new_contrasts_before_pilot_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source(root / 'sources', 'first', plain=True)
            source(root / 'sources', 'second', plain=True)
            original = Runner.unit
            count = 0
            def partial(runner, *args, **kwargs):
                nonlocal count
                if args[1].startswith('source:'):
                    count += 1
                    if count > 1:
                        return {'status': 'budget_exhausted'}
                return original(runner, *args, **kwargs)
            with patch.object(Runner, 'unit', partial):
                run = run_automatic(root / 'store', roots={'antigravity': root / 'sources'}, captured_at=TIME)
            store = Registry(root / 'store')
            try:
                first = inspect_run(store)
                self.assertEqual(first['contrasts'], 1)
                run_automatic(root / 'store', roots={'antigravity': root / 'sources'}, resume=run['run_id'])
                second = inspect_run(store)
                self.assertEqual(second['contrasts'], 2)
                self.assertNotEqual(first['selection_ref'], second['selection_ref'])
                selected = store.get(RecordRef.from_dict(second['selection_ref']))
                self.assertEqual(len(selected.payload['selected_refs']), 2)
                self.assertEqual(inspect_run(store)['selection_ref'], second['selection_ref'])
                self.assertEqual(prepare(store)['status'], 'insufficient_evidence')
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
