"""Local attention remains discoverable without executing or changing decisions."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.recurring import disposition
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.records import Record
from test_eval_workflow_records import TIME, case

PLUGIN = Path(__file__).resolve().parents[1]


def snapshot(path):
    return {p.relative_to(path): p.read_bytes() for p in path.rglob('*') if p.is_file()}


class RecurringStatusTests(unittest.TestCase):
    def test_automatic_report_handles_portable_attention_without_inventing_due_cards(self):
        from retro_eval.workflow.service import run_automatic
        from test_eval_workflow_sources import source_fixture
        for field, value, omitted in [('operational_status', None, True),
                                     ('operational_status', [], False),
                                     ('candidate_key', [], False)]:
            with self.subTest(field=field, omitted=omitted), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / 'source'
                source.mkdir()
                (source / 'session.jsonl').write_bytes(source_fixture('claude'))
                with closing(Registry(root / 'store')) as store:
                    original = disposition(store, 'portable', 'active', reason='Portable input', due_at=TIME)
                    payload = dict(original.payload)
                    if omitted:
                        payload.pop(field)
                    else:
                        payload[field] = value
                    record = Record.create('decision', 'portable', payload, study_scope='recurring', revision=2)
                    store.append(record, 1)
                report = run_automatic(root / 'store', roots={'claude': source}, captured_at=TIME)
                self.assertEqual(report['state'], 'completed')
                self.assertEqual(report['exit_code'], 1)
                self.assertFalse(any(c['category'] == 'due_followup' for c in report['cards']))
                gap, = [g for g in report['gaps'] if g['reason'] == 'local_attention_metadata_unusable']
                self.assertEqual(gap['decision_ref'], record.ref.to_dict())
                self.assertIn('attention metadata', report['next_action'])
                with closing(Registry(root / 'store', readonly=True)) as store:
                    self.assertEqual(store.get(record.ref).payload, payload)
                # An explicit correction restores the normal dated review;
                # the unsupported declaration remains preserved as history.
                with closing(Registry(root / 'store')) as store:
                    corrected = Record.create('decision', 'portable', original.payload,
                                              study_scope='recurring', revision=3)
                    store.append(corrected, 2)
                repaired = run_automatic(root / 'store', roots={'claude': source}, captured_at=TIME)
                self.assertFalse(any(g['reason'] == 'local_attention_metadata_unusable'
                                     for g in repaired['gaps']))
                card, = [c for c in repaired['cards'] if c['category'] == 'due_followup']
                self.assertEqual(card['decision_ref'], corrected.ref.to_dict())

    def test_portable_key_fallback_and_invalid_state_preserve_candidate_selection(self):
        from retro_eval.workflow.recurring import select_cards
        with tempfile.TemporaryDirectory() as temp, closing(Registry(Path(temp))) as store:
            original = disposition(store, 'portable', 'dismissed', reason='Explicit suppression')
            payload = dict(original.payload)
            payload.pop('candidate_key')
            portable = Record.create('decision', 'portable', payload, study_scope='recurring', revision=2)
            candidate = {'key': 'portable', 'priority': 1}
            selection = select_cards([candidate], [portable], now=TIME)
            self.assertEqual(selection['cards'], [])
            self.assertEqual(selection['attention_diagnostics'], [])
            payload['operational_status'] = []
            invalid = Record.create('decision', 'portable', payload, study_scope='recurring', revision=3)
            selection = select_cards([candidate], [invalid], now=TIME)
            self.assertEqual(selection['cards'], [candidate])
            self.assertEqual(len(selection['attention_diagnostics']), 1)

    def test_public_status_exposes_due_review_and_reconciles_lost_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'store'
            with closing(Registry(root)) as store:
                evidence = store.publish_artifact(b'authored review evidence', 'public_synthetic')
                record = disposition(store, 'local-review', 'snoozed', reason='Wait for review',
                    due_at='2000-01-01T00:00:00Z', evidence_refs=[evidence],
                    next_action='Inspect the outcome and record a scoped decision.')
                artifact = store.artifact_path(evidence['sha256'])
                retained = artifact.read_bytes()
            def status():
                before = snapshot(root)
                result = subprocess.run([sys.executable, '-B', str(PLUGIN / 'bin/retro-eval-workflow'),
                    '--store', str(root), 'status'], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(snapshot(root), before)
                return json.loads(result.stdout)
            first = status()
            row, = first['recurring_followups']
            self.assertEqual(row['decision_ref'], record.ref.to_dict())
            self.assertEqual(row['evidence_refs'], [evidence])
            self.assertTrue(row['due'])
            self.assertEqual(row['attention_status'], 'ready_for_review')
            self.assertEqual(row['next_action'], record.payload['next_action'])
            self.assertEqual(row['benefit_status'], 'unknown')
            self.assertEqual(row['quality_status'], 'unknown')
            self.assertFalse(row['global_workflow_changed'])
            artifact.unlink()
            damaged = status()
            stale, = damaged['recurring_followups']
            self.assertFalse(damaged['current_applicability']['current'])
            self.assertFalse(stale['current_applicability']['current'])
            self.assertEqual(stale['attention_status'], 'needs_reconciliation')
            self.assertEqual(stale['recorded_next_action'], row['next_action'])
            self.assertIn('Reconcile', stale['next_action']['action'])
            self.assertEqual(stale['benefit_status'], 'unknown')
            artifact.write_bytes(retained)
            self.assertEqual(status()['recurring_followups'], first['recurring_followups'])

    def test_dates_suppression_and_undated_unknowns_are_distinct(self):
        from retro_eval.workflow.recurring import followups
        with tempfile.TemporaryDirectory() as temp, closing(Registry(Path(temp))) as store:
            for key, state, due in [('due', 'snoozed', TIME),
                    ('future', 'snoozed', '2099-01-01T00:00:00Z'),
                    ('undated', 'active', None), ('dismissed', 'dismissed', TIME),
                    ('retired', 'retired', TIME), ('adopted', 'adopted', TIME)]:
                disposition(store, key, state, reason='Authored attention state', due_at=due)
            before = snapshot(Path(temp))
            rows = {r['candidate_key']: r for r in followups(store, now=TIME)}
            self.assertEqual(rows['due']['attention_status'], 'ready_for_review')
            self.assertEqual(rows['adopted']['attention_status'], 'ready_for_review')
            self.assertEqual(rows['future']['attention_status'], 'waiting_for_due_time')
            self.assertFalse(rows['future']['due'])
            self.assertEqual(rows['undated']['attention_status'], 'trigger_unknown')
            self.assertIsNone(rows['undated']['due'])
            for key in ('dismissed', 'retired'):
                self.assertEqual(rows[key]['attention_status'], 'inactive')
            self.assertEqual(snapshot(Path(temp)), before)

    def test_superseded_dependencies_require_reconciliation_and_latest_decision_only(self):
        from retro_eval.workflow.recurring import followups
        with tempfile.TemporaryDirectory() as temp, closing(Registry(Path(temp))) as store:
            original = case()
            store.append(original, None)
            first = disposition(store, 'review', 'snoozed', reason='Original', due_at=TIME,
                evidence_refs=[original.ref.to_dict()])
            store.append(case(revision=2), 1)
            row, = followups(store, now=TIME)
            self.assertEqual(row['attention_status'], 'needs_reconciliation')
            self.assertEqual(row['decision_ref'], first.ref.to_dict())
            latest = disposition(store, 'review', 'retired', reason='Explicit later decision')
            row, = followups(store, now=TIME)
            self.assertEqual(row['decision_ref'], latest.ref.to_dict())
            self.assertEqual(row['attention_status'], 'inactive')

    def test_public_status_handles_invalid_optional_attention_metadata(self):
        malformed = [None, [], {}, True, False, 0, 1, 1.5, '', '   ', 'unrecognized']
        for field in ('operational_status', 'candidate_key'):
            for value in malformed:
                with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp) / 'store'
                    with closing(Registry(root)) as store:
                        first = disposition(store, 'portable', 'active', reason='Portable declaration',
                            due_at='2000-01-01T00:00:00Z')
                        payload = dict(first.payload)
                        # Any nonempty string is a valid candidate identifier.
                        if field == 'candidate_key' and value == 'unrecognized':
                            continue
                        payload[field] = value
                        record = Record.create('decision', 'portable', payload,
                            study_scope='recurring', revision=2)
                        store.append(record, 1)
                    before = snapshot(root)
                    command = subprocess.run([sys.executable, '-B', str(PLUGIN / 'bin/retro-eval-workflow'),
                        '--store', str(root), 'status'], capture_output=True, text=True, timeout=10)
                    self.assertEqual(command.returncode, 0, command.stderr)
                    self.assertNotIn('Traceback', command.stderr)
                    row, = json.loads(command.stdout)['recurring_followups']
                    self.assertEqual(row['decision_ref'], record.ref.to_dict())
                    self.assertEqual(row['attention_status'], 'invalid_metadata')
                    self.assertEqual(row['attention_metadata_issues'][0]['field'], field)
                    self.assertEqual(row['recorded_next_action'], first.payload['next_action'])
                    self.assertIn('metadata', row['next_action']['action'])
                    self.assertEqual(row['benefit_status'], 'unknown')
                    self.assertEqual(row['quality_status'], 'unknown')
                    self.assertTrue(row['current_applicability']['current'])
                    self.assertEqual(snapshot(root), before)
                    with closing(Registry(root, readonly=True)) as store:
                        self.assertEqual(store.get(record.ref).payload, payload)

    def test_missing_state_with_due_date_stays_unknown_and_valid_custom_key_survives(self):
        from retro_eval.workflow.recurring import followups
        with tempfile.TemporaryDirectory() as temp, closing(Registry(Path(temp))) as store:
            original = disposition(store, 'portable', 'active', reason='Portable declaration', due_at=TIME)
            payload = dict(original.payload)
            payload['candidate_key'] = 'separate-authored-candidate'
            payload.pop('operational_status')
            second = Record.create('decision', 'portable', payload, study_scope='recurring', revision=2)
            store.append(second, 1)
            row, = followups(store, now=TIME)
            self.assertEqual(row['attention_status'], 'trigger_unknown')
            self.assertEqual(row['attention_metadata_issues'], [])
            self.assertTrue(row['due'])
            self.assertEqual(row['candidate_key'], 'separate-authored-candidate')
            payload['operational_status'] = 'provisional'
            store.append(Record.create('decision', 'portable', payload, study_scope='recurring', revision=3), 2)
            row, = followups(store, now=TIME)
            self.assertEqual(row['attention_status'], 'ready_for_review')
            self.assertEqual(row['candidate_key'], 'separate-authored-candidate')

    def test_invalid_metadata_remains_visible_when_evidence_needs_reconciliation(self):
        from retro_eval.workflow.recurring import followups
        with tempfile.TemporaryDirectory() as temp, closing(Registry(Path(temp))) as store:
            evidence = store.publish_artifact(b'authored dependency', 'public_synthetic')
            original = disposition(store, 'portable', 'active', reason='Portable declaration',
                due_at=TIME, evidence_refs=[evidence])
            payload = dict(original.payload)
            payload['operational_status'] = {}
            store.append(Record.create('decision', 'portable', payload, study_scope='recurring', revision=2), 1)
            store.artifact_path(evidence['sha256']).unlink()
            row, = followups(store, now=TIME)
            self.assertEqual(row['attention_status'], 'needs_reconciliation')
            self.assertFalse(row['current_applicability']['current'])
            self.assertEqual(row['attention_metadata_issues'][0]['field'], 'operational_status')
            self.assertEqual(row['recorded_next_action'], original.payload['next_action'])

    def test_portable_decision_does_not_invent_operational_state_or_due_date(self):
        from retro_eval.workflow.recurring import followups
        with tempfile.TemporaryDirectory() as temp, closing(Registry(Path(temp))) as store:
            record = disposition(store, 'portable', 'active', reason='Portable test')
            payload = dict(record.payload)
            for key in ('operational_status', 'due_at', 'candidate_key', 'global_workflow_changed', 'scope'):
                payload.pop(key)
            store.append(Record.create('decision', 'portable', payload, study_scope='recurring', revision=2), 1)
            row, = followups(store, now=TIME)
            self.assertEqual(row['operational_status'], 'unknown')
            self.assertEqual(row['attention_status'], 'trigger_unknown')
            self.assertIsNone(row['due'])
            self.assertEqual(row['benefit_status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
