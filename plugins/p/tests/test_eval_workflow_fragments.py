"""Discovery fragments preserve bytes and gaps without inventing a session."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.errors import PolicyDenied, WorkflowError
from retro_eval.workflow.fragments import capture_fragment, write_fragment, MAX_BYTES

PLUGIN = Path(__file__).resolve().parents[1]


class FragmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.file = self.source / 'session.jsonl'
        self.original = b'first\nrepeat\nrepeat\nlast\n'
        self.file.write_bytes(self.original)

    def capture(self, **kwargs):
        return capture_fragment(self.source, 'session.jsonl', **kwargs)

    def test_identical_windows_have_distinct_recoverable_source_positions(self):
        first, a = self.capture(offset=6, max_bytes=7)
        second, b = self.capture(offset=13, max_bytes=7)
        self.assertEqual(first, second)
        self.assertEqual(a['sha256'], b['sha256'])
        self.assertEqual((a['offset'], a['end_offset_exclusive']), (6, 13))
        self.assertEqual((b['offset'], b['end_offset_exclusive']), (13, 20))
        for data, receipt in ((first, a), (second, b)):
            self.assertEqual(data, self.original[receipt['offset']:receipt['end_offset_exclusive']])
            self.assertEqual(receipt['bytes_read'], 7)
            self.assertFalse(receipt['covers_observed_file'])
            self.assertEqual(receipt['scope'], 'discovery_only_not_session_normalization')

    def test_tail_exact_full_file_and_clipping(self):
        tail, receipt = self.capture(tail=True, max_bytes=5)
        self.assertEqual(tail, b'last\n')
        self.assertEqual(receipt['omitted_before'], len(self.original) - 5)
        self.assertEqual(receipt['omitted_after_observed_start_size'], 0)
        for limit in (len(self.original), len(self.original) + 10):
            with self.subTest(limit=limit):
                data, receipt = self.capture(tail=True, max_bytes=limit)
                self.assertEqual(data, self.original)
                self.assertTrue(receipt['covers_observed_file'])
                self.assertFalse(receipt['short_read'])
        data, receipt = self.capture(offset=len(self.original), max_bytes=1)
        self.assertEqual(data, b'')
        self.assertFalse(receipt['covers_observed_file'])

    def test_empty_source_and_mid_character_bytes_are_not_reinterpreted(self):
        self.file.write_bytes(b'')
        data, receipt = self.capture(offset=0, max_bytes=1)
        self.assertEqual(data, b'')
        self.assertTrue(receipt['covers_observed_file'])
        self.file.write_bytes('a€z\n'.encode())
        data, receipt = self.capture(offset=2, max_bytes=1)
        self.assertEqual(data, b'\x82')
        self.assertEqual(receipt['sha256'], hashlib.sha256(data).hexdigest())
        self.assertNotIn('records', receipt)

    def test_invalid_selections_fail_before_read(self):
        cases = [dict(offset=-1, max_bytes=1), dict(offset=True, max_bytes=1),
                 dict(offset=0, max_bytes=0), dict(offset=0, max_bytes=MAX_BYTES + 1),
                 dict(offset=0, max_bytes=True), dict(max_bytes=1),
                 dict(offset=0, tail=True, max_bytes=1)]
        with patch('retro_eval.workflow.fragments.os.open') as opened:
            for case in cases:
                with self.subTest(case=case), self.assertRaises(WorkflowError):
                    self.capture(**case)
            opened.assert_not_called()
        with self.assertRaises(WorkflowError):
            self.capture(offset=len(self.original) + 1, max_bytes=1)

    def test_containment_and_nonregular_sources(self):
        for relative in ('../outside', str(self.file), '.'):
            with self.subTest(relative=relative), self.assertRaises(PolicyDenied):
                capture_fragment(self.source, relative, offset=0, max_bytes=1)
        outside = self.root / 'outside'
        outside.write_bytes(b'outside')
        (self.source / 'escape').symlink_to(outside)
        with self.assertRaises(PolicyDenied):
            capture_fragment(self.source, 'escape', offset=0, max_bytes=1)
        (self.source / 'alias').symlink_to(self.file)
        data, receipt = capture_fragment(self.source, 'alias', offset=0, max_bytes=5)
        self.assertEqual(data, b'first')
        self.assertEqual(receipt['resolved_relative_path'], 'session.jsonl')
        if hasattr(os, 'mkfifo'):
            os.mkfifo(self.source / 'pipe')
            with self.assertRaises(PolicyDenied):
                capture_fragment(self.source, 'pipe', offset=0, max_bytes=1)

    def test_intermediate_symlink_replacement_is_rejected_before_read(self):
        nested = self.source / 'nested'
        nested.mkdir()
        (nested / 'record').write_bytes(b'authorized')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'record').write_bytes(b'unauthorized')
        original_open = os.open
        swapped = []
        def racing_open(path, flags, *args, **kwargs):
            if not swapped:
                nested.rename(self.source / 'retained')
                nested.symlink_to(outside, target_is_directory=True)
                swapped.append(True)
            return original_open(path, flags, *args, **kwargs)
        with patch('retro_eval.workflow.fragments.os.open', side_effect=racing_open):
            with self.assertRaises((PolicyDenied, OSError)):
                capture_fragment(self.source, 'nested/record', offset=0, max_bytes=20)
        self.assertTrue(swapped)

    def test_unsupported_anchored_open_fails_closed(self):
        with patch('retro_eval.workflow.sources.ANCHORED_OPEN_SUPPORTED', False):
            with patch('retro_eval.workflow.fragments.os.open') as opened:
                with self.assertRaises(PolicyDenied):
                    self.capture(offset=0, max_bytes=1)
                opened.assert_not_called()

    def test_append_and_path_replacement_are_visible(self):
        original_fstat = os.fstat
        for mode in ('append', 'replace', 'remove'):
            self.file.write_bytes(self.original)
            calls = []
            def observe(fd):
                calls.append(fd)
                # Shared opening checks file type, then capture observes its
                # initial and final metadata. Mutate at the final observation.
                if len(calls) == 3:
                    if mode == 'append':
                        with self.file.open('ab') as handle:
                            handle.write(b'new\n')
                    elif mode == 'replace':
                        replacement = self.source / 'replacement'
                        replacement.write_bytes(b'changed')
                        replacement.replace(self.file)
                    else:
                        self.file.unlink()
                return original_fstat(fd)
            with self.subTest(mode=mode), patch('retro_eval.workflow.fragments.os.fstat', side_effect=observe):
                data, receipt = self.capture(offset=0, max_bytes=len(self.original))
            self.assertEqual(data, self.original)
            self.assertFalse(receipt['source_metadata_unchanged'])
            self.assertFalse(receipt['covers_observed_file'])

    def test_private_publication_and_existing_output(self):
        output = self.root / 'fragment'
        result = write_fragment(self.source, 'session.jsonl', output, tail=True, max_bytes=5)
        receipt = json.loads(Path(result['capture_path']).read_bytes())
        self.assertEqual((output / receipt['artifact']).read_bytes(), b'last\n')
        self.assertEqual(receipt['sha256'], result['sha256'])
        self.assertFalse((output / 'capture.pending.json').exists())
        if os.name != 'nt':
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertEqual((output / 'fragment.bin').stat().st_mode & 0o777, 0o600)
        before = {p.name: p.read_bytes() for p in output.iterdir()}
        with self.assertRaises(PolicyDenied):
            write_fragment(self.source, 'session.jsonl', output, tail=True, max_bytes=5)
        self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})

    def test_output_refuses_repository_source_store_and_symlink(self):
        repo = self.root / 'repo'
        repo.mkdir()
        (repo / '.git').mkdir()
        store = self.root / 'store'
        link = self.root / 'alias'
        link.symlink_to(self.root / 'not-created')
        for output in (repo / 'fragment', self.source / 'fragment', store / 'fragment', link):
            with self.subTest(output=output), self.assertRaises(PolicyDenied):
                write_fragment(self.source, 'session.jsonl', output, offset=0,
                               max_bytes=5, protected_paths=(store,))
        self.assertFalse((self.root / 'not-created').exists())

    def test_failed_publication_has_no_completion_receipt_and_never_overwrites(self):
        output = self.root / 'interrupted'
        with patch('retro_eval.workflow.fragments.os.link', side_effect=OSError('authored publication failure')):
            with self.assertRaises(OSError):
                write_fragment(self.source, 'session.jsonl', output, tail=True, max_bytes=5)
        self.assertFalse((output / 'capture.json').exists())
        self.assertEqual((output / 'fragment.bin').read_bytes(), b'last\n')
        with self.assertRaises(PolicyDenied):
            write_fragment(self.source, 'session.jsonl', output, tail=True, max_bytes=5)

    def test_shipped_cli_needs_no_store_and_flags_partial_capture(self):
        store = self.root / 'absent-store'
        command = [sys.executable, '-B', str(PLUGIN / 'bin/retro-eval-workflow'),
                   '--store', str(store), 'fragment', '--source-root', str(self.source),
                   '--file', 'session.jsonl', '--tail']
        partial = subprocess.run(command + ['--max-bytes', '5', '--output', str(self.root / 'partial')],
                                 capture_output=True, text=True)
        self.assertEqual(partial.returncode, 1, partial.stderr)
        self.assertFalse(json.loads(partial.stdout)['covers_observed_file'])
        full = subprocess.run(command + ['--output', str(self.root / 'full')], capture_output=True, text=True)
        self.assertEqual(full.returncode, 0, full.stderr)
        self.assertTrue(json.loads(full.stdout)['covers_observed_file'])
        self.assertFalse(store.exists())
        self.assertNotIn('repeat', partial.stdout)


if __name__ == '__main__':
    unittest.main()
