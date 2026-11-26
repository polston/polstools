"""Authored transport shapes distinguish metadata from missing context."""
from contextlib import closing
import copy
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.sources import normalize_captured
from retro_eval.workflow.record_coverage import summarize_record_coverage
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.service import run_automatic, _interpretation_gaps
from test_eval_workflow_sources import source_fixture, jsonl


def accounting():
    usage = {'input_tokens': 12, 'output_tokens': 4, 'total_tokens': 16}
    return {'type': 'token_usage_record', 'payload': {
        **{key: 'authored-' + key for key in
           ('response_id', 'root_turn_id', 'session_id', 'thread_id', 'turn_id')},
        **{key: dict(usage) for key in ('usage', 'turn_token_usage', 'thread_token_usage')}}}


def reminder():
    return {'type': 'attachment', 'attachment': {
        'type': 'total_tokens_reminder', 'text': '<total_tokens>100 tokens left</total_tokens>'}}


class RecordCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Registry(self.root / 'store')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def read(self, source, extra, *, usage=True, **kwargs):
        raw = source_fixture(source, usage=usage) + jsonl(extra)
        captured = normalize_captured(source, 'sample/transcript.jsonl', raw, self.store, **kwargs)
        self.assertEqual(self.store.read_artifact(captured.raw_ref)[0], raw)
        return captured

    def test_metadata_preserves_traces_excerpts_and_accounting(self):
        samples = {'codex': [accounting()], 'claude': [
            {'type': 'ai-title', 'aiTitle': 'Example title', 'sessionId': 'authored'},
            {'type': 'mode', 'mode': 'normal', 'sessionId': 'authored'},
            {'type': 'permission-mode', 'permissionMode': 'default', 'sessionId': 'authored'},
            {'type': 'atis-latch', 'atis': '', 'sessionId': 'authored'}, reminder()]}
        for source, extra in samples.items():
            before = self.read(source, [])
            after = self.read(source, extra)
            self.assertEqual(before.records, after.records)
            self.assertEqual(before.excerpts, after.excerpts)
            self.assertEqual(after.diagnostics, ())
            self.assertEqual(after.observations['input_tokens']['value'], 10)
            classification = after.observations['record_coverage']
            self.assertEqual(classification['value']['routine_metadata_records'], len(extra))
            for entry in classification['entries']:
                self.assertEqual(entry['raw_ref']['sha256'], after.raw_ref['sha256'])
                self.assertEqual(entry['raw_ref']['selector']['line'], entry['line'])

    def test_recognized_accounting_is_not_a_new_usage_source(self):
        captured = self.read('codex', [accounting()], usage=False)
        self.assertEqual(captured.diagnostics, ())
        self.assertEqual(captured.observations['input_tokens']['availability'], 'not_observed')
        self.assertIsNone(captured.observations['input_tokens']['value'])

    def test_accounting_unknown_or_malformed_fields_stay_gaps(self):
        variants = []
        row = accounting(); row['payload']['usage']['input_tokens'] = True; variants.append(row)
        row = accounting(); row['payload']['usage']['output_tokens'] = -1; variants.append(row)
        row = accounting(); row['payload']['usage']['new_metric'] = 3; variants.append(row)
        row = accounting(); row['payload']['instructions'] = 'Do another task.'; variants.append(row)
        row = accounting(); row['content'] = 'A consequential message.'; variants.append(row)
        row = accounting(); row['payload']['response_id'] = None; variants.append(row)
        row = accounting(); row['ordinal'] = False; variants.append(row)
        row = accounting(); row['timestamp'] = {}; variants.append(row)
        row = accounting(); del row['payload']['thread_token_usage']; variants.append(row)
        variants.extend({'type': 'token_usage_record', 'payload': p} for p in (None, [], '', 0))
        for row in variants:
            with self.subTest(row=row):
                captured = self.read('codex', [row])
                self.assertEqual([d['reason'] for d in captured.diagnostics], ['invalid_metadata_record'])
                self.assertTrue(_interpretation_gaps([captured.summary()]))

    def test_title_and_latch_are_closed_shapes(self):
        for row in ({'type': 'ai-title', 'aiTitle': 4, 'sessionId': 'authored'},
                    {'type': 'ai-title', 'aiTitle': 'Title', 'sessionId': 'authored', 'content': 'Extra'},
                    {'type': 'atis-latch', 'atis': 'New message', 'sessionId': 'authored'},
                    {'type': 'mode', 'mode': {}, 'sessionId': 'authored'}):
            self.assertEqual(self.read('claude', [row]).diagnostics[0]['reason'], 'invalid_metadata_record')

    def test_reminder_rendering_cannot_hide_additional_context(self):
        row = reminder()
        row['rendered'] = [{'content': '<system-reminder>\n' + row['attachment']['text'] + '\n</system-reminder>'}]
        self.assertEqual(self.read('claude', [row]).diagnostics, ())
        variants = []
        altered = copy.deepcopy(row); altered['rendered'].append({'content': 'New user request'}); variants.append(altered)
        altered = reminder(); altered['attachment']['text'] += '\nNew instructions'; variants.append(altered)
        altered = reminder(); altered['attachment']['extra'] = 'Context'; variants.append(altered)
        altered = reminder(); altered['content'] = 'Context'; variants.append(altered)
        altered = reminder(); altered['cwd'] = {'instructions': 'Context'}; variants.append(altered)
        altered = reminder(); altered['isSidechain'] = 'false'; variants.append(altered)
        for altered in variants:
            self.assertEqual(self.read('claude', [altered]).diagnostics[0]['reason'], 'invalid_metadata_record')

    def test_context_has_raw_selectors_and_never_becomes_a_user_turn(self):
        samples = {'codex': [{'type': kind, 'payload': {'text': 'Summarized user request'}}
                             for kind in ('world_state', 'compacted')],
                   'claude': [{'type': 'attachment', 'attachment': {'type': kind,
                               'content': 'Preserved contextual evidence', 'humanTurn': True}}
                              for kind in ('instructions', 'queued_command', 'hook_success', 'future_attachment')],
                   'antigravity': [{'type': kind, 'step_index': i + 10, 'source': 'SYSTEM',
                                    'content': 'Command finished. User asked for a check.'}
                                   for i, kind in enumerate(('SYSTEM_MESSAGE', 'CHECKPOINT'))]}
        for source, rows in samples.items():
            before, after = self.read(source, []), self.read(source, rows)
            self.assertEqual(before.records, after.records)
            self.assertEqual(before.excerpts, after.excerpts)
            self.assertEqual(len(after.diagnostics), len(rows))
            for diagnostic in after.diagnostics:
                self.assertEqual(diagnostic['reason'], 'unmapped_context_record')
                selector = diagnostic['raw_ref']['selector']
                raw = self.store.read_artifact(diagnostic['raw_ref'])[0]
                selected = json.loads(raw.splitlines()[selector['line'] - 1])
                for part in selector['pointer'].split('/')[1:]:
                    selected = selected[part]
                self.assertTrue(selected)

    def test_missing_or_invalid_context_envelopes_stay_gaps(self):
        for source, row in [('codex', {'type': 'world_state'}),
                            ('codex', {'type': 'compacted', 'payload': []}),
                            ('claude', {'type': 'attachment', 'attachment': None})]:
            self.assertEqual(self.read(source, [row]).diagnostics[0]['reason'], 'invalid_record_payload')

    def test_unknown_types_are_not_metadata(self):
        for source in ('claude', 'codex', 'antigravity'):
            captured = self.read(source, [{'type': 'future_record'}])
            self.assertEqual(captured.diagnostics[0]['reason'], 'unmapped_record_type')
            self.assertEqual(captured.observations['record_coverage']['value']['routine_metadata_records'], 0)

    def test_future_context_is_not_an_in_window_gap(self):
        row = {'type': 'world_state', 'timestamp': '2099-01-01T00:00:00Z', 'payload': {'text': 'Future'}}
        captured = self.read('codex', [row], window_end='2026-01-02T00:00:00Z')
        self.assertEqual(_interpretation_gaps([captured.summary()]), [])
        self.assertEqual(captured.observations['record_coverage']['entries'], [])

    def test_historical_missing_classification_is_unknown_not_zero(self):
        self.assertEqual(summarize_record_coverage([{'observations': {}}]),
                         {'mapped_sources': 0, 'unmapped_sources': 1, 'counts': None})
        historical = {'source': 'codex', 'source_key': 'old',
                      'diagnostics': [{'line': 1, 'reason': 'unmapped_record_type'}]}
        self.assertEqual(_interpretation_gaps([historical])[0]['diagnostic_counts'],
                         {'unmapped_record_type': 1})

    def test_frozen_warning_is_not_reclassified_on_resume(self):
        source = self.root / 'historical-source'
        source.mkdir()
        (source / 'rollout-example.jsonl').write_bytes(source_fixture('codex') + jsonl([accounting()]))
        # Reconstruct an older source operation with an unmapped-type warning
        # and no usable classification. Its capture is immutable on inspection.
        with patch('retro_eval.workflow.sources.classify_record', return_value=None), \
                patch('retro_eval.workflow.sources.assess_records', return_value=([], {'availability': 'not_supported', 'value': None})):
            old = run_automatic(self.root / 'historical', roots={'codex': source},
                                captured_at='2026-01-02T00:00:00Z')
        self.assertEqual(old['state'], 'partial')
        before = Path(old['report_path']).read_bytes()
        with patch('retro_eval.workflow.service.normalize_captured', side_effect=AssertionError('reparsed')):
            resumed = run_automatic(self.root / 'historical', roots={'codex': source}, resume=old['run_id'])
        self.assertEqual((resumed['state'], resumed['exit_code']), ('partial', 1))
        self.assertEqual(resumed['outputs'], old['outputs'])
        self.assertEqual(resumed['coverage']['codex']['record_classification']['counts'], None)
        self.assertEqual(Path(old['report_path']).read_bytes(), before)
        fresh = run_automatic(self.root / 'fresh', roots={'codex': source}, captured_at='2026-01-02T00:00:00Z')
        self.assertEqual((fresh['state'], fresh['exit_code']), ('completed', 0))

    def test_registered_transport_without_rules_reports_unknown_coverage(self):
        from retro_eval.adapters.registry import AdapterRegistry, AdapterRegistration
        from retro_eval.adapters.claude import ClaudeAdapter

        class AuthoredAdapter(ClaudeAdapter):
            source = 'authored_transport'

            def __init__(self, salt):
                super().__init__(salt, direct_prompt_sources=['typed'], capabilities={})

        adapters = AdapterRegistry([AdapterRegistration(
            'authored_transport', lambda root: root.glob('*.jsonl'),
            factory=lambda salt, options: AuthoredAdapter(salt))])
        source = self.root / 'custom-source'
        source.mkdir()
        (source / 'sample.jsonl').write_bytes(jsonl([
            {'type': 'user', 'timestamp': '2026-01-01T00:00:00Z',
             'promptSource': 'typed', 'message': {'role': 'user', 'content': 'Inspect this example.'}},
            {'type': 'future_context', 'content': 'Unmapped task context'}]))
        result = run_automatic(self.root / 'custom-store', roots={'authored_transport': source},
                               captured_at='2026-01-02T00:00:00Z', adapters=adapters)
        report = json.loads(Path(result['report_path']).read_text())
        self.assertEqual(report['coverage']['authored_transport']['record_classification'],
                         {'mapped_sources': 0, 'unmapped_sources': 1, 'counts': None})
        observation = report['outputs'][0]['observations']['record_coverage']
        self.assertEqual(observation['availability'], 'not_supported')
        self.assertIsNone(observation['value'])

    def test_public_reports_separate_routine_metadata_from_context_gaps(self):
        source = self.root / 'source'
        source.mkdir()
        path = source / 'rollout-example.jsonl'
        path.write_bytes(source_fixture('codex') + jsonl([accounting()]))
        clean = run_automatic(self.root / 'clean', roots={'codex': source}, captured_at='2026-01-02T00:00:00Z')
        self.assertEqual((clean['state'], clean['exit_code']), ('completed', 0))
        self.assertEqual(clean['coverage']['codex']['record_classification']['counts']['routine_metadata_records'], 1)
        before = Path(clean['report_path']).read_bytes()
        path.write_bytes(path.read_bytes() + jsonl([{'type': 'world_state', 'payload': {'text': 'Instructions'}}]))
        partial = run_automatic(self.root / 'partial', roots={'codex': source}, captured_at='2026-01-02T00:00:00Z')
        self.assertEqual((partial['state'], partial['exit_code']), ('partial', 1))
        counts = partial['coverage']['codex']['record_classification']['counts']
        self.assertEqual((counts['routine_metadata_records'], counts['context_gap_records']), (1, 1))
        with closing(Registry(self.root / 'partial')) as store:
            state = json.loads(store.db.execute("SELECT value FROM metadata WHERE key='automatic_state'").fetchone()[0])
        self.assertNotIn('last_committed_cutoff', state)
        self.assertEqual(Path(clean['report_path']).read_bytes(), before)
