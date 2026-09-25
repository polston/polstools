"""Evidence sampling must expose late candidates without losing context."""

import contextlib
import io
import unittest
from unittest import mock

from test_retro_extract import load_retro
import test_retro_reporting as reporting
from test_retro_reporting import base_row


class Sampling(unittest.TestCase):
    def test_all_readers_reach_late_complaints_and_keep_context(self):
        retro = load_retro()
        for harness in ('claude', 'codex', 'antigravity'):
            with self.subTest(harness=harness):
                records = []
                for i in range(7):
                    for role, body in [('assistant', f'Context café {i}. ' * 30),
                                       ('user', f'No, fix example {i}.')]:
                        if harness == 'claude':
                            rec = {'type': role, 'timestamp': str(i),
                                   'message': {'content': body}}
                        elif harness == 'codex':
                            rec = {'type': 'response_item', 'timestamp': str(i),
                                   'payload': {'type': 'message', 'role': role,
                                               'content': [{'type': 'input_text', 'text': body}]}}
                        else:
                            rec = {'type': 'PLANNER_RESPONSE' if role == 'assistant' else 'USER_INPUT',
                                   'source': 'MODEL' if role == 'assistant' else 'USER_EXPLICIT',
                                   'created_at': str(i), 'content': body}
                        records.append(rec)
                reader = 'antigravity_records' if harness == 'antigravity' else 'read_records'
                with mock.patch.object(retro, reader, return_value=records), \
                        mock.patch('pathlib.Path.is_file', return_value=True):
                    row = {'harness': harness, 'transcript': 'synthetic.jsonl'}
                    selected = retro.moments(row)
                    self.assertEqual(['0', '3', '6'], [m['at'] for m in selected])
                    self.assertTrue(selected[-1]['after'].endswith('Context café 6.'))
                    self.assertEqual('No, fix example 6.', selected[-1]['said'])
                    self.assertEqual(7, len(retro.moments(row, 20)))

    def test_boundaries_and_determinism(self):
        retro = load_retro()
        evidence = list(range(8))
        self.assertEqual([0, 3, 7], retro.sample_moments(evidence, 3))
        self.assertEqual([4], retro.sample_moments(evidence, 1))
        self.assertEqual([], retro.sample_moments([], 3))
        self.assertEqual(evidence, retro.sample_moments(evidence, 8))
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                retro.sample_moments(evidence, value)

    def test_cli_rejects_invalid_limits(self):
        retro = load_retro()
        for value in ('0', '-1', '1.5', 'many'):
            with self.subTest(value=value), mock.patch('sys.argv', ['retro', 'pack', '--moments-per-session', value]), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                retro.main()
            self.assertEqual(2, exc.exception.code)


class PackSampling(unittest.TestCase):
    setUp = reporting.Reporting.setUp
    load_with_ledger = reporting.Reporting.load_with_ledger
    run_cmd = reporting.Reporting.run_cmd

    def test_pack_reports_available_and_omitted_for_selected_sessions_only(self):
        retro = self.load_with_ledger([
            base_row(harness='codex', correction_candidates=7),
            base_row(harness='codex', transcript='unselected.jsonl', correction_candidates=1),
        ])
        evidence = [{'at': str(i), 'kind': 'correction', 'said': f'fix {i}',
                     'after': f'context {i}'} for i in range(7)]
        with mock.patch.object(retro, '_all_moments', return_value=evidence) as reader:
            self.run_cmd(retro, retro.cmd_pack, days=7, sessions=1, moments_per_session=3)
            reader.assert_called_once()
            self.assertEqual('p/s.jsonl', reader.call_args.args[0]['transcript'])
        pack = next(self.work.glob('pack-*.md')).read_text(encoding='utf-8')
        self.assertIn('3 selected / 7 available; 4 omitted; capped: yes', pack)
        self.assertIn('fix 6', pack)
        self.assertNotIn('fix 1', pack)
        with mock.patch.object(retro, '_all_moments', return_value=evidence):
            self.run_cmd(retro, retro.cmd_pack, days=7, sessions=1, moments_per_session=20)
        pack = next(self.work.glob('pack-*.md')).read_text(encoding='utf-8')
        self.assertIn('7 selected / 7 available; 0 omitted; capped: no', pack)
