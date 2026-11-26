import sys
import contextlib
import io
import sqlite3
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.records import Record
from retro_eval.workflow.packets import excerpt, make_packet, render_html, resource_summary
from retro_eval.workflow.errors import WorkflowError, StoreBusy
from test_eval_workflow_records import case


class PacketTests(unittest.TestCase):
    def test_packet_and_cli_preserve_one_committed_view(self):
        from retro_eval.workflow.cli import main
        for public_command in (False, True):
            with self.subTest(public_command=public_command), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'store'
                writer = Registry(path, timeout=0.02)
                cases = [case('one'), case('two')]
                writer.import_records(cases)
                reader = Registry(path, readonly=True)
                calls, attempts = [], []
                def publish():
                    try:
                        writer.import_records([case('one', revision=2), case('two', revision=2)])
                        attempts.append('committed')
                    except StoreBusy:
                        attempts.append('blocked')
                    except sqlite3.OperationalError as exc:
                        self.assertEqual(str(exc), 'disk I/O error')
                        attempts.append('blocked')
                try:
                    if public_command:
                        original = reader.heads
                        def interleave(kind=None, study_scope=None):
                            result = original(kind, study_scope)
                            if kind == 'case':
                                publish()
                            return result
                        stream = io.StringIO()
                        with patch('retro_eval.workflow.cli.Registry', return_value=reader), \
                                patch.object(reader, 'heads', side_effect=interleave), \
                                contextlib.redirect_stdout(stream):
                            self.assertEqual(main(['--store', str(path), 'packet']), 0)
                        packet = json.loads(stream.getvalue())
                    else:
                        original = reader.applicability
                        def interleave(refs):
                            result = original(refs)
                            calls.append(True)
                            if len(calls) == 1:
                                publish()
                            return result
                        with patch.object(reader, 'applicability', side_effect=interleave):
                            packet = make_packet(reader, cases)
                    self.assertEqual([c['current_applicability']['current']
                                      for c in packet['cases']], [True, True])
                    self.assertEqual(attempts, ['blocked'])
                    self.assertEqual([c['case_ref']['revision'] for c in packet['cases']], [1, 1])
                    writer.import_records([case('one', revision=2), case('two', revision=2)])
                    self.assertEqual([r.ref.revision for r in writer.heads('case')], [2, 2])
                    self.assertEqual(writer.db.execute('PRAGMA integrity_check').fetchone(), ('ok',))
                finally:
                    reader.close()
                    writer.close()

    def test_F19_invalid_bounds(self):
        for value in (0, -1, True, 2.5):
            with self.assertRaises(WorkflowError):
                excerpt('example', value)

    def test_F20_exact_and_F21_multibyte_bounds(self):
        self.assertEqual(excerpt('abcd', 4), {'text': 'abcd', 'displayed_chars': 4,
                                            'available_chars': 4, 'truncated': False})
        self.assertEqual(excerpt('Aé中B', 3), {'text': 'Aé中', 'displayed_chars': 3,
                                           'available_chars': 4, 'truncated': True})

    def test_F22_case_cap_and_escaping(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Registry(Path(temp))
            try:
                cases = [case(k) for k in ('c', 'a', 'b')]
                value = cases[1].to_dict()
                value['payload']['intent'] = '<script>alert(1)</script>'
                cases[1] = Record.from_dict(value)
                store.import_records(cases)
                packet = make_packet(store, cases, case_limit=2)
                self.assertEqual([r['case_ref']['key'] for r in packet['cases']], ['a', 'b'])
                self.assertEqual((packet['selected'], packet['omitted']), (3, 1))
                self.assertFalse(packet['population_claim'])
                page = render_html(packet)
                self.assertNotIn('<script>', page)
                self.assertIn('&lt;script&gt;', page)
            finally:
                store.close()

    def test_review_all_free_text_is_redacted_and_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Registry(Path(temp))
            try:
                value = case().to_dict()
                marker = 'authored' + '@' + 'example.invalid'
                value['payload']['contract']['criteria'][0]['description'] = marker
                value['payload']['contract']['criteria'][0]['verification_method'] = marker
                value['payload']['limitations'] = [marker]
                record = Record.from_dict(value)
                store.append(record, None)
                packet = make_packet(store, [record])
                self.assertNotIn(marker, json.dumps(packet))
                self.assertIn('<email>', json.dumps(packet))
            finally:
                store.close()

    def test_F25_zero_deliveries_has_no_finite_ratio(self):
        self.assertEqual(resource_summary(12, 0), {'total': 12, 'verified_deliveries': 0,
            'per_delivery': None, 'reason': 'zero_verified_deliveries'})

    def test_nonfinite_resources_cannot_be_reported_as_observations(self):
        for total in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(total=total), self.assertRaises(WorkflowError):
                resource_summary(total, 1)


if __name__ == '__main__':
    unittest.main()
