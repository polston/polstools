"""Custom finding-to-pilot path through shipped commands and immutable records."""
import copy
from contextlib import redirect_stdout
import io
import json
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.errors import StoreBusy, WorkflowError
from retro_eval.workflow.inspection import inspect_run
from retro_eval.workflow.pilot_followup import capture_evidence, register, review, followups
from retro_eval.workflow.records import Record, RecordRef, canonical, digest, strict_loads
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.service import run_automatic
from test_eval_workflow_sources import jsonl
from test_eval_workflow_records import TIME

PLUGIN = Path(__file__).resolve().parents[1]
TEMPLATES = PLUGIN / 'skills/reviewing-improvement-effects/references'


def template(name):
    return strict_loads((TEMPLATES / ('pilot-' + name + '-template.json')).read_bytes())


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source'
        source.mkdir()
        (source / 'session.jsonl').write_bytes(jsonl([
            {'type': 'user', 'timestamp': TIME, 'promptSource': 'typed', 'message': {'role': 'user', 'content': 'Review the supplied patch.'}},
            {'type': 'assistant', 'timestamp': TIME, 'message': {'role': 'assistant', 'content': 'I will inspect the patch.'}}
        ]))
        self.run = run_automatic(self.root / 'store', roots={'claude': source}, captured_at=TIME)
        self.store = Registry(self.root / 'store')
        self.addCleanup(self.store.close)
        inspected = inspect_run(self.store)
        self.packet = strict_loads(Path(inspected['inspection_path']).read_bytes())
        entry = next(e for e in self.packet['entries'] if e['candidate']['category'] == 'review_request')
        self.entry = entry
        spec = template('registration')
        spec.update(inspection_ref=inspected['artifact_ref'], candidate_key=entry['candidate']['key'])
        spec['justification'].update(interpretation='A review procedure can be compared on this authored patch.',
            rationale='The review request identifies the intended consumer, not proof of a defect.',
            scope='This authored review exercise only.', counterevidence_search='The request alone does not show missed defects.',
            possible_harm='Extra checking effort without improved defect detection.', evidence_refs=entry['candidate']['evidence_refs'])
        composition = self.store.publish_artifact(b'Authored fixed baseline and candidate procedure specification.', 'private_raw')
        p = spec['pilot']
        for key, value in list(p.items()):
            if value is None:
                p[key] = 'authored_' + key
        p.update(intervention={'description': 'Run the candidate review checklist.', 'evidence_refs': [composition]},
            comparator={'description': 'Run the baseline checklist.', 'evidence_refs': [composition]},
            units=[{'unit': 'patch-a', 'case_ref': entry['case_refs'][0], 'split_group': entry['candidate']['split_group']}],
            split_groups=[entry['candidate']['split_group']], planned_sample=1,
            checkpoints=['Account for the frozen patch before any extension.'],
            quality_guardrails=['No harmful fix.'], direction='higher', practical_threshold=1,
            analysis_method='authored-descriptive', analysis_version='1',
            resource_budget={'units': 1, 'external_calls': 0})
        self.spec = spec

    def command(self, *args):
        return subprocess.run([sys.executable, '-B', str(PLUGIN / 'bin/retro-eval-workflow'),
            '--store', str(self.root / 'store'), *args], capture_output=True, text=True)

    def test_malformed_group_lists_are_actionable_input_errors_without_records(self):
        before = self.store.export_bundle()
        for field in ('discovery_groups', 'split_groups'):
            for value in (None, 'one-group', [{}], ['']):
                with self.subTest(field=field, value=value):
                    spec = copy.deepcopy(self.spec)
                    spec['pilot'][field] = value
                    result = self.command('pilot', 'register', '--spec',
                                          self.write('bad-groups.json', spec))
                    self.assertEqual(result.returncode, 2)
                    self.assertIn('error: ' + field, result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertEqual(self.store.export_bundle(), before)

    def test_atomic_import_cannot_mix_review_and_pilot_views(self):
        from retro_eval.workflow.cli import main
        pilot = register(self.store, self.spec)
        review(self.store, self.review_spec(pilot))
        bundle = self.store.export_bundle()
        for public_status in (False, True):
            with self.subTest(public_status=public_status):
                path = self.root / ('target-' + str(public_status))
                writer = Registry(path, timeout=0.01)
                reader = Registry(path, readonly=True)
                original = reader.heads
                attempts = []

                def interleave(kind=None, study_scope=None):
                    rows = original(kind, study_scope)
                    if kind == 'decision' and not attempts:
                        try:
                            writer.import_bundle(bundle)
                        except StoreBusy:
                            attempts.append('commit_blocked')
                        except sqlite3.OperationalError as exc:
                            # System SQLite can report lock refusal as IOERR.
                            # The unchanged view and later successful import
                            # below verify the contract independently of it.
                            self.assertEqual(str(exc), 'disk I/O error')
                            attempts.append('commit_blocked')
                        else:
                            attempts.append('committed')
                    return rows

                try:
                    with patch.object(reader, 'heads', side_effect=interleave):
                        if public_status:
                            output = io.StringIO()
                            with patch('retro_eval.workflow.cli.Registry', return_value=reader), redirect_stdout(output):
                                self.assertEqual(main(['--store', str(path), 'status']), 0)
                            rows = json.loads(output.getvalue())['pilot_followups']
                        else:
                            rows = followups(reader)
                    self.assertEqual(attempts, ['commit_blocked'])
                    self.assertEqual(rows, [])
                    writer.import_bundle(bundle)
                    rows = followups(writer)
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]['disposition'], 'insufficient_evidence')
                    self.assertEqual(writer.db.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
                finally:
                    reader.close()
                    writer.close()

    def write(self, name, value):
        path = self.root / name
        path.write_bytes(canonical(value))
        return str(path)

    def review_spec(self, pilot):
        spec = template('review')
        spec['pilot_ref'] = pilot['pilot_ref']
        a = spec['analysis']
        a.update(method='authored-descriptive', method_version='1', code_version='authored-checker-v1',
            results=[{'unit': 'patch-a', 'status': 'pending', 'reason': 'Execution not yet observed.', 'evidence_refs': []}],
            guardrails=[{'guardrail': 'No harmful fix.', 'status': 'unknown', 'reason': 'Not yet checked.', 'evidence_refs': []}],
            limitations=['Authored exercise; no causal productivity claim.'])
        for key in ('activation', 'comparison', 'benefit'):
            a[key]['reason'] = 'Not yet observed.'
        spec['decision'].update(rationale='Keep missing observations explicit.', due_at='2026-01-02T00:00:00Z',
            next_action={'owner': 'agent', 'action': 'Run the authorized comparison.',
                'prerequisites': 'Frozen patch and existing execution authority.',
                'acceptance': 'Retain output, exit and guardrail observations.'})
        return spec

    def observed_review(self, pilot):
        spec = self.review_spec(pilot)
        evidence = self.store.publish_artifact(canonical({'baseline': 0, 'intervention': 1,
            'executed': True, 'harmful_fixes': 0}), 'private_raw')
        a = spec['analysis']
        a['results'][0].update(status='observed', reason='Authored comparison outputs retained.', evidence_refs=[evidence])
        for name, status in (('activation', 'observed'), ('comparison', 'matched'), ('benefit', 'supported')):
            a[name].update(status=status, reason='Authored fixture judgment only.', evidence_refs=[evidence])
        a['guardrails'][0].update(status='preserved', reason='Authored check found no harmful fix.', evidence_refs=[evidence])
        spec['decision'].update(disposition='keep', benefit_status='supported', quality_status='preserved',
            evidence_refs=[evidence], rationale='Keep only within this authored comparison.')
        return spec

    def test_public_cli_registration_review_revision_status_and_portability(self):
        registered = self.command('pilot', 'register', '--spec', self.write('registration.json', self.spec))
        self.assertEqual(registered.returncode, 0, registered.stderr)
        pilot = json.loads(registered.stdout)
        self.assertEqual(pilot['benefit_status'], 'unknown')
        self.assertEqual(register(self.store, self.spec)['pilot_ref'], pilot['pilot_ref'])
        pending = self.review_spec(pilot)
        first = self.command('pilot', 'review', '--spec', self.write('pending.json', pending))
        self.assertEqual(first.returncode, 0, first.stderr)
        first = json.loads(first.stdout)
        self.assertEqual(first['disposition'], 'insufficient_evidence')
        self.assertEqual(review(self.store, pending)['decision_ref'], first['decision_ref'])
        completed = self.observed_review(pilot)
        completed['expected_revision'] = 1
        second = self.command('pilot', 'review', '--spec', self.write('complete.json', completed))
        self.assertEqual(second.returncode, 0, second.stderr)
        second = json.loads(second.stdout)
        self.assertEqual(second['decision_ref']['revision'], 2)
        self.assertEqual(second['disposition'], 'keep')
        self.assertEqual(second['judgment_basis'], 'attributed_review_not_automatic_effect_verification')
        self.assertFalse(second['global_workflow_changed'])
        self.assertEqual(self.store.get(RecordRef.from_dict(first['decision_ref'])).payload['disposition'], 'insufficient_evidence')
        status = self.command('status')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)['pilot_followups'][0]['decision_ref'], second['decision_ref'])
        copied = Registry(self.root / 'copy')
        self.addCleanup(copied.close)
        copied.import_bundle(self.store.export_bundle())
        self.assertEqual(followups(copied), followups(self.store))

    def test_due_custom_review_reaches_automatic_report_once_per_revision(self):
        pilot = register(self.store, self.spec)
        spec = self.review_spec(pilot)
        first = review(self.store, spec)
        def run(at, **kwargs):
            return run_automatic(self.root / 'store', roots={'claude': self.root / 'source'},
                                 captured_at=at, **kwargs)
        early = run(TIME)
        self.assertEqual(early['plan']['due_followups'], [])
        due = run('2026-01-02T00:00:00Z')
        cards = [c for c in due['cards'] if c['category'] == 'due_followup']
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['decision_ref'], first['decision_ref'])
        self.assertEqual(cards[0]['candidate_key'], self.entry['candidate']['key'])
        self.assertEqual(cards[0]['prerequisites'], spec['decision']['next_action']['prerequisites'])
        self.assertIn(first['decision_ref'], due['plan']['decision_refs'])
        quiet = run('2026-01-03T00:00:00Z')
        self.assertFalse(any(c['category'] == 'due_followup' for c in quiet['cards']))
        spec['expected_revision'] = 1
        spec['decision']['rationale'] = 'Comparison remains pending after the first reminder.'
        second = review(self.store, spec)
        revised = run('2026-01-04T00:00:00Z')
        self.assertEqual([c['decision_ref'] for c in revised['cards']
                          if c['category'] == 'due_followup'], [second['decision_ref']])
        resumed = run('2026-01-04T00:00:00Z', resume=due['run_id'])
        self.assertEqual(resumed['cards'], due['cards'])
        self.assertFalse(resumed['result_applicability']['current'])
        self.assertIn('reconciles changed dependencies', resumed['next_action'])

    def test_status_routes_missing_dependency_to_reconciliation_without_rewriting_decision(self):
        pilot = register(self.store, self.spec)
        spec = self.review_spec(pilot)
        reviewed = review(self.store, spec)
        ref = RecordRef.from_dict(reviewed['decision_ref'])
        original_decision = self.store.get(ref).encoded
        before = followups(self.store)
        evidence = self.spec['pilot']['intervention']['evidence_refs'][0]
        path = self.store.artifact_path(evidence['sha256'])
        data = path.read_bytes()
        path.unlink()
        try:
            result = self.command('status')
            self.assertEqual(result.returncode, 0, result.stderr)
            followup = json.loads(result.stdout)['pilot_followups'][0]
            self.assertFalse(followup['current_applicability']['current'])
            self.assertIn('Reconcile', followup['next_action']['action'])
            self.assertEqual(followup['recorded_next_action'], spec['decision']['next_action'])
            self.assertEqual(followup['disposition'], before[0]['disposition'])
            self.assertEqual(self.store.get(ref).encoded, original_decision)
        finally:
            path.write_bytes(data)
        self.assertEqual(followups(self.store), before)

    def test_public_due_review_preserves_stale_inputs_and_undisplayed_backlog(self):
        from retro_eval.workflow.recurring import disposition
        from retro_eval.workflow.runner import Limits
        pilot = register(self.store, self.spec)
        reviewed = review(self.store, self.review_spec(pilot))
        disposition(self.store, 'earlier-attention', 'snoozed', reason='Earlier due action.',
                    due_at=TIME)
        limited = run_automatic(self.root / 'store', roots={'claude': self.root / 'source'},
                                captured_at='2026-01-02T00:00:00Z', limits=Limits(cards=1))
        self.assertEqual(limited['cards'][0]['candidate_key'], 'earlier-attention')
        resumed = run_automatic(self.root / 'store', roots={'claude': self.root / 'source'},
                                resume=limited['run_id'])
        self.assertEqual(resumed['cards'], limited['cards'])
        old = self.store.get(RecordRef.from_dict(self.entry['case_refs'][0]))
        self.store.append(Record.create('case', old.ref.key, old.payload,
                          study_scope=old.ref.study_scope, revision=2), 1)
        result = self.command('run', '--source', 'claude=' + str(self.root / 'source'))
        # No in-window source is required to show an already registered due action.
        self.assertNotIn('Traceback', result.stderr)
        receipt = json.loads(result.stdout)
        report = strict_loads(Path(receipt['report_path']).read_bytes())
        cards = [c for c in report['cards'] if c['category'] == 'due_followup']
        self.assertEqual([c['decision_ref'] for c in cards], [reviewed['decision_ref']])
        self.assertFalse(report['result_applicability']['current'])
        self.assertIn('reconciles changed dependencies', receipt['next_action'])
        self.assertEqual(len(self.store.heads(kind='decision', study_scope='candidate-pilots')), 1)

    def test_explicit_capture_checks_digest_limit_privacy_and_no_execution(self):
        path = self.root / 'procedure.txt'
        data = b'An inert proposed procedure; no command is run.'
        path.write_bytes(data)
        result = self.command('evidence', str(path), '--sha256', digest(data))
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertFalse(receipt['executed'])
        self.assertEqual(receipt['artifact_ref']['content_class'], 'private_raw')
        self.assertEqual(self.store.read_artifact(receipt['artifact_ref'])[0], data)
        for kwargs in ({'sha256': '0' * 64}, {'sha256': digest(data), 'max_bytes': 1}):
            with self.assertRaises(WorkflowError):
                capture_evidence(self.store, path, **kwargs)
        repo = self.root / 'repo'
        repo.mkdir()
        (repo / '.git').mkdir()
        private = repo / 'input'
        private.write_bytes(data)
        with self.assertRaises(WorkflowError):
            capture_evidence(self.store, private, digest(data))

    def test_substituted_candidate_and_invalid_evidence_do_not_register(self):
        forged = copy.deepcopy(self.packet)
        entry = next(e for e in forged['entries'] if e['candidate']['key'] == self.spec['candidate_key'])
        entry['candidate']['interpretation'] = 'Substituted unsupported finding.'
        spec = copy.deepcopy(self.spec)
        spec['inspection_ref'] = self.store.publish_artifact(canonical(forged), 'private_redacted')
        with self.assertRaisesRegex(WorkflowError, 'frozen source'):
            register(self.store, spec)
        spec = copy.deepcopy(self.spec)
        spec['justification']['evidence_refs'][0]['selector']['pointer'] = '/absent'
        with self.assertRaisesRegex(WorkflowError, 'unresolved'):
            register(self.store, spec)
        self.assertEqual(self.store.heads(kind='pilot'), [])

    def test_selection_cannot_drop_displayed_source_dependencies(self):
        selection = self.store.get(RecordRef.from_dict(self.packet['selection_ref']))
        payload = selection.payload
        payload['selected_refs'] = []
        unrelated = Record.create('selection', 'unrelated-empty', payload,
                                  study_scope='inspections')
        self.store.append(unrelated, None)
        packet = copy.deepcopy(self.packet)
        packet['selection_ref'] = unrelated.ref.to_dict()
        spec = copy.deepcopy(self.spec)
        spec['inspection_ref'] = self.store.publish_artifact(canonical(packet), 'private_redacted')
        with self.assertRaisesRegex(WorkflowError, 'discovery selection'):
            register(self.store, spec)
        self.assertEqual(self.store.heads(kind='pilot'), [])

    def test_independent_assignment_keeps_discovery_source_currentness(self):
        from test_eval_workflow_records import case
        independent = case('independent-assignment')
        self.store.append(independent, None)
        spec = copy.deepcopy(self.spec)
        spec['pilot']['units'][0].update(case_ref=independent.ref.to_dict(),
                                       split_group=independent.payload['split_group'])
        spec['pilot']['split_groups'] = [independent.payload['split_group']]
        pilot = register(self.store, spec)
        original = self.store.get(RecordRef.from_dict(self.entry['case_refs'][0]))
        updated = Record.create('case', original.ref.key, original.payload,
                                study_scope=original.ref.study_scope, revision=2)
        self.store.append(updated, 1)
        followup = followups(self.store)[0]
        self.assertFalse(followup['current_applicability']['current'])
        self.assertIn('Reconcile', followup['next_action']['action'])
        self.assertIn('Perform the registered comparison', followup['recorded_next_action']['action'])
        with self.assertRaisesRegex(WorkflowError, 'stale'):
            review(self.store, self.review_spec(pilot))
        self.assertEqual(self.store.heads(kind='analysis'), [])

    def test_displayed_contrast_binds_discovery_and_currentness_after_transfer(self):
        from retro_eval.workflow.pilots import _discovery_groups
        source = self.root / 'source'
        (source / 'contrast.jsonl').write_bytes(jsonl([
            {'type': 'user', 'timestamp': TIME, 'promptSource': 'typed',
             'message': {'role': 'user', 'content': 'List the colors in this supplied palette.'}},
            {'type': 'assistant', 'timestamp': TIME,
             'message': {'role': 'assistant', 'content': 'Blue and green.'}}
        ]))
        run = run_automatic(self.root / 'store', roots={'claude': source}, captured_at=TIME)
        inspected = inspect_run(self.store, run_id=run['run_id'])
        packet = strict_loads(Path(inspected['inspection_path']).read_bytes())
        self.assertEqual(len(packet['noncandidate_contrasts']), 1)
        entry = next(e for e in packet['entries'] if e['candidate']['category'] == 'review_request')
        spec = copy.deepcopy(self.spec)
        spec.update(inspection_ref=inspected['artifact_ref'], candidate_key=entry['candidate']['key'])
        spec['justification']['evidence_refs'] = entry['candidate']['evidence_refs']
        spec['pilot']['units'][0].update(case_ref=entry['case_refs'][0],
                                       split_group=entry['candidate']['split_group'])
        spec['pilot']['split_groups'] = [entry['candidate']['split_group']]
        result = register(self.store, spec)
        pilot = self.store.get(RecordRef.from_dict(result['pilot_ref']))
        selection = self.store.get(RecordRef.from_dict(packet['selection_ref']))
        candidate_refs = {canonical(r) for e in packet['entries'] for r in e['case_refs']}
        contrast_refs = [r for r in selection.payload['selected_refs'] if canonical(r) not in candidate_refs]
        self.assertEqual(len(contrast_refs), 1)
        contrast = self.store.get(RecordRef.from_dict(contrast_refs[0]))
        self.assertIn(contrast.ref.to_dict(), pilot.payload['source_record_refs'])
        self.assertIn(contrast.payload['split_group'], pilot.payload['discovery_groups'])
        transferred = Registry(self.root / 'contrast-transfer')
        self.addCleanup(transferred.close)
        transferred.import_bundle(self.store.export_bundle())
        for store in (self.store, transferred):
            with self.subTest(store=store.root):
                self.assertIn(contrast.payload['split_group'], _discovery_groups(store))
                self.assertTrue(followups(store)[0]['current_applicability']['current'])
                store.append(Record.create('case', contrast.ref.key, contrast.payload,
                    study_scope=contrast.ref.study_scope, revision=2), 1)
                row = followups(store)[0]
                self.assertFalse(row['current_applicability']['current'])
                self.assertIn('Reconcile', row['next_action']['action'])
                before = store.export_bundle()
                with self.assertRaises(WorkflowError):
                    register(store, spec)
                self.assertEqual(store.export_bundle(), before)

    def test_substituted_contrast_or_malformed_packet_is_not_registered(self):
        for change in ('entries', 'contrasts', 'contrast-reference'):
            packet = copy.deepcopy(self.packet)
            if change == 'entries':
                packet['entries'] = [None]
            elif change == 'contrasts':
                packet['noncandidate_contrasts'] = None
            else:
                packet['noncandidate_contrasts'] = [{'source': 'claude',
                    'source_key': self.entry['candidate']['split_group'],
                    'reference': {'sha256': '0' * 64}}]
            spec = copy.deepcopy(self.spec)
            spec['inspection_ref'] = self.store.publish_artifact(canonical(packet), 'private_redacted')
            with self.subTest(change=change), self.assertRaises(WorkflowError):
                register(self.store, spec)
        self.assertEqual(self.store.heads(kind='pilot'), [])

    def test_inferred_cases_cannot_be_relabelled_as_independent_groups(self):
        spec = copy.deepcopy(self.spec)
        spec['pilot']['units'][0]['split_group'] = 'invented-independent-unit'
        with self.assertRaisesRegex(WorkflowError, 'split group'):
            register(self.store, spec)

    def test_stale_case_or_changed_artifact_prevents_registration(self):
        old = self.store.get(RecordRef.from_dict(self.entry['case_refs'][0]))
        updated = Record.create('case', old.ref.key, old.payload, study_scope=old.ref.study_scope, revision=2)
        self.store.append(updated, 1)
        with self.assertRaises(WorkflowError):
            register(self.store, self.spec)

    def test_missing_units_changed_method_and_invented_keep_are_rejected_atomically(self):
        pilot = register(self.store, self.spec)
        base = self.observed_review(pilot)
        changes = [lambda s: s['analysis'].update(results=[]),
            lambda s: s['analysis']['results'].append(copy.deepcopy(s['analysis']['results'][0])),
            lambda s: s['analysis'].update(method='changed-after-results'),
            lambda s: s['analysis']['activation'].update(status='unknown', evidence_refs=[]),
            lambda s: s['analysis']['comparison'].update(status='unknown', evidence_refs=[]),
            lambda s: s['analysis']['guardrails'][0].update(status='unknown', evidence_refs=[]),
            lambda s: s['analysis']['guardrails'][0].update(guardrail='Easier replacement condition.'),
            lambda s: s['analysis']['results'][0].update(evidence_refs=[])]
        for change in changes:
            spec = copy.deepcopy(base)
            change(spec)
            with self.subTest(change=change), self.assertRaises(WorkflowError):
                review(self.store, spec)
            self.assertEqual(self.store.heads(kind='analysis'), [])
            self.assertEqual(self.store.heads(kind='decision'), [])

    def test_pending_units_and_quality_regression_support_honest_followup(self):
        pilot = register(self.store, self.spec)
        spec = self.review_spec(pilot)
        first = review(self.store, spec)
        self.assertEqual(followups(self.store, now=TIME)[0]['due'], False)
        self.assertEqual(followups(self.store, now='2026-01-03T00:00:00Z')[0]['due'], True)
        evidence = self.store.publish_artifact(b'Authored direct guardrail failure.', 'private_raw')
        spec['expected_revision'] = 1
        spec['analysis']['guardrails'][0].update(status='regressed', reason='A harmful fix is directly evidenced.', evidence_refs=[evidence])
        spec['decision'].update(disposition='revert', quality_status='regressed', evidence_refs=[evidence])
        latest = review(self.store, spec)
        self.assertEqual(latest['disposition'], 'revert')
        self.assertEqual(latest['decision_ref']['revision'], 2)
        stale = self.review_spec(pilot)
        stale['decision']['rationale'] = 'A stale overwrite.'
        with self.assertRaisesRegex(WorkflowError, 'latest decision'):
            review(self.store, stale)
        self.assertEqual(self.store.get(RecordRef.from_dict(first['decision_ref'])).payload['disposition'], 'insufficient_evidence')

    def test_invalid_decision_does_not_leave_an_orphan_analysis(self):
        pilot = register(self.store, self.spec)
        spec = self.review_spec(pilot)
        spec['decision']['next_action']['acceptance'] = ''
        with self.assertRaises(WorkflowError):
            review(self.store, spec)
        self.assertEqual(self.store.heads(kind='analysis'), [])

    def test_publication_failure_rolls_back_analysis_and_decision_together(self):
        pilot = register(self.store, self.spec)
        original = self.store._link
        def fail(record):
            original(record)
            if record.ref.kind == 'decision':
                raise RuntimeError('Authored interruption before commit.')
        with patch.object(self.store, '_link', side_effect=fail), self.assertRaises(RuntimeError):
            review(self.store, self.review_spec(pilot))
        self.assertEqual(self.store.heads(kind='analysis'), [])
        self.assertEqual(self.store.heads(kind='decision'), [])
        self.assertEqual(review(self.store, self.review_spec(pilot))['decision_ref']['revision'], 1)

    def test_existing_inspection_packets_need_no_migration(self):
        old = copy.deepcopy(self.packet)
        for entry in old['entries']:
            entry.pop('case_refs')
        spec = copy.deepcopy(self.spec)
        spec['inspection_ref'] = self.store.publish_artifact(canonical(old), 'private_redacted')
        self.assertEqual(register(self.store, spec)['status'], 'registered')

    def test_registered_candidate_and_old_records_survive_extra_inspection_fields(self):
        pilot = register(self.store, self.spec)
        stored = self.store.get(RecordRef.from_dict(pilot['pilot_ref']))
        self.assertEqual(stored.payload['candidate'], self.entry['candidate'])
        self.assertIn(self.entry['candidate']['split_group'], stored.payload['discovery_groups'])
        self.assertTrue(followups(self.store)[0]['current_applicability']['current'])
        self.assertEqual(followups(self.store)[0]['disposition'], 'not_reviewed')
        evidence = stored.payload['intervention']['evidence_refs'][0]
        self.store.artifact_path(evidence['sha256']).write_bytes(b'changed')
        followup = followups(self.store)[0]
        self.assertFalse(followup['current_applicability']['current'])
        self.assertIn('Reconcile', followup['next_action']['action'])
        self.assertIn('Perform the registered comparison', followup['recorded_next_action']['action'])
        with self.assertRaises(WorkflowError):
            review(self.store, self.review_spec(pilot))

    def test_custom_review_exposure_blocks_installed_blind_replay_after_transfer(self):
        from retro_eval.workflow.pilots import prepare, compare, _discovery_groups
        source = self.root / 'new-source'
        path = source / 'sample/.system_generated/logs/transcript_full.jsonl'
        path.parent.mkdir(parents=True)
        path.write_bytes(jsonl([{'step_index': 0, 'type': 'USER_INPUT',
            'source': 'USER_EXPLICIT', 'created_at': '2026-01-02T00:00:00Z',
            'content': 'What time is it?'}]))
        run_automatic(self.root / 'store', roots={'antigravity': source},
                      captured_at='2026-01-02T00:00:00Z')
        installed = prepare(self.store)
        labels = strict_loads(Path(installed['label_packet']).read_bytes())
        for row in labels['units']:
            row.update(label=False, reason='An ordinary authored question.')
        unit = labels['units'][0]
        spec = copy.deepcopy(self.spec)
        spec['pilot']['units'] = [{'unit': 'patch-a', 'case_ref': unit['case_ref'],
                                  'split_group': unit['split_group']}]
        spec['pilot']['split_groups'] = [unit['split_group']]
        custom = register(self.store, spec)
        self.assertNotIn(unit['split_group'], _discovery_groups(self.store))
        # Even pending reviews are conservatively treated as exposure: their
        # aggregate judgments need not be stored in the per-unit result row.
        result = review(self.store, self.review_spec(custom))
        self.assertEqual(prepare(self.store)['status'], 'insufficient_evidence')
        analysis = self.store.get(RecordRef.from_dict(result['analysis_ref']))
        changed = analysis.payload
        changed['assigned_case_refs'] = []
        # Generic correction preserves old bytes but removes current references.
        # It must not erase what was already exposed by the original review.
        self.store.append(Record.create('analysis', analysis.ref.key, changed,
            study_scope=analysis.ref.study_scope, revision=2), 1)
        copied = Registry(self.root / 'transferred')
        self.addCleanup(copied.close)
        copied.import_bundle(self.store.export_bundle())
        for store in (self.store, copied):
            with self.subTest(store=store.root):
                before = len(store.heads(kind='analysis'))
                with self.assertRaisesRegex(WorkflowError, 'discovery exposure'):
                    compare(store, RecordRef.from_dict(installed['pilot_ref']), labels)
                self.assertEqual(len(store.heads(kind='analysis')), before)
                self.assertEqual(prepare(store)['status'], 'insufficient_evidence')


if __name__ == '__main__':
    unittest.main()
