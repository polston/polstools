import sys
import errno
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.planning import plan_run, cadence, inventory_sources
from retro_eval.workflow.records import canonical
from retro_eval.adapters.registry import AdapterRegistry, default_registry
from test_eval_workflow_records import TIME


class PlanningTests(unittest.TestCase):
    def inventory(self):
        return {'sources': [{'source': n, 'available': True, 'reason': '', 'truncated': False}
                            for n in ('claude', 'codex', 'antigravity')],
                'files': [{'source': name, 'key': name + str(i), 'bytes': 100, 'mtime_ns': i,
                           'relative_path': str(i) + '.jsonl'}
                          for name, count in [('claude', 150), ('codex', 50), ('antigravity', 2)]
                          for i in reversed(range(count))]}

    def test_A17_automatic_bounded_plan_no_chosen_parameters(self):
        plan = plan_run(self.inventory(), captured_at=TIME)
        self.assertEqual(plan['limits']['runtime_seconds'], 600)
        self.assertEqual(plan['limits']['provider_calls'], 0)
        self.assertEqual(plan['limits']['human_annotations'], 0)
        self.assertLessEqual(len(plan['selected_files']), 100)
        self.assertEqual({f['source'] for f in plan['selected_files']}, {'claude', 'codex', 'antigravity'})
        self.assertTrue(any(f['selection_mode'] == 'exploration' for f in plan['selected_files']))
        self.assertEqual(len({f['key'] for f in plan['selected_files']}), len(plan['selected_files']))

    def test_A18_same_inputs_same_plan_before_grading(self):
        inventory = self.inventory()
        self.assertEqual(canonical(plan_run(inventory, captured_at=TIME)),
                         canonical(plan_run(inventory, captured_at=TIME)))
        self.assertTrue(plan_run(inventory, captured_at=TIME)['planning_inputs_frozen_before_grading'])

    def test_A19_empty_evidence_honest_gap_no_scope_expansion(self):
        inventory = {'sources': [{'source': 'missing', 'available': False,
                                  'reason': 'source_root_unavailable', 'truncated': False}], 'files': []}
        plan = plan_run(inventory, captured_at=TIME)
        self.assertEqual(plan['selected_files'], [])
        self.assertEqual(plan['next_action'], 'report_coverage_gap_and_retry_on_new_evidence')
        self.assertIsNone(plan['subjective_preference']['value'])
        self.assertEqual(plan['limits']['provider_calls'], 0)

    def test_due_followups_prioritized_and_disabled_pack_not_planned(self):
        prior = {'followups': [
            {'key': 'ordinary', 'due_at': '2025-12-01T00:00:00Z'},
            {'key': 'severe', 'due_at': '2025-12-02T00:00:00Z', 'severe': True}]}
        plan = plan_run(self.inventory(), captured_at=TIME, prior=prior, enabled_packs=['diagnosis'])
        self.assertEqual(plan['due_followups'][0]['key'], 'severe')
        self.assertEqual([q['pack'] for q in plan['questions']], ['diagnosis'])

    def test_bounded_codex_inventory_starts_with_newest_date_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for year in ('2024', '2026'):
                directory = root / year / '01' / '01'
                directory.mkdir(parents=True)
                for i in range(3):
                    (directory / ('rollout-' + str(i) + '.jsonl')).write_text('{}\n')
            inventory = inventory_sources({'codex': root}, b'authored-salt', file_limit=2)
            self.assertTrue(all(f['relative_path'].startswith('2026/') for f in inventory['files']))
            self.assertTrue(inventory['sources'][0]['truncated'])

    def test_registered_discovery_controls_hints_and_inventory_for_native_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            allowed = root / 'selected.data'
            allowed.write_text('{}\n')
            (root / 'rollout-unselected.jsonl').write_text('{}\n')
            for name in ('claude', 'codex'):
                for selected in ((), (allowed,)):
                    with self.subTest(name=name, selected=bool(selected)):
                        calls, hinted = [], []
                        def discover(path):
                            calls.append(path)
                            return iter(selected)
                        registration = replace(default_registry().get(name), discover=discover)
                        result = inventory_sources({name: root}, b'authored-salt',
                            adapters=AdapterRegistry([registration]),
                            hint_reader=lambda source, path, source_root:
                                hinted.append(path) or {'eligibility': 'unknown'})
                        self.assertEqual(calls, [root])
                        self.assertEqual(hinted, list(selected))
                        self.assertEqual([r['relative_path'] for r in result['files']],
                                         ['selected.data'] if selected else [])
                        self.assertTrue(result['population_complete'])

    def test_registered_discovery_failure_is_a_coverage_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def denied(path):
                raise PermissionError('authored discovery denial')
            for name in ('claude', 'codex'):
                with self.subTest(name=name):
                    registration = replace(default_registry().get(name), discover=denied)
                    result = inventory_sources({name: root}, b'authored-salt',
                        adapters=AdapterRegistry([registration]))
                    self.assertFalse(result['population_complete'])
                    self.assertEqual(result['files'], [])
                    self.assertEqual(result['sources'][0]['reason'], 'source_enumeration_unreadable')

    def test_symlink_loops_preserve_readable_inventory_and_report_root_failures(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'ordinary.jsonl').write_text('{}\n')
            loop = root / 'loop.jsonl'
            try:
                loop.symlink_to(loop.name)
            except (OSError, NotImplementedError):
                self.skipTest('symbolic links unavailable')
            result = inventory_sources({'claude': root}, b'authored-salt')
            self.assertEqual([f['relative_path'] for f in result['files']], ['ordinary.jsonl'])
            self.assertFalse(result['population_complete'])
            source = result['sources'][0]
            self.assertTrue(source['available'])
            self.assertEqual(source['reason'], 'source_enumeration_unreadable')
            self.assertEqual(source['enumeration_errors'][0]['errno'], errno.ELOOP)
            self.assertEqual(source['enumeration_errors'][0]['relative_path'], loop.name)
            failed_root = inventory_sources({'claude': loop}, b'authored-salt')
            self.assertFalse(failed_root['sources'][0]['available'])
            self.assertFalse(failed_root['population_complete'])
            self.assertEqual(failed_root['sources'][0]['reason'], 'source_enumeration_unreadable')

    def test_callback_runtime_error_is_not_reclassified_as_filesystem_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            def broken(path):
                raise RuntimeError('authored callback failure')
            registration = replace(default_registry().get('claude'), discover=broken)
            with self.assertRaisesRegex(RuntimeError, 'authored callback failure'):
                inventory_sources({'claude': Path(temp)}, b'authored-salt',
                                  adapters=AdapterRegistry([registration]))

    def test_later_discovery_failure_preserves_readable_prefix_and_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            readable = root / 'source.jsonl'
            readable.write_text('{}\n')
            def discover(path):
                yield readable
                raise PermissionError('authored later failure')
            registration = replace(default_registry().get('claude'), discover=discover)
            result = inventory_sources({'claude': root}, b'authored-salt',
                adapters=AdapterRegistry([registration]))
            self.assertEqual([f['relative_path'] for f in result['files']], ['source.jsonl'])
            self.assertTrue(result['sources'][0]['available'])
            self.assertFalse(result['population_complete'])
            plan = plan_run(result, captured_at=TIME)
            self.assertEqual(len(plan['selected_files']), 1)
            self.assertEqual(plan['coverage_gaps'][0]['reason'], 'source_enumeration_unreadable')

    def test_inventory_retains_bounded_error_locators_without_exception_prose(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            def discover(path, *, on_error):
                for i in range(20):
                    on_error(PermissionError(13, 'private diagnostic prose',
                        str(path / ('nested/例-' + str(i) + '.jsonl'))))
                return iter(())
            registration = replace(default_registry().get('claude'), discover=discover)
            result = inventory_sources({'claude': root}, b'authored-salt',
                adapters=AdapterRegistry([registration]))
            source = result['sources'][0]
            self.assertEqual(source['enumeration_error_count'], 20)
            self.assertEqual(len(source['enumeration_errors']), 16)
            self.assertEqual(source['enumeration_errors_omitted'], 4)
            self.assertEqual(source['enumeration_errors'][0], {
                'exception_type': 'PermissionError', 'errno': 13,
                'relative_path': 'nested/例-0.jsonl'})
            self.assertNotIn(b'private diagnostic prose', canonical(result))
            self.assertFalse(result['population_complete'])

    def test_error_locators_do_not_escape_or_silently_truncate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            filenames = [None, str(root.parent / 'outside-secret'),
                         str(root / '../escape'), str(root / ('界' * 1400)),
                         str(root / '\udcff'), 'relative-without-root']
            def discover(path, *, on_error):
                for filename in filenames:
                    on_error(OSError(5, 'unretained text', filename))
                return iter(())
            registration = replace(default_registry().get('claude'), discover=discover)
            result = inventory_sources({'claude': root}, b'authored-salt',
                adapters=AdapterRegistry([registration]))
            errors = result['sources'][0]['enumeration_errors']
            self.assertEqual(len(errors), len(filenames))
            self.assertTrue(all(e['relative_path'] is None for e in errors))
            self.assertNotIn(b'outside-secret', canonical(result))

    def test_unreadable_directory_is_not_an_empty_complete_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'rollout-readable.jsonl'
            path.write_text('{}\n')
            def walk(directory, **options):
                options['onerror'](PermissionError('authored inaccessible directory'))
                yield str(root), [], [path.name]
            for source in ('claude', 'codex'):
                with self.subTest(source=source), patch(
                        'retro_eval.adapters.discovery.os.walk', side_effect=walk):
                    inventory = inventory_sources({source: root}, b'authored-salt')
                self.assertFalse(inventory['population_complete'])
                self.assertEqual(len(inventory['files']), 1)
                self.assertEqual(inventory['sources'][0]['reason'], 'source_enumeration_unreadable')
                plan = plan_run(inventory, captured_at=TIME)
                self.assertEqual(plan['coverage_gaps'][0]['reason'], 'source_enumeration_unreadable')

    def test_antigravity_unreadable_export_is_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            logs = root / 'sample' / '.system_generated' / 'logs'
            logs.mkdir(parents=True)
            full = logs / 'transcript_full.jsonl'
            full.write_text('{}\n')
            original = Path.stat
            def stat_path(path, *args, **kwargs):
                if path == full:
                    raise PermissionError('authored inaccessible export')
                return original(path, *args, **kwargs)
            with patch.object(Path, 'stat', stat_path):
                inventory = inventory_sources({'antigravity': root}, b'authored-salt')
            self.assertFalse(inventory['population_complete'])
            self.assertEqual(inventory['sources'][0]['reason'], 'source_enumeration_unreadable')

    def test_unreadable_header_keeps_readable_files_eligible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('rollout-readable.jsonl', 'rollout-denied.jsonl'):
                (root / name).write_text('{}\n')
            def hint(source, path, source_root):
                if path.name == 'rollout-denied.jsonl':
                    raise PermissionError('authored inaccessible header')
                return {'eligibility': 'eligible_hint'}
            inventory = inventory_sources({'codex': root}, b'authored-salt', hint_reader=hint)
            self.assertFalse(inventory['population_complete'])
            plan = plan_run(inventory, captured_at=TIME)
            self.assertEqual([f['relative_path'] for f in plan['selected_files']],
                             ['rollout-readable.jsonl'])
            self.assertEqual(plan['coverage_gaps'][0]['reason'], 'source_enumeration_unreadable')

    def test_deadline_before_first_yield_is_not_complete_empty_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'rollout-source.jsonl').write_text('{}\n')
            ticks = iter([0, 1])
            inventory = inventory_sources({'codex': root}, b'authored-salt',
                deadline=1, clock=lambda: next(ticks, 1))
            self.assertFalse(inventory['population_complete'])
            self.assertEqual(inventory['files'], [])
            self.assertTrue(inventory['sources'][0]['truncated'])
            self.assertEqual(inventory['sources'][0]['reason'], 'planning_deadline_exhausted')

    def test_unresolvable_root_does_not_abort_other_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            good = root / 'readable'
            good.mkdir()
            (good / 'rollout-readable.jsonl').write_text('{}\n')
            denied = root / 'denied'
            original = Path.resolve
            def resolve(path, *args, **kwargs):
                if path == denied:
                    raise PermissionError('authored inaccessible ancestor')
                return original(path, *args, **kwargs)
            with patch.object(Path, 'resolve', resolve):
                inventory = inventory_sources({'claude': denied, 'codex': good}, b'authored-salt')
            self.assertFalse(inventory['population_complete'])
            self.assertEqual([f['source'] for f in inventory['files']], ['codex'])
            self.assertEqual(inventory['sources'][0]['reason'], 'source_enumeration_unreadable')

    def test_cadence_prunes_burden_without_installing_schedule(self):
        result = cadence({'cadence_days': 7}, false_alerts=5, new_evidence=1)
        self.assertIsNone(result['cadence_days'])
        self.assertEqual(result['mode'], 'event_triggered')
        self.assertFalse(result['installed'])

    def test_candidate_volume_does_not_establish_periodic_value(self):
        for prior in ({}, {'cadence_days': 7}, {'cadence_days': None}):
            for candidates in (0, 1, 100):
                result = cadence(prior, new_evidence=candidates)
                self.assertIsNone(result['cadence_days'])
                self.assertEqual(result['mode'], 'event_triggered')

    def test_confirmed_yield_only_proposes_frequency_and_can_lose_it(self):
        recommendation = cadence({'cadence_days': None}, useful_confirmed=2,
                                 new_evidence=3, false_alerts=1)
        self.assertEqual(recommendation['mode'], 'periodic_candidate')
        self.assertEqual(recommendation['cadence_days'], 7)
        self.assertFalse(recommendation['installed'])
        self.assertIsNone(cadence(recommendation, new_evidence=3)['cadence_days'])
        self.assertIsNone(cadence(recommendation, useful_confirmed=2,
                                 new_evidence=3, false_alerts=3)['cadence_days'])


if __name__ == '__main__':
    unittest.main()
