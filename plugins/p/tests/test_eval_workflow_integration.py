"""Public-boundary coverage for inspection, selection hints and v1 coexistence."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.errors import PolicyDenied, WorkflowError
from retro_eval.workflow.inspection import inspect_run
from retro_eval.workflow.legacy import import_effect
from retro_eval.workflow.planning import plan_run
from retro_eval.workflow.records import Record, strict_loads
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.runner import Limits
from retro_eval.workflow.service import run_automatic
from retro_eval.workflow.sources import selection_hint
from test_effect_records import synthetic_bundle, synthetic_record, write_packet
from test_eval_workflow_records import TIME
from test_eval_workflow_sources import source_fixture, jsonl


class IntegrationTests(unittest.TestCase):
    def test_inspection_uses_contrasts_to_cover_a_candidate_free_case_within_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            source.mkdir()
            for name in ('one', 'two', 'quiet'):
                rows = [{'type': 'user', 'timestamp': TIME, 'promptSource': 'typed',
                         'message': {'role': 'user', 'content': 'Count the local entries for ' + name}}]
                if name != 'quiet':
                    rows.extend([
                        {'type': 'user', 'timestamp': TIME, 'promptSource': 'typed',
                         'message': {'role': 'user', 'content': 'List the available files for ' + name}},
                        {'type': 'assistant', 'timestamp': TIME,
                         'message': {'role': 'assistant', 'content': [{'type': 'text',
                             'text': 'A detailed answer about the available entries. ' * 10}]}},
                        {'type': 'user', 'timestamp': TIME, 'promptSource': 'typed',
                         'message': {'role': 'user', 'content': 'No, use the original records.'}}])
                (source / (name + '.jsonl')).write_bytes(jsonl(rows))
            store = Registry(root / 'store')
            try:
                store.db.execute("UPDATE metadata SET value=? WHERE key='id_salt'", (bytes(32),))
                store.db.commit()
            finally:
                store.close()
            run_automatic(root / 'store', roots={'claude': source}, captured_at=TIME)
            store = Registry(root / 'store')
            self.addCleanup(store.close)
            result = inspect_run(store)
            packet = strict_loads(Path(result['inspection_path']).read_bytes())
            self.assertEqual(len(packet['entries']), 2)
            self.assertEqual(len(packet['noncandidate_contrasts']), 2)
            self.assertTrue(any('quiet' in row['excerpt']['text']
                                for row in packet['noncandidate_contrasts']))
            self.assertEqual(packet['noncandidate_contrasts_available'], 5)
            self.assertEqual(packet['noncandidate_contrasts_omitted'], 3)
            self.assertEqual(result['selection_ref'], inspect_run(store)['selection_ref'])

    def test_automatic_attention_does_not_inherit_unsupported_legacy_schedule(self):
        from retro_eval.workflow.records import canonical
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            source.mkdir()
            (source / 'session.jsonl').write_bytes(source_fixture('claude'))
            store = Registry(root / 'store')
            try:
                with store.transaction():
                    store.db.execute('INSERT INTO metadata VALUES(?,?)',
                                     ('automatic_state', canonical({'cadence_days': 7})))
            finally:
                store.close()
            first = run_automatic(root / 'store', roots={'claude': source}, captured_at=TIME)
            self.assertIsNone(first['cadence']['cadence_days'])
            self.assertEqual(first['cadence']['mode'], 'event_triggered')
            store = Registry(root / 'store')
            try:
                state = strict_loads(store.db.execute(
                    "SELECT value FROM metadata WHERE key='automatic_state'").fetchone()[0])
                self.assertIsNone(state['cadence_days'])
            finally:
                store.close()
            second = run_automatic(root / 'store', roots={'claude': source}, captured_at=TIME)
            self.assertIsNone(second['cadence']['cadence_days'])
            self.assertFalse(second['cadence']['installed'])

    def test_review_legacy_index_cannot_authorize_external_locators(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            collection = root / 'collection'
            collection.mkdir()
            bundle = synthetic_bundle()
            paths = write_packet(collection, bundle, synthetic_record(bundle))
            outside = root / 'outside.json'
            outside.write_bytes((collection / 'facts.json').read_bytes())
            (collection / 'link.json').symlink_to(outside)
            index = strict_loads(paths[1].read_bytes())
            from retro_eval.workflow.records import canonical
            store = Registry(root / 'store')
            self.addCleanup(store.close)
            for locator in ('../outside.json', str(outside), 'link.json'):
                changed = copy.deepcopy(index)
                changed['evidence'][0]['artifact'] = locator
                paths[1].write_bytes(canonical(changed))
                with self.subTest(locator=locator), self.assertRaises(PolicyDenied):
                    import_effect(store, *paths)
            self.assertEqual(store.heads(), [])

    def test_v1_compatibility_preserves_declaration_without_claim_upgrade(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Registry(root / 'store')
            self.addCleanup(store.close)
            bundle = synthetic_bundle()
            record = synthetic_record(bundle)
            paths = write_packet(root, bundle, record)
            imported = import_effect(store, *paths)
            self.assertEqual(imported.payload['legacy_declaration']['disposition'], 'keep')
            self.assertEqual(imported.payload['disposition'], 'insufficient_evidence')
            self.assertEqual(imported.payload['benefit_status'], 'unknown')
            self.assertEqual(import_effect(store, *paths), imported)
            self.assertTrue(store.applicability([imported.ref])['current'])
            (root / 'facts.json').write_bytes(b'changed')
            with self.assertRaises(WorkflowError):
                import_effect(store, *paths)

    def test_inspection_has_noncandidates_and_replay_does_not_duplicate_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            source.mkdir()
            (source / 'session.jsonl').write_bytes(source_fixture('claude'))
            run_automatic(root / 'store', roots={'claude': source}, captured_at=TIME)
            store = Registry(root / 'store')
            self.addCleanup(store.close)
            result = inspect_run(store)
            packet = strict_loads(Path(result['inspection_path']).read_bytes())
            self.assertEqual(packet['candidates_selected'], 0)
            self.assertEqual(len(packet['noncandidate_contrasts']), 1)
            self.assertEqual(packet['presentation_mode'], 'assisted')
            self.assertEqual(inspect_run(store)['selection_ref'], result['selection_ref'])
            self.assertEqual(len(store.heads(kind='selection')), 1)

    def test_header_hints_prioritize_eligible_threads_without_certifying_them(self):
        header = jsonl([{'type': 'session_meta', 'payload': {'id': 'authored', 'thread_source': 'user'}}])
        hint = selection_hint('codex', header)
        self.assertEqual(hint['eligibility'], 'eligible_hint')
        self.assertEqual(selection_hint('codex', header[:10])['eligibility'], 'unknown')
        self.assertEqual(selection_hint('codex', header, exclude_session_ids={'authored'})['eligibility'], 'reporting_session')
        files = [{'source': 'codex', 'relative_path': str(i), 'key': str(i), 'bytes': 100,
                  'mtime_ns': i, 'selection_hint': {'eligibility': 'eligible_hint' if i < 5 else 'ineligible_hint'}}
                 for i in range(20)]
        plan = plan_run({'sources': [{'source': 'codex', 'available': True, 'reason': '', 'truncated': False}],
                         'files': files}, captured_at=TIME, limits=Limits(cases=6))
        self.assertGreaterEqual(sum(f['selection_hint']['eligibility'] == 'eligible_hint'
                                    for f in plan['selected_files']), 4)

    def test_store_components_and_output_paths_cannot_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / 'outside'
            target.write_bytes(b'unchanged')
            (root / 'bad').mkdir()
            (root / 'bad/workflow.sqlite').symlink_to(target)
            with self.assertRaises(PolicyDenied):
                Registry(root / 'bad')
            self.assertEqual(target.read_bytes(), b'unchanged')
            store = Registry(root / 'good')
            self.addCleanup(store.close)
            with self.assertRaises(PolicyDenied):
                store.directory('reports', '..', 'outside')

    def test_exposure_configuration_does_not_prove_execution(self):
        ref = {'study_scope': 'authored', 'kind': 'case', 'key': 'one', 'revision': 1}
        p = {'case_ref': ref, 'pilot_ref': {**ref, 'kind': 'pilot'}, 'component': 'rule',
             'version': '1', 'context': 'authored',
             'stages': {key: {'status': 'unknown', 'evidence_refs': []} for key in
                        ('assigned', 'configuration_present', 'loaded', 'executed', 'behavior_observed')}}
        p['stages']['configuration_present'] = {'status': 'observed', 'evidence_refs': [{'authored': 'config'}]}
        record = Record.create('exposure', 'one', p)
        self.assertEqual(record.payload['stages']['executed']['status'], 'unknown')
        p['stages']['executed']['status'] = 'observed'
        with self.assertRaises(WorkflowError):
            Record.create('exposure', 'two', p)


if __name__ == '__main__':
    unittest.main()
