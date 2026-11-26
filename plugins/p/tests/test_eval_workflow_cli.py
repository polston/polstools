"""Exercise automatic operation through the shipped command and service boundary."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.service import run_automatic
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.runner import Limits, Runner
from retro_eval.workflow.records import Record
from retro_eval.workflow.errors import WorkflowError
from retro_eval.workflow.packs import Pack, PackRegistry, default_packs
from retro_eval.adapters.registry import AdapterRegistry, default_registry
from test_eval_workflow_sources import source_fixture
from test_eval_workflow_records import TIME, case, analysis_payload

PLUGIN = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = self.root / 'store'
        self.sources = {}
        for name in ('claude', 'codex', 'antigravity'):
            root = self.root / name
            relative = ('sample/.system_generated/logs/transcript_full.jsonl' if name == 'antigravity'
                        else 'sample/rollout-sample.jsonl' if name == 'codex' else 'sample/session.jsonl')
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(source_fixture(name))
            self.sources[name] = root

    def tearDown(self):
        self.temp.cleanup()

    def command(self, *args):
        return subprocess.run([sys.executable, '-B', str(PLUGIN / 'bin' / 'retro-eval-workflow'),
                               '--store', str(self.store), *args], capture_output=True, text=True)

    def test_imported_effect_is_discoverable_without_promoting_its_claim_or_trigger(self):
        from test_effect_records import synthetic_bundle, synthetic_record, write_packet
        bundle = synthetic_bundle()
        declared = synthetic_record(bundle)
        record, index = write_packet(self.root, bundle, declared)
        imported = self.command('effect-import', '--record', str(record), '--evidence-index', str(index))
        self.assertEqual(imported.returncode, 0, imported.stderr)
        before = {p.relative_to(self.store): p.read_bytes() for p in self.store.rglob('*') if p.is_file()}
        status = self.command('status')
        self.assertEqual(status.returncode, 0, status.stderr)
        result = json.loads(status.stdout)
        self.assertEqual(result['pilot_followups'], [])
        effect, = result['legacy_effects']
        self.assertEqual(effect['decision_ref'], json.loads(imported.stdout)['decision_ref'])
        self.assertEqual(effect['declared_review_trigger'], declared['review_trigger'])
        self.assertEqual(effect['declared_next_action'], declared['next_action'])
        self.assertEqual(effect['proposal_id'], declared['proposal_id'])
        self.assertEqual(effect['benefit_status'], 'unknown')
        self.assertEqual(effect['disposition'], 'insufficient_evidence')
        self.assertEqual(effect['operational_status'], 'inactive')
        self.assertNotIn('due', effect)
        self.assertTrue(effect['current_applicability']['current'])
        self.assertTrue(effect['next_action']['action'])
        self.assertEqual(before, {p.relative_to(self.store): p.read_bytes()
                                 for p in self.store.rglob('*') if p.is_file()})

        # Losing retained evidence must be visible even though the authored
        # declaration and import-time binding check still exist in the database.
        with closing(Registry(self.store)) as store:
            retained, = store.heads(kind='decision', study_scope='legacy-effects')
            artifact = store.artifact_path(retained.payload['evidence_refs'][0]['sha256'])
            original_bytes = artifact.read_bytes()
            artifact.unlink()
        damaged = self.command('status')
        self.assertEqual(damaged.returncode, 0, damaged.stderr)
        result = json.loads(damaged.stdout)
        self.assertFalse(result['legacy_effects'][0]['current_applicability']['current'])
        self.assertFalse(result['current_applicability']['current'])
        self.assertEqual(result['legacy_effects'][0]['benefit_status'], 'unknown')
        damaged_effect = result['legacy_effects'][0]
        self.assertEqual(damaged_effect['declared_next_action'], declared['next_action'])
        self.assertEqual(damaged_effect['recorded_next_action'], effect['next_action'])
        self.assertIn('Reconcile', damaged_effect['next_action']['action'])
        self.assertNotEqual(damaged_effect['next_action'], effect['next_action'])
        artifact.write_bytes(original_bytes)
        restored = self.command('status')
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(json.loads(restored.stdout)['legacy_effects'][0], effect)

    def test_status_handles_portable_decision_without_legacy_extension_fields(self):
        # The portable decision contract does not require importer-specific
        # extension fields, even when its author chose the legacy scope.
        with closing(Registry(self.store)) as store:
            decision = Record.create('decision', 'portable', {
                'disposition': 'insufficient_evidence', 'benefit_status': 'unknown',
                'quality_status': 'unknown', 'analysis_refs': [],
                'counterevidence_refs': [], 'evidence_refs': [],
                'rationale': 'Authored portable decision with no legacy declaration.',
                'next_action': {'owner': 'agent', 'action': 'Recover original inputs.',
                    'prerequisites': 'Original record.', 'acceptance': 'Inputs inspected.'},
            }, study_scope='legacy-effects')
            store.append(decision, None)
        status = self.command('status')
        self.assertEqual(status.returncode, 0, status.stderr)
        effect, = json.loads(status.stdout)['legacy_effects']
        self.assertIsNone(effect['proposal_id'])
        self.assertIsNone(effect['declared_review_trigger'])
        self.assertEqual(effect['operational_status'], 'unknown')

    def test_header_capture_rejects_directory_swap_without_retaining_outside_bytes(self):
        source = self.sources['codex']
        nested = source / 'sample'
        outside = self.root / 'outside'
        outside.mkdir()
        marker = b'outside-source-marker'
        (outside / 'rollout-sample.jsonl').write_bytes(marker)
        original_open = os.open
        swapped = []
        def racing_open(path, flags, *args, **kwargs):
            if path == 'sample' and kwargs.get('dir_fd') is not None and not swapped:
                nested.rename(source / 'retained')
                nested.symlink_to(outside, target_is_directory=True)
                swapped.append(True)
            return original_open(path, flags, *args, **kwargs)
        with patch('retro_eval.workflow.sources.os.open', side_effect=racing_open):
            result = run_automatic(self.store, roots={'codex': source}, captured_at=TIME)
        self.assertTrue(swapped)
        report = json.loads(Path(result['report_path']).read_text())
        self.assertEqual(report['coverage']['codex']['captured'], 0)
        self.assertTrue(report['coverage']['codex']['source_reason'])
        for artifact in (self.store / 'objects').iterdir():
            self.assertNotIn(marker, artifact.read_bytes())

    def test_inventory_reads_metadata_without_capturing_or_mutating_store(self):
        Registry(self.store).close()
        before = {p.relative_to(self.store): p.read_bytes() for p in self.store.rglob('*') if p.is_file()}
        output = self.root / 'inventory.json'
        args = ['inventory', '--output', str(output)]
        files = []
        for name, root in self.sources.items():
            args += ['--source', '%s=%s' % (name, root)]
            files.extend(root.rglob('*.jsonl'))
        modes = {p: p.stat().st_mode for p in files}
        try:
            for path in files:
                path.chmod(0)
            result = self.command(*args)
        finally:
            for path, mode in modes.items():
                path.chmod(mode)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['files'], 3)
        self.assertFalse(receipt['source_contents_captured'])
        self.assertNotIn('relative_path', result.stdout)
        snapshot = json.loads(output.read_bytes())
        self.assertTrue(snapshot['population_complete'])
        self.assertTrue(all('selection_hint' not in row for row in snapshot['files']))
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(before, {p.relative_to(self.store): p.read_bytes()
                                 for p in self.store.rglob('*') if p.is_file()})

    def test_inventory_exposes_gaps_and_refuses_overwrite_or_protected_output(self):
        Registry(self.store).close()
        output = self.root / 'inventory.json'
        args = ['inventory', '--output', str(output), '--file-limit', '1',
                '--source', 'claude=%s' % self.sources['claude'],
                '--source', 'codex=%s' % (self.root / 'missing')]
        result = self.command(*args)
        self.assertEqual(result.returncode, 1, result.stderr)
        snapshot = json.loads(output.read_bytes())
        self.assertFalse(snapshot['population_complete'])
        self.assertTrue(any(s['reason'] == 'source_root_unavailable' for s in snapshot['sources']))
        original = output.read_bytes()
        self.assertEqual(self.command(*args).returncode, 2)
        self.assertEqual(output.read_bytes(), original)
        for destination in (self.store / 'inventory.json', self.sources['claude'] / 'inventory.json'):
            result = self.command('inventory', '--output', str(destination),
                                  '--source', 'claude=%s' % self.sources['claude'])
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(destination.exists())
        another = self.sources['claude'] / 'sample' / 'another.jsonl'
        another.write_text('{}\n')
        result = self.command('inventory', '--output', str(self.root / 'truncated.json'),
                              '--source', 'claude=%s' % self.sources['claude'], '--file-limit', '1')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(json.loads((self.root / 'truncated.json').read_bytes())['sources'][0]['truncated'])

    def test_inventory_requires_existing_store_and_valid_limits(self):
        output = self.root / 'inventory.json'
        result = self.command('inventory', '--output', str(output))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.store.exists())
        self.assertFalse(output.exists())
        Registry(self.store).close()
        for option in ('--file-limit', '--runtime-seconds'):
            result = self.command('inventory', '--output', str(output), option, '0')
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(output.exists())

    def test_inventory_previous_compares_actual_changes_without_mutating_inputs(self):
        Registry(self.store).close()
        old = self.root / 'before.json'
        source = self.sources['claude']
        removed = source / 'removed.jsonl'
        removed.write_text('{}\n')
        result = self.command('inventory', '--output', str(old), '--source', 'claude=%s' % source)
        self.assertEqual(result.returncode, 0, result.stderr)
        original = old.read_bytes()
        store_bytes = {p.relative_to(self.store): p.read_bytes() for p in self.store.rglob('*') if p.is_file()}
        removed.unlink()
        (source / 'added.jsonl').write_text('{}\n')
        with (source / 'sample/session.jsonl').open('a') as handle:
            handle.write('{}\n')
        output = self.root / 'after.json'
        result = self.command('inventory', '--output', str(output), '--previous', str(old),
                              '--source', 'claude=%s' % source)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        for field in ('newly_observed', 'metadata_changed', 'not_observed_in_current'):
            self.assertEqual(receipt['comparison'][field], 1)
        self.assertNotIn('relative_path', result.stdout)
        self.assertEqual(old.read_bytes(), original)
        self.assertEqual(store_bytes, {p.relative_to(self.store): p.read_bytes() for p in self.store.rglob('*') if p.is_file()})
        comparison = json.loads(output.read_bytes())['comparison']
        self.assertEqual(comparison['newly_observed'][0]['relative_path'], 'added.jsonl')
        self.assertEqual(comparison['not_observed_in_current'][0]['relative_path'], 'removed.jsonl')

    def test_inventory_previous_incomparability_and_malformed_snapshot(self):
        Registry(self.store).close()
        old = self.root / 'before.json'
        result = self.command('inventory', '--output', str(old), '--source', 'claude=%s' % self.sources['claude'])
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.root / 'after.json'
        result = self.command('inventory', '--output', str(output), '--previous', str(old),
                              '--source', 'claude=%s' % self.sources['codex'])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)['comparison']['incomparable_sources'], 1)
        invalid = json.loads(old.read_bytes())
        invalid['files'].append(invalid['files'][0])
        old.write_text(json.dumps(invalid))
        rejected = self.root / 'rejected.json'
        result = self.command('inventory', '--output', str(rejected), '--previous', str(old),
                              '--source', 'claude=%s' % self.sources['claude'])
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(rejected.exists())

    def test_inventory_previous_rejects_invalid_or_contradictory_coverage(self):
        Registry(self.store).close()
        old = self.root / 'before.json'
        result = self.command('inventory', '--output', str(old), '--source', 'claude=%s' % self.sources['claude'])
        self.assertEqual(result.returncode, 0, result.stderr)
        original = old.read_bytes()
        variants = [(None, 'file_limit', -1), (None, 'population_complete', 'false'),
                    (None, 'population_complete', False),
                    (0, 'enumerated', -1), (0, 'enumerated', 0),
                    (0, 'enumeration_error_count', -1), (0, 'enumeration_error_count', 1),
                    (0, 'enumeration_errors_omitted', -1)]
        for index, (source, field, value) in enumerate(variants):
            with self.subTest(field=field, value=value):
                invalid = json.loads(original)
                (invalid if source is None else invalid['sources'][source])[field] = value
                old.write_text(json.dumps(invalid))
                output = self.root / ('invalid-%s.json' % index)
                result = self.command('inventory', '--output', str(output), '--previous', str(old),
                                      '--source', 'claude=%s' % self.sources['claude'])
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(output.exists())

    def test_empty_source_name_fails_before_store_or_output_creation(self):
        output = self.root / 'inventory.json'
        for args in (['inventory', '--output', str(output)], ['run']):
            result = self.command(*args, '--source', '=%s' % self.sources['claude'])
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('source must be a unique NAME=ROOT', result.stderr)
            self.assertFalse(self.store.exists())
            self.assertFalse(output.exists())

    def test_saved_report_and_current_receipt_identify_different_usage_scopes(self):
        result = run_automatic(self.store, roots=self.sources, captured_at=TIME)
        saved = json.loads(Path(result['report_path']).read_bytes())
        status = self.command('status')
        self.assertEqual(status.returncode, 0, status.stderr)
        current = next(r for r in json.loads(status.stdout)['runs'] if r['run_id'] == result['run_id'])
        self.assertEqual(saved['usage_scope'], 'source_operation_reservations')
        self.assertEqual(result['usage_scope'], 'all_run_operation_reservations')
        self.assertEqual(current['usage_scope'], result['usage_scope'])
        self.assertEqual(current['used'], result['used'])
        self.assertGreater(current['used']['derived_bytes'], saved['used']['derived_bytes'])
        # Codex planning reads a header hint before source capture.
        self.assertGreater(current['used']['captured_bytes'], saved['used']['captured_bytes'])

    def test_all_sources_automatic_without_manual_study_parameters(self):
        result = run_automatic(self.store, roots=self.sources, captured_at=TIME)
        self.assertEqual(result['state'], 'completed')
        self.assertTrue(all(v['included'] == 1 for v in result['coverage'].values()))
        self.assertEqual(result['used']['provider_calls'], 0)
        self.assertTrue(Path(result['report_path']).is_file())
        self.assertTrue(all(c['delivery']['status'] == 'unknown' for c in result['packet']['cases']))
        self.assertTrue(all(not c['independent_unit_eligible'] for c in result['packet']['cases']))
        second = run_automatic(self.store, roots=self.sources, captured_at=TIME)
        store = Registry(self.store)
        try:
            self.assertEqual(len(store.heads(kind='case')), 3)
        finally:
            store.close()
        self.assertEqual(second['state'], 'completed')

    def test_inventory_gap_cannot_complete_or_advance_review_cutoff(self):
        missing = self.root / 'unavailable-source'
        result = run_automatic(self.store, roots={
            'claude': self.sources['claude'], 'codex': missing},
            enabled_packs=(), captured_at=TIME)
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(result['exit_code'], 1)
        self.assertTrue(any(g['source'] == 'codex' for g in result['gaps']))
        self.assertEqual(result['coverage']['claude']['included'], 1)
        store = Registry(self.store)
        try:
            state = json.loads(store.db.execute(
                "SELECT value FROM metadata WHERE key='automatic_state'").fetchone()[0])
            self.assertNotIn('last_committed_cutoff', state)
        finally:
            store.close()

    def test_truncated_inventory_remains_partial_after_successful_capture(self):
        result = run_automatic(self.store, roots={'claude': self.sources['claude']},
            limits=Limits(source_files=1), enabled_packs=(), captured_at=TIME)
        # One source file is a complete census and can complete cleanly.
        self.assertEqual(result['state'], 'completed')
        second = self.sources['claude'] / 'sample' / 'another.jsonl'
        second.write_bytes(source_fixture('claude'))
        result = run_automatic(self.root / 'truncated-store',
            roots={'claude': self.sources['claude']}, limits=Limits(source_files=1),
            enabled_packs=(), captured_at=TIME)
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(result['exit_code'], 1)
        self.assertTrue(result['coverage']['claude']['enumeration_truncated'])

    def test_legacy_completion_still_exposes_its_frozen_inventory_gap(self):
        roots = {'claude': self.sources['claude'], 'codex': self.root / 'missing'}
        first = run_automatic(self.store, roots=roots, enabled_packs=(), captured_at=TIME)
        store = Registry(self.store)
        try:
            # Reconstruct the former persisted state: successful source work,
            # a terminal receipt, and inventory gaps only in its frozen plan.
            with store.transaction():
                store.db.execute("UPDATE runs SET state='completed' WHERE run_id=?", (first['run_id'],))
                store.db.execute("UPDATE metadata SET value=? WHERE key=?",
                                 ('[]', 'run_gaps:' + first['run_id']))
            before = Runner(store).status(first['run_id'])
        finally:
            store.close()
        old_report = Path(first['report_path']).read_bytes()
        resumed = run_automatic(self.store, roots=roots, resume=first['run_id'])
        self.assertEqual(resumed['state'], 'completed')
        self.assertEqual(resumed['exit_code'], 1)
        self.assertTrue(any(g['source'] == 'codex' for g in resumed['gaps']))
        self.assertEqual(Path(first['report_path']).read_bytes(), old_report)
        store = Registry(self.store)
        try:
            after = Runner(store).status(first['run_id'])
            self.assertEqual(before['plan'], after['plan'])
            self.assertEqual([o for o in before['operations'] if o['unit_key'].startswith('source:')],
                             [o for o in after['operations'] if o['unit_key'].startswith('source:')])
            state = json.loads(store.db.execute(
                "SELECT value FROM metadata WHERE key='automatic_state'").fetchone()[0])
            self.assertNotIn('last_committed_cutoff', state)
        finally:
            store.close()

    def test_F01_empty_source_is_cannot_run_not_success(self):
        empty = self.root / 'empty'
        empty.mkdir()
        result = run_automatic(self.store, roots={'claude': empty})
        self.assertEqual(result['exit_code'], 2)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['run_reason'], 'no_eligible_evidence')

    def test_resume_rechecks_authorized_source_roots(self):
        first = run_automatic(self.store, roots=self.sources, captured_at=TIME)
        changed = {**self.sources, 'codex': self.root / 'different'}
        with self.assertRaises(WorkflowError):
            run_automatic(self.store, roots=changed, resume=first['run_id'])

    def test_failed_symlink_root_receipt_can_be_reopened(self):
        loop = self.root / 'loop'
        try:
            loop.symlink_to(loop.name)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links unavailable')
        first = run_automatic(self.store, roots={'claude': loop}, captured_at=TIME)
        second = run_automatic(self.store, roots={'claude': loop}, resume=first['run_id'])
        self.assertEqual(first['state'], 'failed')
        self.assertEqual(second['state'], 'failed')
        self.assertEqual(second['plan'], first['plan'])
        self.assertEqual(second['gaps'], first['gaps'])

    def test_file_replaced_by_symlink_loop_after_inventory_stays_partial(self):
        from retro_eval.workflow.planning import inventory_sources
        source = self.root / 'changing-source'
        source.mkdir()
        changed = source / 'changed.jsonl'
        changed.write_bytes(source_fixture('claude'))
        (source / 'readable.jsonl').write_bytes(source_fixture('claude'))
        probe = source / 'symlink-capability'
        try:
            probe.symlink_to(probe.name)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links unavailable')
        probe.unlink()
        def inventory_then_change(*args, **kwargs):
            result = inventory_sources(*args, **kwargs)
            changed.unlink()
            changed.symlink_to(changed.name)
            return result
        with patch('retro_eval.workflow.service.inventory_sources', side_effect=inventory_then_change):
            result = run_automatic(self.store, roots={'claude': source}, captured_at=TIME)
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(len(result['outputs']), 1)
        self.assertTrue(result['gaps'])
        store = Registry(self.store)
        try:
            state = store.db.execute("SELECT value FROM metadata WHERE key='automatic_state'").fetchone()
            self.assertIsNone(json.loads(state[0]).get('last_committed_cutoff'))
        finally:
            store.close()

    def test_F02_unknown_source_cli(self):
        result = self.command('run', '--source', 'unknown=' + str(self.sources['claude']))
        self.assertEqual(result.returncode, 2, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['coverage']['unknown']['source_reason'], 'unregistered_source')

    def test_review_status_reports_superseded_analysis_inputs(self):
        store = Registry(self.store)
        try:
            old = case()
            store.append(old, None)
            store.append(Record.create('analysis', 'bound', analysis_payload(input_refs=[old.ref.to_dict()])), None)
            store.append(case(revision=2), 1)
        finally:
            store.close()
        result = json.loads(self.command('status').stdout)
        self.assertFalse(result['current_applicability']['current'])
        self.assertFalse(result['analysis_applicability']['current'])

    def test_review_repeat_report_and_current_evidence_invalidation(self):
        first = run_automatic(self.store, roots=self.sources, captured_at=TIME)
        second = run_automatic(self.store, roots=self.sources, resume=first['run_id'])
        self.assertEqual(first['report_path'], second['report_path'])
        store = Registry(self.store)
        try:
            ref = first['outputs'][0]['excerpts_ref']
            store.artifact_path(ref['sha256']).write_bytes(b'corrupted artifact')
        finally:
            store.close()
        third = run_automatic(self.store, roots=self.sources, resume=first['run_id'])
        self.assertFalse(third['result_applicability']['current'])
        self.assertNotEqual(first['report_path'], third['report_path'])
        self.assertTrue(Path(first['report_path']).exists())

    def test_interpretation_gaps_are_flagged_without_committing_coverage(self):
        examples = (b'{broken\n', b'[]\n', b'{"type":"future_type"}\n')
        source = self.sources['codex']
        path = source / 'sample/rollout-sample.jsonl'
        for index, extra in enumerate(examples):
            with self.subTest(extra=extra):
                path.write_bytes(source_fixture('codex') + extra)
                store_path = self.root / ('gap-store-' + str(index))
                result = run_automatic(store_path, roots={'codex': source}, captured_at='2026-01-02T00:00:00Z')
                self.assertEqual(result['exit_code'], 1)
                self.assertEqual(result['state'], 'partial')
                gaps = [g for g in result['gaps'] if g['reason'] == 'source_interpretation_incomplete']
                self.assertEqual(len(gaps), 1)
                self.assertTrue(gaps[0]['diagnostic_counts'])
                with closing(Registry(store_path, readonly=True)) as store:
                    state = json.loads(store.db.execute(
                        "SELECT value FROM metadata WHERE key='automatic_state'").fetchone()[0])
                self.assertNotIn('last_committed_cutoff', state)
                self.assertTrue(result['outputs'][0]['included'])

    def test_known_codex_records_with_unreadable_payload_leave_coverage_partial(self):
        source = self.sources['codex']
        path = source / 'sample/rollout-sample.jsonl'
        for kind in ('session_meta', 'response_item', 'event_msg'):
            for index, payload in enumerate((None, [], 'unreadable', 1, False)):
                with self.subTest(kind=kind, payload=payload):
                    raw = source_fixture('codex') + (json.dumps(
                        {'type': kind, 'payload': payload}) + '\n').encode()
                    path.write_bytes(raw)
                    store_path = self.root / ('envelope-' + kind + str(index))
                    result = run_automatic(store_path, roots={'codex': source},
                                           captured_at='2026-01-02T00:00:00Z')
                    self.assertEqual((result['exit_code'], result['state']), (1, 'partial'))
                    self.assertEqual(result['gaps'][0]['diagnostic_counts'],
                                     {'invalid_record_payload': 1})
                    self.assertTrue(result['outputs'][0]['included'])
                    with closing(Registry(store_path, readonly=True)) as store:
                        self.assertEqual(store.read_artifact(result['outputs'][0]['raw_ref'])[0], raw)
                        state = json.loads(store.db.execute(
                            "SELECT value FROM metadata WHERE key='automatic_state'").fetchone()[0])
                    self.assertNotIn('last_committed_cutoff', state)

    def test_intentional_future_filter_is_not_an_interpretation_gap(self):
        source = self.sources['codex']
        path = source / 'sample/rollout-sample.jsonl'
        for kind, payload in (('event_msg', {}), ('future_type', {}), ('response_item', False)):
            with self.subTest(kind=kind):
                extra = {'type': kind, 'timestamp': '2099-01-01T00:00:00Z', 'payload': payload}
                path.write_bytes(source_fixture('codex') + (json.dumps(extra) + '\n').encode())
                result = run_automatic(self.root / ('future-' + kind), roots={'codex': source},
                                       captured_at='2026-01-02T00:00:00Z')
                self.assertEqual(result['exit_code'], 0)
                self.assertEqual(result['state'], 'completed')
                self.assertFalse(result['gaps'])
                self.assertIn({'line': 6, 'reason': 'after_closed_window'},
                              result['outputs'][0]['diagnostics'])

    def test_resume_rejects_changed_shared_parser_or_classification_profile(self):
        original = Runner.unit
        calls = [0]
        def interrupt(runner, *args, **kwargs):
            if args[1].startswith('source:'):
                calls[0] += 1
            if calls[0] > 1:
                return {'status': 'budget_exhausted', 'reason': 'authored_interruption'}
            return original(runner, *args, **kwargs)
        with patch.object(Runner, 'unit', interrupt):
            first = run_automatic(self.store, roots=self.sources, captured_at=TIME)
        self.assertEqual(first['state'], 'partial')
        store = Registry(self.store, readonly=True)
        self.addCleanup(store.close)
        before = store.export_bundle()
        original_read = Path.read_bytes
        for relative in ('retro_eval/proposal_report.py', 'profiles/tool-taxonomy.json',
                         'retro_eval/claude_usage.py'):
            target = PLUGIN / relative
            def changed(path):
                value = original_read(path)
                return value + b'\n' if path == target else value
            with self.subTest(dependency=relative), patch.object(Path, 'read_bytes', changed):
                with self.assertRaisesRegex(WorkflowError, 'measurement composition changed'):
                    run_automatic(self.store, roots=self.sources, resume=first['run_id'])
            self.assertEqual(store.export_bundle(), before)
        # Rejecting a changed dependency neither consumes nor renews the run.
        resumed = run_automatic(self.store, roots=self.sources, resume=first['run_id'])
        self.assertEqual(resumed['state'], 'completed')
        self.assertEqual(resumed['plan'], first['plan'])

    def test_resume_rejects_domain_pack_replacement_and_configuration_changes(self):
        original_unit, calls = Runner.unit, [0]
        def interrupt(runner, *args, **kwargs):
            if args[1].startswith('source:'):
                calls[0] += 1
            if calls[0] > 1:
                return {'status': 'budget_exhausted', 'reason': 'authored_interruption'}
            return original_unit(runner, *args, **kwargs)
        with patch.object(Runner, 'unit', interrupt):
            first = run_automatic(self.store, roots=self.sources, captured_at=TIME,
                                  enabled_packs=('diagnosis',))
        self.assertEqual(first['state'], 'partial')
        original_pack = default_packs()._packs['diagnosis']
        variations = (
            {'analyze': lambda context: {'status': 'completed', 'candidates': []}},
            {'required_capabilities': ('unobserved_capability',)},
            {'claim_requirements': {'new_claim': ['unobserved_capability']}},
            {'measurement_mappings': {'scope': 'changed'}},
        )
        with closing(Registry(self.store, readonly=True)) as store:
            before = store.export_bundle()
        for variation in variations:
            with self.subTest(variation=list(variation)):
                packs = PackRegistry([replace(original_pack, **variation)])
                with self.assertRaisesRegex(WorkflowError, 'domain pack implementation or configuration changed'):
                    run_automatic(self.store, roots=self.sources, resume=first['run_id'],
                                  packs=packs, enabled_packs=('diagnosis',))
                with closing(Registry(self.store, readonly=True)) as store:
                    self.assertEqual(store.export_bundle(), before)
        resumed = run_automatic(self.store, roots=self.sources, resume=first['run_id'],
                                enabled_packs=('diagnosis',))
        self.assertEqual(resumed['state'], 'completed')
        self.assertEqual(resumed['plan'], first['plan'])

    def test_domain_pack_binding_distinguishes_json_scalar_types(self):
        original_pack = default_packs()._packs['diagnosis']
        for index, (old, new) in enumerate(((False, 0), (True, 1), (1, 1.0))):
            with self.subTest(old=old, new=new):
                store_path = self.root / ('scalar-pack-' + str(index))
                original_unit, calls = Runner.unit, [0]
                def interrupt(runner, *args, **kwargs):
                    if args[1].startswith('source:'):
                        calls[0] += 1
                    if calls[0] > 1:
                        return {'status': 'budget_exhausted', 'reason': 'authored_interruption'}
                    return original_unit(runner, *args, **kwargs)
                initial_packs = PackRegistry([replace(original_pack,
                    measurement_mappings={'nested': {'value': old}})])
                changed_packs = PackRegistry([replace(original_pack,
                    measurement_mappings={'nested': {'value': new}})])
                with patch.object(Runner, 'unit', interrupt):
                    first = run_automatic(store_path, roots=self.sources, captured_at=TIME,
                        enabled_packs=('diagnosis',), packs=initial_packs)
                self.assertEqual(first['state'], 'partial')
                with closing(Registry(store_path, readonly=True)) as store:
                    before = store.export_bundle()
                with self.assertRaisesRegex(WorkflowError, 'domain pack implementation or configuration changed'):
                    run_automatic(store_path, roots=self.sources, resume=first['run_id'],
                        enabled_packs=('diagnosis',), packs=changed_packs)
                with closing(Registry(store_path, readonly=True)) as store:
                    self.assertEqual(store.export_bundle(), before)
                resumed = run_automatic(store_path, roots=self.sources, resume=first['run_id'],
                    enabled_packs=('diagnosis',), packs=initial_packs)
                self.assertEqual(resumed['state'], 'completed')
                self.assertEqual(resumed['plan'], first['plan'])

    def test_custom_pack_can_run_fresh_but_not_resume_as_frozen_code(self):
        executions = []
        def analyze(context):
            executions.append(context['source'])
            return {'status': 'completed', 'candidates': []}
        packs = PackRegistry([Pack('custom', '1', analyze)])
        original_unit, calls = Runner.unit, [0]
        def interrupt(runner, *args, **kwargs):
            if args[1].startswith('source:'):
                calls[0] += 1
            if calls[0] > 1:
                return {'status': 'budget_exhausted', 'reason': 'authored_interruption'}
            return original_unit(runner, *args, **kwargs)
        with patch.object(Runner, 'unit', interrupt):
            first = run_automatic(self.store, roots=self.sources, captured_at=TIME,
                                  enabled_packs=('custom',), packs=packs)
        self.assertEqual(first['state'], 'partial')
        with closing(Registry(self.store, readonly=True)) as store:
            before = store.export_bundle()
        count = len(executions)
        with self.assertRaisesRegex(WorkflowError, 'domain pack implementation has no frozen resumable identity'):
            run_automatic(self.store, roots=self.sources, resume=first['run_id'],
                          enabled_packs=('custom',), packs=packs)
        self.assertEqual(len(executions), count)
        with closing(Registry(self.store, readonly=True)) as store:
            self.assertEqual(store.export_bundle(), before)
        fresh = run_automatic(self.root / 'fresh-custom', roots=self.sources,
                              enabled_packs=('custom',), packs=packs, captured_at=TIME)
        self.assertEqual(fresh['state'], 'completed')
        terminal = run_automatic(self.root / 'fresh-custom', roots=self.sources,
                                 enabled_packs=('custom',), packs=packs, resume=fresh['run_id'])
        self.assertEqual(terminal['state'], 'completed')
        self.assertEqual(terminal['plan'], fresh['plan'])

    def test_resume_rejects_adapter_registration_replacement_before_execution(self):
        original_unit, calls = Runner.unit, [0]
        def interrupt(runner, *args, **kwargs):
            if args[1].startswith('source:'):
                calls[0] += 1
            if calls[0] > 1:
                return {'status': 'budget_exhausted', 'reason': 'authored_interruption'}
            return original_unit(runner, *args, **kwargs)
        with patch.object(Runner, 'unit', interrupt):
            first = run_automatic(self.store, roots=self.sources, captured_at=TIME)
        self.assertEqual(first['state'], 'partial')
        with closing(Registry(self.store, readonly=True)) as store:
            before = store.export_bundle()
        original = default_registry().get('claude')
        replacements = (replace(original, factory=lambda salt, options: original.create(salt)),
                        replace(original, class_name='ChangedAdapter'))
        for index, replacement in enumerate(replacements):
            copied_store = self.root / ('replacement-' + str(index))
            shutil.copytree(self.store, copied_store)
            registry = AdapterRegistry([replacement if item.name == 'claude' else item
                                        for item in default_registry()])
            with self.subTest(factory=replacement.factory is not None):
                with self.assertRaisesRegex(WorkflowError, 'adapter implementation'):
                    run_automatic(copied_store, roots=self.sources, resume=first['run_id'],
                                  adapters=registry)
                with closing(Registry(copied_store, readonly=True)) as store:
                    self.assertEqual(store.export_bundle(), before)
        resumed = run_automatic(self.store, roots=self.sources, resume=first['run_id'])
        self.assertEqual(resumed['state'], 'completed')
        self.assertEqual(resumed['plan'], first['plan'])

    def test_unbound_factory_can_run_but_cannot_resume_as_frozen_code(self):
        original_adapter = default_registry().get('claude')
        factory_calls = []
        def factory(salt, options):
            factory_calls.append(True)
            return original_adapter.create(salt)
        registrations = AdapterRegistry([
            replace(item, factory=factory) if item.name == 'claude' else item
            for item in default_registry()])
        original_unit, calls = Runner.unit, [0]
        def interrupt(runner, *args, **kwargs):
            if args[1].startswith('source:'):
                calls[0] += 1
            if calls[0] > 1:
                return {'status': 'budget_exhausted', 'reason': 'authored_interruption'}
            return original_unit(runner, *args, **kwargs)
        with patch.object(Runner, 'unit', interrupt):
            first = run_automatic(self.store, roots=self.sources, captured_at=TIME,
                                  adapters=registrations)
        self.assertEqual(first['state'], 'partial')
        self.assertFalse(factory_calls)
        with closing(Registry(self.store, readonly=True)) as store:
            before = store.export_bundle()
        with self.assertRaisesRegex(WorkflowError, 'no frozen resumable identity'):
            run_automatic(self.store, roots=self.sources, resume=first['run_id'],
                          adapters=registrations)
        self.assertFalse(factory_calls)
        with closing(Registry(self.store, readonly=True)) as store:
            self.assertEqual(store.export_bundle(), before)
        fresh = run_automatic(self.root / 'fresh-factory', roots=self.sources,
                              captured_at=TIME, adapters=registrations)
        self.assertEqual(fresh['state'], 'completed')
        self.assertTrue(factory_calls)

    def test_resume_preserves_configured_exclusions_and_rejects_option_drift(self):
        source = self.sources['antigravity']
        second = source / 'ordinary/.system_generated/logs/transcript_full.jsonl'
        second.parent.mkdir(parents=True)
        second.write_bytes(source_fixture('antigravity'))
        # Ordinary evidence is captured first; the excluded session is pending.
        os.utime(second, (2000000000, 2000000000))
        original_adapter = default_registry().get('antigravity')
        configured = replace(original_adapter, options={**original_adapter.options,
            'excluded_session_ids': ['sample']})
        adapters = AdapterRegistry([configured])
        original_unit, calls = Runner.unit, [0]
        def interrupt(runner, *args, **kwargs):
            if args[1].startswith('source:'):
                calls[0] += 1
            if calls[0] > 1:
                return {'status': 'budget_exhausted', 'reason': 'authored_interruption'}
            return original_unit(runner, *args, **kwargs)
        roots = {'antigravity': source}
        with patch.object(Runner, 'unit', interrupt):
            first = run_automatic(self.store, roots=roots, adapters=adapters,
                                  captured_at=TIME)
        self.assertEqual(first['state'], 'partial')
        self.assertEqual([o['included'] for o in first['outputs']], [True])
        store = Registry(self.store, readonly=True)
        self.addCleanup(store.close)
        before = store.export_bundle()
        with self.assertRaisesRegex(WorkflowError, 'adapter options changed'):
            run_automatic(self.store, roots=roots, resume=first['run_id'])
        self.assertEqual(store.export_bundle(), before)
        resumed = run_automatic(self.store, roots=roots, adapters=adapters,
                                resume=first['run_id'])
        self.assertEqual(resumed['state'], 'completed')
        self.assertEqual(resumed['plan'], first['plan'])
        self.assertEqual(sorted(o['included'] for o in resumed['outputs']), [False, True])
        self.assertEqual(len(store.heads(kind='case')), 1)

    def test_resume_option_binding_distinguishes_boolean_and_numeric_exclusions(self):
        source = self.sources['antigravity']
        (source / 'sample').rename(source / 'false')
        ordinary = source / 'ordinary/.system_generated/logs/transcript_full.jsonl'
        ordinary.parent.mkdir(parents=True)
        ordinary.write_bytes(source_fixture('antigravity'))
        os.utime(ordinary, (2000000000, 2000000000))
        registration = default_registry().get('antigravity')
        def configured(value):
            return AdapterRegistry([replace(registration, options={
                **registration.options, 'excluded_session_ids': [value]})])
        original_unit, calls = Runner.unit, [0]
        def interrupt(runner, *args, **kwargs):
            if args[1].startswith('source:'):
                calls[0] += 1
            if calls[0] > 1:
                return {'status': 'budget_exhausted', 'reason': 'authored_interruption'}
            return original_unit(runner, *args, **kwargs)
        with patch.object(Runner, 'unit', interrupt):
            first = run_automatic(self.store, roots={'antigravity': source},
                                  adapters=configured(False), captured_at=TIME)
        self.assertEqual(first['state'], 'partial')
        with closing(Registry(self.store, readonly=True)) as store:
            before = store.export_bundle()
        with self.assertRaisesRegex(WorkflowError, 'adapter options changed'):
            run_automatic(self.store, roots={'antigravity': source},
                          adapters=configured(0), resume=first['run_id'])
        with closing(Registry(self.store, readonly=True)) as store:
            self.assertEqual(store.export_bundle(), before)
        resumed = run_automatic(self.store, roots={'antigravity': source},
                                adapters=configured(False), resume=first['run_id'])
        self.assertEqual(resumed['state'], 'completed')
        self.assertEqual(sorted(o['included'] for o in resumed['outputs']), [False, True])
        self.assertEqual(resumed['plan'], first['plan'])

    def test_resume_retains_original_reporter_and_excludes_current_reporter(self):
        source = self.sources['antigravity']
        for identity, modified in (('ordinary', 2000000000), ('new-reporter', 1000000000)):
            path = source / identity / '.system_generated/logs/transcript_full.jsonl'
            path.parent.mkdir(parents=True)
            path.write_bytes(source_fixture('antigravity'))
            os.utime(path, (modified, modified))
        original_unit, calls = Runner.unit, [0]
        def interrupt(runner, *args, **kwargs):
            if args[1].startswith('source:'):
                calls[0] += 1
            if calls[0] > 1:
                return {'status': 'budget_exhausted', 'reason': 'authored_interruption'}
            return original_unit(runner, *args, **kwargs)
        roots = {'antigravity': source}
        with patch.dict(os.environ, {'CODEX_THREAD_ID': 'SAMPLE'}), patch.object(Runner, 'unit', interrupt):
            first = run_automatic(self.store, roots=roots, captured_at=TIME)
        self.assertEqual(first['state'], 'partial')
        with patch.dict(os.environ, {'CODEX_THREAD_ID': 'new-reporter'}):
            resumed = run_automatic(self.store, roots=roots, resume=first['run_id'])
        self.assertEqual(resumed['state'], 'completed')
        self.assertEqual(resumed['plan'], first['plan'])
        self.assertIn('sample', resumed['plan']['reporting_session_exclusions'])
        self.assertEqual(sorted(o['included'] for o in resumed['outputs']), [False, False, True])
        store = Registry(self.store, readonly=True)
        self.addCleanup(store.close)
        self.assertEqual(len(store.heads(kind='case')), 1)

    def test_historical_report_cannot_move_committed_cutoff_backward(self):
        first = run_automatic(self.store, roots=self.sources, captured_at=TIME)
        later_time = '2026-01-03T00:00:00Z'
        second = run_automatic(self.store, roots=self.sources, captured_at=later_time)
        self.assertEqual(second['state'], 'completed')
        historical = run_automatic(self.store, roots=self.sources, resume=first['run_id'])
        self.assertEqual(historical['plan'], first['plan'])
        with closing(Registry(self.store)) as store:
            state = json.loads(store.db.execute(
                "SELECT value FROM metadata WHERE key='automatic_state'").fetchone()[0])
        self.assertEqual(state['last_committed_cutoff'], later_time)
        next_run = run_automatic(self.store, roots=self.sources,
                                 captured_at='2026-01-04T00:00:00Z')
        self.assertEqual(next_run['plan']['window']['start'], later_time)

    def test_report_commit_merges_attention_state_published_during_rendering(self):
        from test_eval_workflow_sources import jsonl
        source = self.root / 'interleaved-source'
        source.mkdir()
        prompt = {'type': 'user', 'timestamp': TIME, 'promptSource': 'typed',
                  'message': {'role': 'user', 'content': 'Review the supplied patch.'}}
        (source / 'first.jsonl').write_bytes(jsonl([prompt]))
        original = os.replace
        nested = []

        def interleave(src, dst):
            if Path(dst).name == 'latest.json' and not nested:
                nested.append(None)
                (source / 'second.jsonl').write_bytes(jsonl([prompt]))
                nested[0] = run_automatic(self.store, roots={'claude': source},
                                          captured_at='2026-01-03T00:00:00Z')
            return original(src, dst)

        with patch('retro_eval.workflow.service.os.replace', side_effect=interleave):
            first = run_automatic(self.store, roots={'claude': source}, captured_at=TIME)
        self.assertEqual(len(first['cards']), 1)
        self.assertEqual(len(nested[0]['cards']), 2)
        with closing(Registry(self.store)) as store:
            state = json.loads(store.db.execute(
                "SELECT value FROM metadata WHERE key='automatic_state'").fetchone()[0])
        self.assertEqual(set(state['seen_candidates']), {c['key'] for c in nested[0]['cards']})
        self.assertEqual(state['last_committed_cutoff'], '2026-01-03T00:00:00Z')

    def test_review_partial_resume_publishes_new_receipt(self):
        original = Runner.unit
        calls = [0]
        def interrupt_between_units(runner, *args, **kwargs):
            if args[1].startswith('source:'):
                calls[0] += 1
            if calls[0] > 1:
                return {'status': 'budget_exhausted', 'reason': 'authored_interruption'}
            return original(runner, *args, **kwargs)
        with patch.object(Runner, 'unit', interrupt_between_units):
            first = run_automatic(self.store, roots=self.sources, captured_at=TIME)
        self.assertEqual(first['state'], 'partial')
        self.assertEqual(sum(v['included'] for v in first['coverage'].values()), 1)
        second = run_automatic(self.store, roots=self.sources, resume=first['run_id'])
        self.assertEqual(second['state'], 'completed')
        self.assertEqual(sum(v['included'] for v in second['coverage'].values()), 3)
        self.assertNotEqual(first['report_path'], second['report_path'])
        self.assertEqual(json.loads(Path(first['report_path']).read_text())['state'], 'partial')
        store = Registry(self.store)
        try:
            state = json.loads(store.db.execute("SELECT value FROM metadata WHERE key='automatic_state'").fetchone()[0])
            self.assertEqual(state['last_committed_cutoff'], TIME)
        finally:
            store.close()

    def test_command_and_packet(self):
        args = ['run']
        for name, root in self.sources.items():
            args += ['--source', name + '=' + str(root)]
        run = self.command(*args)
        self.assertIn(run.returncode, (0, 1), run.stderr)
        self.assertNotIn('Please perform', run.stdout)
        status = self.command('status')
        self.assertEqual(status.returncode, 0, status.stderr)
        packet = self.command('packet')
        self.assertEqual(len(json.loads(packet.stdout)['cases']), 3)

    def test_registered_extension_runs_through_service_without_dispatch_edit(self):
        seen = []
        def custom(context):
            seen.append(context['source'])
            return {'status': 'completed', 'candidates': [],
                    'pack': 'copied-template', 'pack_version': 'stale'}
        registry = PackRegistry([Pack('custom', '1', custom)])
        result = run_automatic(self.store, roots=self.sources, captured_at=TIME,
                               packs=registry, enabled_packs=['custom'])
        self.assertEqual(set(seen), {'claude', 'codex', 'antigravity'})
        self.assertEqual(result['plan']['pack_versions'], {'custom': '1'})
        store = Registry(self.store)
        try:
            operations = Runner(store).status(result['run_id'])['operations']
            for operation in operations:
                if not operation['unit_key'].startswith('source:'):
                    continue
                output = json.loads(store.read_artifact(operation['result']['artifact_ref'])[0])
                self.assertEqual(output['packs'][0]['pack'], 'custom')
                self.assertEqual(output['packs'][0]['pack_version'], '1')
                self.assertEqual(output['packs'][0]['status'], 'completed')
        finally:
            store.close()

    def test_review_one_file_budget_leaves_room_for_actual_capture(self):
        result = run_automatic(self.store, roots={'codex': self.sources['codex']},
            captured_at=TIME, limits=Limits(source_files=1))
        self.assertEqual(result['coverage']['codex']['included'], 1)
        self.assertEqual(result['used']['source_files'], 1)

    def test_expired_planning_retains_observed_reads_without_admitting_capture(self):
        from retro_eval.workflow.planning import inventory_sources
        now = [100]
        def inventory(*args, **kwargs):
            value = inventory_sources(*args, **kwargs)
            now[0] = 102
            return value
        with patch('retro_eval.workflow.service.inventory_sources', side_effect=inventory):
            result = run_automatic(self.store, roots={'codex': self.sources['codex']},
                captured_at=TIME, limits=Limits(runtime_seconds=1), clock=lambda: now[0])
        self.assertEqual(result['used']['source_files'], 1)
        self.assertGreater(result['used']['captured_bytes'], 0)
        store = Registry(self.store)
        try:
            status = Runner(store).status(result['run_id'])
            self.assertEqual(status['deadline'], 101)
            self.assertEqual(store.heads(kind='case'), [])
            self.assertEqual(next(o for o in status['operations'] if o['unit_key'] == 'planning')['state'], 'completed')
        finally:
            store.close()

    def test_tiny_derived_budget_does_not_publish_unaccounted_plan(self):
        result = run_automatic(self.store, roots={'codex': self.sources['codex']},
            captured_at=TIME, limits=Limits(derived_bytes=1))
        self.assertEqual(result['exit_code'], 2)
        self.assertIsNone(result['report_path'])
        self.assertEqual(result['used']['derived_bytes'], 0)
        self.assertGreater(result['used']['captured_bytes'], 0)
        store = Registry(self.store)
        try:
            self.assertEqual(store.heads(kind='plan'), [])
            status = Runner(store).status(result['run_id'])
            self.assertEqual(status['receipt']['reason'], 'planning_output_exceeds_budget')
            ref = status['operations'][0]['result']['evidence_refs'][0]
            self.assertTrue(store.read_artifact(ref)[0])
        finally:
            store.close()

    def test_review_failed_pack_keeps_raw_capture_but_publishes_no_cases(self):
        def broken(context):
            raise WorkflowError('authored pack failure')
        result = run_automatic(self.store, roots={'claude': self.sources['claude']},
            captured_at=TIME, packs=PackRegistry([Pack('broken', '1', broken)]), enabled_packs=['broken'])
        store = Registry(self.store)
        try:
            self.assertEqual(store.heads(kind='case'), [])
            operations = Runner(store).status(result['run_id'])['operations']
            failed = next(o for o in operations if o['unit_key'].startswith('source:'))
            self.assertEqual(failed['state'], 'failed')
            self.assertTrue(failed['result']['capture_complete'])
            data, cls = store.read_artifact(failed['result']['raw_ref'])
            self.assertEqual(cls, 'private_raw')
            self.assertEqual(data, source_fixture('claude'))
        finally:
            store.close()

    def test_review_growing_capture_is_charged_and_retained_as_partial(self):
        from retro_eval.workflow.sources import capture as read_capture
        def grew(path, root, limit, **kwargs):
            with Path(path).open('ab') as handle:
                handle.write(b'changed after inventory\n')
            return read_capture(path, root, limit, **kwargs)
        with patch('retro_eval.workflow.service.capture', side_effect=grew):
            result = run_automatic(self.store, roots={'claude': self.sources['claude']}, captured_at=TIME)
        expected = len(source_fixture('claude')) + 1
        self.assertEqual(result['used']['captured_bytes'], expected)
        store = Registry(self.store)
        try:
            failed = next(o for o in Runner(store).status(result['run_id'])['operations']
                          if o['unit_key'].startswith('source:'))
            self.assertFalse(failed['result']['capture_complete'])
            self.assertEqual(len(store.read_artifact(failed['result']['raw_ref'])[0]), expected)
            self.assertEqual(store.heads(kind='case'), [])
        finally:
            store.close()

    def test_review_publication_crash_rolls_back_cases_and_completion_together(self):
        original = Registry._link
        def crash(store, record):
            original(store, record)
            if record.ref.kind == 'case':
                raise RuntimeError('authored precommit interruption')
        with patch.object(Registry, '_link', crash), self.assertRaises(RuntimeError):
            run_automatic(self.store, roots={'claude': self.sources['claude']}, captured_at=TIME)
        store = Registry(self.store)
        try:
            self.assertEqual(store.heads(kind='case'), [])
            run = Runner(store).list_runs()[0]
            source = next(o for o in run['operations'] if o['unit_key'].startswith('source:'))
            self.assertEqual(source['state'], 'reserved')
            self.assertIsNone(source['result'])
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()
