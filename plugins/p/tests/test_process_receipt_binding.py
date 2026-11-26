"""A cleanup receipt must describe the owner the caller intends to recover."""
import copy
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.investigation.process_barrier import MODEL, VERSION, OwnedProcess, successor_allowed


class ReceiptTests(unittest.TestCase):
    def receipt(self):
        return {'version': VERSION, 'model': MODEL, 'owner_id': 'a' * 32,
                'verified': True, 'ownership_confirmed': True,
                'launch_succeeded': True, 'running_after': {}, 'errors': [],
                'group': 100, 'before': {'100': 'Ss', '101': 'S'}, 'after': {},
                'target': {'phase': 'exited', 'pid': 101, 'returncode': 0}}

    def test_current_identity_and_consistent_evidence(self):
        r = self.receipt()
        self.assertTrue(successor_allowed(r, expected_owner='a' * 32))
        r['after'] = {'101': 'Z'}
        self.assertTrue(successor_allowed(r, expected_owner='a' * 32))
        r['target'] = {'phase': 'running', 'pid': 101}
        self.assertTrue(successor_allowed(r, expected_owner='a' * 32))

    def test_wrong_missing_and_legacy_identity_block(self):
        r = self.receipt()
        self.assertFalse(successor_allowed(r))
        for identity in (None, '', 'b' * 32, 1, [], 'a' * 31):
            self.assertFalse(successor_allowed(r, expected_owner=identity))
        for change in ({'owner_id': None}, {'version': 1}, {'version': True}):
            self.assertFalse(successor_allowed({**r, **change}, expected_owner='a' * 32))

    def test_incomplete_and_contradictory_evidence_blocks(self):
        base = self.receipt()
        for field in base:
            r = copy.deepcopy(base); del r[field]
            with self.subTest(missing=field):
                self.assertFalse(successor_allowed(r, expected_owner='a' * 32))
        for change in (
            {'after': {'101': 'S'}}, {'after': {'101': ''}}, {'after': []},
            {'after': {'-1': 'Z'}}, {'after': {'0': 'Z'}},
            {'after': {'١': 'Z'}}, {'after': {'101': None}},
            {'before': {}}, {'before': {'100': 'Z'}}, {'group': True},
            {'group': 102}, {'errors': ['observation failed']},
            {'running_after': {'101': 'S'}}, {'launch_succeeded': False},
            {'target': {'phase': 'launch_failed', 'error': 'synthetic'}},
            {'target': {'phase': 'exited', 'pid': 101}},
            {'target': {'phase': 'exited', 'pid': 101, 'returncode': True}},
            {'target': {'phase': 'running', 'pid': 101, 'returncode': 0}},
            {'target': {'phase': 'running', 'pid': True}},
        ):
            with self.subTest(change=change):
                self.assertFalse(successor_allowed({**base, **change}, expected_owner='a' * 32))


@unittest.skipUnless(os.name == 'posix', 'POSIX adapter')
class ControllerTests(unittest.TestCase):
    def test_previous_receipt_cannot_start_successor_for_live_owner(self):
        # This caller retains expected identity from its own process object;
        # substituting a previous receipt must not produce a successor effect.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with OwnedProcess([sys.executable, '-c', 'pass'], root / 'previous') as previous:
                self.assertEqual(previous.wait(timeout=5)['phase'], 'exited')
            ready, stop = root / 'ready', root / 'stop'
            worker = ('from pathlib import Path; import time\n'
                      'Path(' + repr(str(ready)) + ').write_text("ready")\n'
                      'end=time.monotonic()+10\n'
                      'while time.monotonic()<end and not Path(' + repr(str(stop)) + ').exists(): time.sleep(.01)')
            current = OwnedProcess([sys.executable, '-c', worker], root / 'current').start()
            try:
                end = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < end: time.sleep(.01)
                self.assertTrue(ready.exists())
                self.assertIsNone(current.proc.poll())
                self.assertNotEqual(previous.owner_id, current.owner_id)
                effect = root / 'successor'
                def dispatch(receipt):
                    if successor_allowed(receipt, expected_owner=current.owner_id):
                        effect.write_text('successor ran')
                dispatch(previous.receipt)
                self.assertFalse(effect.exists())
                current.cleanup()
                dispatch(current.receipt)
                self.assertTrue(effect.exists(), current.receipt)
            finally:
                stop.write_text('stop')
                if current.proc.poll() is None:
                    self.assertEqual(os.getpgid(current.proc.pid), current.proc.pid)
                    os.killpg(current.proc.pid, signal.SIGKILL)
                    current.proc.wait(timeout=5)
