"""Inspection must not create stores or contend for the writer reservation."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.errors import EvidenceError, PolicyDenied, StoreBusy, StoreRecoveryRequired, UnsupportedVersion
from retro_eval.workflow.registry import Registry
from test_eval_workflow_records import case
from test_investigation_state import frame
from retro_eval.investigation.service import Investigation

PLUGIN = Path(__file__).resolve().parents[1]


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


class ReadonlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'store ?# space'
        self.writer = Registry(self.path)
        self.addCleanup(self.writer.close)
        self.writer.append(case(), None)

    def test_read_snapshot_holds_committed_view_and_releases_after_exception(self):
        reader = Registry(self.path, readonly=True)
        self.addCleanup(reader.close)
        self.writer.db.execute('PRAGMA busy_timeout=10')
        with self.assertRaisesRegex(RuntimeError, 'abort inspection'):
            with reader.read_snapshot():
                self.assertEqual(reader.heads('case'), [case()])
                # Some system SQLite builds report lock refusal as IOERR.
                # Assert refusal and the subsequent data/release invariants;
                # production must not turn arbitrary I/O errors into retries.
                with self.assertRaises((StoreBusy, sqlite3.OperationalError)) as refused:
                    self.writer.append(case(revision=2), 1)
                if isinstance(refused.exception, sqlite3.OperationalError):
                    self.assertEqual(str(refused.exception), 'disk I/O error')
                self.assertTrue(reader.applicability([case().ref])['current'])
                self.assertTrue(reader.db.in_transaction)
                self.assertEqual(reader.heads('case'), [case()])
                raise RuntimeError('abort inspection')
        self.assertFalse(reader.db.in_transaction)
        self.writer.append(case(revision=2), 1)
        self.assertEqual(reader.heads('case'), [case(revision=2)])
        self.assertEqual(self.writer.db.execute('PRAGMA integrity_check').fetchall(), [('ok',)])

    def test_nested_inspection_never_finishes_caller_transaction(self):
        with self.writer.transaction():
            second = case(revision=2)
            self.writer._append(second, 1)
            with self.assertRaisesRegex(RuntimeError, 'nested'):
                with self.writer.read_snapshot():
                    self.assertTrue(self.writer.applicability([second.ref])['current'])
                    raise RuntimeError('nested')
            self.assertTrue(self.writer.db.in_transaction)
            self.assertEqual(self.writer.heads('case'), [second])
        self.assertEqual(self.writer.heads('case'), [second])

    def test_header_inspection_preserves_writer_lock_against_another_process(self):
        child_code = '''
import json, sqlite3, sys
db = sqlite3.connect(sys.argv[1], timeout=0.05)
try:
    db.execute("INSERT INTO metadata VALUES('contender', 'committed')")
    db.commit()
    print(json.dumps({'committed': True}))
except sqlite3.OperationalError as exc:
    print(json.dumps({'committed': False, 'error': str(exc)}))
finally:
    db.close()
'''
        def contend():
            result = subprocess.run([sys.executable, '-B', '-c', child_code,
                str(self.path / 'workflow.sqlite')], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        with self.writer.transaction():
            self.writer._append(case(revision=2), 1)
            reader = Registry(self.path, readonly=True)
            try:
                self.assertEqual(reader.heads('case'), [case()])
                refused = contend()
                self.assertFalse(refused['committed'])
                self.assertIn('locked', refused['error'])
            finally:
                reader.close()
        self.assertEqual(self.writer.heads('case'), [case(revision=2)])
        self.assertTrue(contend()['committed'])
        self.assertEqual(self.writer.db.execute(
            "SELECT value FROM metadata WHERE key='contender'").fetchone(), ('committed',))
        self.assertEqual(self.writer.db.execute('PRAGMA integrity_check').fetchall(), [('ok',)])

    def test_unavailable_header_child_fails_without_opening_sqlite(self):
        before = snapshot(self.path)
        for error in (OSError('child execution unavailable'),
                      subprocess.TimeoutExpired('header reader', 5),
                      subprocess.CalledProcessError(1, 'header reader')):
            with self.subTest(error=type(error).__name__):
                with patch('retro_eval.workflow.registry.subprocess.run', side_effect=error), \
                     patch('retro_eval.workflow.registry.sqlite3.connect') as connect:
                    with self.assertRaisesRegex(EvidenceError, 'child-process execution'):
                        Registry(self.path, readonly=True)
                    connect.assert_not_called()
        self.assertEqual(snapshot(self.path), before)
        self.writer.append(case(revision=2), 1)

    def test_crashed_writer_requires_explicit_recovery_without_reader_mutation(self):
        original = self.writer.export_bundle()
        self.writer.close()
        child = subprocess.run([sys.executable, '-B', '-c', '''
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA synchronous=FULL')
db.execute('PRAGMA cache_size=1')
db.execute('PRAGMA cache_spill=1')
db.execute('BEGIN IMMEDIATE')
db.execute("INSERT INTO metadata VALUES('uncommitted_crash_probe',?)", (b'x' * 524288,))
os._exit(73)
''', str(self.path / 'workflow.sqlite')], capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 73, child.stderr)
        before = snapshot(self.path)
        self.assertIn('workflow.sqlite-journal', before)
        with self.assertRaisesRegex(StoreRecoveryRequired, 'authorized writable access'):
            Registry(self.path, readonly=True)
        command = subprocess.run([sys.executable, '-B', str(PLUGIN / 'bin/retro-eval-workflow'),
            '--store', str(self.path), 'status'], capture_output=True, text=True, timeout=10)
        self.assertEqual(command.returncode, 2)
        self.assertIn('SQLite rollback recovery', command.stderr)
        self.assertNotIn('Traceback', command.stderr)
        self.assertEqual(snapshot(self.path), before)
        recovered = Registry(self.path)
        try:
            self.assertEqual(recovered.export_bundle(), original)
            self.assertIsNone(recovered.db.execute(
                "SELECT value FROM metadata WHERE key='uncommitted_crash_probe'").fetchone())
        finally:
            recovered.close()
        reader = Registry(self.path, readonly=True)
        try:
            self.assertEqual(reader.heads('case'), [case()])
        finally:
            reader.close()

    def test_legacy_recovery_diagnostic_requires_error_and_journal(self):
        journal = self.path / 'workflow.sqlite-journal'
        for journal_present, code, expected in (
                (True, None, StoreRecoveryRequired),
                (False, None, sqlite3.OperationalError),
                (True, 5, StoreBusy),
                (True, 10, sqlite3.OperationalError)):
            with self.subTest(journal_present=journal_present, code=code):
                if journal_present:
                    journal.write_bytes(b'authored retained journal')
                else:
                    journal.unlink(missing_ok=True)
                error = sqlite3.OperationalError('attempt to write a readonly database')
                if code is not None:
                    error.sqlite_errorcode = code
                connection = Mock()
                connection.execute.side_effect = error
                with patch('retro_eval.workflow.registry.sqlite3.connect', return_value=connection):
                    with self.assertRaises(expected):
                        Registry(self.path, readonly=True)
                connection.close.assert_called_once()
        journal.unlink()

    def test_live_spilled_writer_is_busy_not_recovery_required(self):
        self.writer.close()
        child = subprocess.Popen([sys.executable, '-B', '-c', '''
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA cache_size=1')
db.execute('PRAGMA cache_spill=1')
db.execute('BEGIN IMMEDIATE')
db.execute("INSERT INTO metadata VALUES('live_probe',?)", (b'x' * 524288,))
print('ready', flush=True)
sys.stdin.readline()
db.rollback()
db.close()
''', str(self.path / 'workflow.sqlite')], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline(), 'ready\n')
            before = snapshot(self.path)
            self.assertIn('workflow.sqlite-journal', before)
            with self.assertRaisesRegex(StoreBusy, 'active transaction'):
                Registry(self.path, readonly=True, timeout=0.01)
            result = subprocess.run([sys.executable, '-B', str(PLUGIN / 'bin/retro-eval-workflow'),
                '--store', str(self.path), 'status'], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertIn('active transaction', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertNotIn('rollback recovery', result.stderr)
            self.assertEqual(snapshot(self.path), before)
        finally:
            try:
                child.communicate('\n', timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
        self.assertEqual(child.returncode, 0)
        reader = Registry(self.path, readonly=True)
        try:
            self.assertEqual(reader.heads('case'), [case()])
            self.assertIsNone(reader.db.execute(
                "SELECT value FROM metadata WHERE key='live_probe'").fetchone())
        finally:
            reader.close()

    def test_cold_journal_does_not_require_recovery_for_reading(self):
        self.writer.close()
        child = subprocess.run([sys.executable, '-B', '-c', '''
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA cache_spill=OFF')
db.execute('BEGIN IMMEDIATE')
db.execute("INSERT INTO metadata VALUES('cold_probe', 'not committed')")
os._exit(73)
''', str(self.path / 'workflow.sqlite')], capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 73, child.stderr)
        before = snapshot(self.path)
        self.assertIn('workflow.sqlite-journal', before)
        reader = Registry(self.path, readonly=True)
        try:
            self.assertEqual(reader.heads('case'), [case()])
            self.assertIsNone(reader.db.execute(
                "SELECT value FROM metadata WHERE key='cold_probe'").fetchone())
        finally:
            reader.close()
        self.assertEqual(snapshot(self.path), before)

    def test_reader_sees_committed_state_while_writer_has_uncommitted_revision(self):
        with self.writer.transaction():
            second = case(revision=2)
            self.writer._append(second, 1)
            reader = Registry(self.path, readonly=True, timeout=0.01)
            try:
                self.assertEqual(reader.heads('case'), [case()])
                self.assertEqual(reader.export_bundle()['bundle_format'], 1)
            finally:
                reader.close()
        reader = Registry(self.path, readonly=True)
        try:
            self.assertEqual(reader.heads('case'), [second])
        finally:
            reader.close()

    def test_mutating_api_and_sql_rejected_without_filesystem_effects(self):
        before = snapshot(self.path)
        reader = Registry(self.path, readonly=True)
        try:
            for operation in (lambda: reader.directory('new-output'),
                              lambda: reader.stage_artifact(b'not published'),
                              lambda: reader.publish_artifact(b'not published', 'private_raw'),
                              lambda: reader.append(case(revision=2), 1),
                              lambda: reader.import_bundle(self.writer.export_bundle())):
                with self.subTest(operation=operation):
                    with self.assertRaises(PolicyDenied):
                        operation()
            with self.assertRaises(sqlite3.OperationalError):
                reader.db.execute("DELETE FROM records")
        finally:
            reader.close()
        self.assertEqual(snapshot(self.path), before)

    def test_missing_store_and_uninitialized_database_are_not_created_or_upgraded(self):
        missing = self.path.parent / 'missing'
        with self.assertRaises(EvidenceError):
            Registry(missing, readonly=True)
        self.assertFalse(missing.exists())
        self.writer.db.execute('PRAGMA user_version=0')
        before = snapshot(self.path)
        with self.assertRaises(UnsupportedVersion):
            Registry(self.path, readonly=True)
        self.assertEqual(snapshot(self.path), before)

    def test_closed_wal_store_is_rejected_without_creating_sidecars(self):
        path = self.path.parent / 'wal-store'
        writer = Registry(path)
        writer.db.execute('PRAGMA journal_mode=WAL')
        writer.close()
        self.assertFalse((path / 'workflow.sqlite-wal').exists())
        before = snapshot(path)
        with self.assertRaisesRegex(UnsupportedVersion, 'rollback journaling'):
            Registry(path, readonly=True)
        self.assertEqual(snapshot(path), before)

    def test_active_wal_store_is_rejected_without_altering_writer_or_sidecars(self):
        self.writer.db.execute('PRAGMA journal_mode=WAL')
        self.writer.append(case(revision=2), 1)
        before = snapshot(self.path)
        with self.assertRaisesRegex(UnsupportedVersion, 'rollback journaling'):
            Registry(self.path, readonly=True)
        self.assertEqual(snapshot(self.path), before)
        self.writer.append(case(revision=3), 2)
        self.assertEqual(self.writer.heads('case'), [case(revision=3)])

    def test_incomplete_schema_and_missing_identity_are_not_repaired_by_reader(self):
        self.writer.db.execute("DELETE FROM metadata WHERE key='id_salt'")
        self.writer.db.commit()
        with self.assertRaisesRegex(EvidenceError, 'identity material'):
            Registry(self.path, readonly=True)
        self.writer.db.execute('DROP TABLE operations')
        with self.assertRaisesRegex(EvidenceError, 'incomplete registry schema'):
            Registry(self.path, readonly=True)

    def test_public_observational_commands_work_during_writer_reservation(self):
        Investigation(self.writer, clock=lambda: 1000).begin('readable', frame(),
            deadline_ms=2000, limits={}, authority=['read'], capabilities={
                'deadline': {'mode': 'helper_enforced', 'scope': 'admission'}})
        commands = [
            ('investigation', ['checkpoint', '--store', str(self.path), '--root', 'readable'], 0),
            ('investigation', ['audit', '--store', str(self.path), '--root', 'readable'], 1),
            ('investigation', ['export', '--store', str(self.path)], 0),
            ('retro-eval-workflow', ['--store', str(self.path), 'status'], 0),
            ('retro-eval-workflow', ['--store', str(self.path), 'packet'], 0),
            ('retro-eval-workflow', ['--store', str(self.path), 'export', str(self.path.parent / 'bundle.json')], 0),
        ]
        before = snapshot(self.path)
        with self.writer.transaction():
            for program, args, expected in commands:
                with self.subTest(program=program, args=args):
                    result = subprocess.run([sys.executable, '-B', str(PLUGIN / 'bin' / program), *args],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertIsInstance(json.loads(result.stdout), dict)
        self.assertEqual(snapshot(self.path), before)

    def test_public_read_commands_do_not_initialize_missing_store(self):
        missing = self.path.parent / 'missing'
        for program, args in [
            ('investigation', ['checkpoint', '--store', str(missing), '--root', 'absent']),
            ('retro-eval-workflow', ['--store', str(missing), 'status'])]:
            result = subprocess.run([sys.executable, '-B', str(PLUGIN / 'bin' / program), *args],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn('existing workflow store required', result.stderr)
            self.assertFalse(missing.exists())
