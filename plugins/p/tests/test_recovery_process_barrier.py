"""Real POSIX lifecycle tests; fixtures own every signaled process group."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.investigation.process_barrier import OwnedProcess, successor_allowed


@unittest.skipUnless(os.name == 'posix', 'POSIX adapter')
class ProcessBarrierTests(unittest.TestCase):
    def test_normal_parent_exit_leaves_child_then_barrier_kills_it(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); marker=root/'child'; release=root/'release'
            child='import time; from pathlib import Path\nwhile not Path('+repr(str(release))+').exists(): time.sleep(.01)\nPath('+repr(str(marker))+').write_text("late effect"); time.sleep(10)'
            parent='import subprocess,sys; subprocess.Popen([sys.executable,"-c",'+repr(child)+'])'
            with subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)']) as unrelated:
                try:
                    with OwnedProcess([sys.executable,'-c',parent],root/'control') as owned:
                        self.assertEqual(owned.wait(timeout=5)['phase'],'exited')
                        self.assertFalse(successor_allowed(None))
                    self.assertTrue(successor_allowed(owned.receipt, expected_owner=owned.owner_id),owned.receipt)
                    self.assertGreaterEqual(len(owned.receipt['before']),2)
                    release.write_text('attempt the effect after cleanup')
                    time.sleep(.15)
                    self.assertFalse(marker.exists())
                    self.assertIsNone(unrelated.poll())
                finally:
                    unrelated.terminate()

    def test_gate_timeout_exception_and_launch_failure(self):
        for mode in ('gate','timeout','exception','launch_failure'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                command=[sys.executable,'-c','import time; time.sleep(20)']
                if mode=='launch_failure':command=['/no-such-owned-test-command']
                owned=OwnedProcess(command,Path(d)/'control')
                try:
                    with owned:
                        # Test lifecycle outcomes after launch, independently of
                        # interpreter startup latency on a loaded host.
                        if mode in ('timeout', 'exception'):
                            started = owned.wait(timeout=5, stop_when=lambda:
                                                 owned.observe()['phase'] == 'running')
                            self.assertEqual(started['phase'], 'interrupted')
                        if mode=='exception':
                            owned.wait(timeout=.15)
                            raise RuntimeError('synthetic controller failure')
                        r=owned.wait(timeout=.15 if mode=='timeout' else 5,stop_when=(lambda:owned.observe()['phase']=='running') if mode=='gate' else None)
                        self.assertEqual(r['phase'],{'gate':'interrupted','timeout':'timeout','launch_failure':'launch_failed'}[mode])
                except RuntimeError:
                    if mode!='exception':raise
                self.assertTrue(owned.receipt['verified'],owned.receipt)
                self.assertEqual(successor_allowed(owned.receipt, expected_owner=owned.owner_id),mode!='launch_failure')
                self.assertEqual(owned.cleanup(),owned.receipt)

    def test_startup_timeout_does_not_authorize_successor(self):
        from retro_eval.investigation import process_barrier
        with tempfile.TemporaryDirectory() as directory, patch.object(
                process_barrier, '_ANCHOR',
                'import time; time.sleep(20)\n' + process_barrier._ANCHOR):
            owned = OwnedProcess([sys.executable, '-c', 'pass'],
                                 Path(directory) / 'control')
            with owned:
                self.assertEqual(owned.wait(timeout=.15)['phase'], 'timeout')
            self.assertTrue(owned.receipt['verified'], owned.receipt)
            self.assertFalse(owned.receipt['launch_succeeded'])
            self.assertFalse(successor_allowed(owned.receipt,
                                              expected_owner=owned.owner_id))

    def test_inconclusive_observation_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            owned=OwnedProcess([sys.executable,'-c','pass'],Path(d)/'control').start()
            owned.wait(timeout=5)
            # Fail the post-kill observation, so no live fixture is leaked.
            from retro_eval.investigation.process_barrier import group_states
            calls=[]
            def fail_second(group):
                calls.append(group)
                if len(calls)>1:raise RuntimeError('synthetic ps failure')
                return group_states(group)
            with patch('retro_eval.investigation.process_barrier.group_states',side_effect=fail_second):
                owned.cleanup()
            self.assertFalse(successor_allowed(owned.receipt, expected_owner=owned.owner_id))
            self.assertTrue(owned.receipt['errors'])

    def test_missing_contradictory_and_surviving_receipts_block(self):
        for value in (None,{}, {'verified':True}, {'running_after':{}},
                      {'verified':True,'running_after':{'123':'S'}}):
            self.assertFalse(successor_allowed(value))

    def test_malformed_marker_does_not_prevent_kill(self):
        from retro_eval.investigation.process_barrier import group_states
        for marker in ('{', '[]', '{"phase":[]}'):
            with self.subTest(marker=marker),tempfile.TemporaryDirectory() as d:
                owned=OwnedProcess([sys.executable,'-c','import time; time.sleep(20)'],Path(d)/'control').start()
                owned.wait(timeout=.15)
                owned.marker.write_text(marker)
                owned.cleanup()
                self.assertFalse(successor_allowed(owned.receipt, expected_owner=owned.owner_id))
                self.assertFalse({p:s for p,s in group_states(owned.proc.pid).items() if not s.startswith('Z')})

    def test_surviving_real_process_cannot_admit_successor(self):
        import signal
        with tempfile.TemporaryDirectory() as d:
            owned=OwnedProcess([sys.executable,'-c','import time; time.sleep(20)'],Path(d)/'control').start()
            owned.wait(timeout=.1)
            try:
                with patch('retro_eval.investigation.process_barrier.os.killpg'):
                    owned.cleanup(timeout=.1)
                self.assertIsNone(owned.proc.poll())
                self.assertFalse(successor_allowed(owned.receipt, expected_owner=owned.owner_id))
                self.assertTrue(owned.receipt['errors'])
            finally:
                os.killpg(owned.proc.pid,signal.SIGKILL)
                owned.proc.wait(timeout=5)

    def test_anchor_launch_failure_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            owned=OwnedProcess([sys.executable,'-c','pass'],Path(d)/'control')
            with patch('retro_eval.investigation.process_barrier.subprocess.Popen',side_effect=OSError('synthetic launch failure')):
                with self.assertRaises(OSError):owned.start()
            self.assertFalse(successor_allowed(owned.receipt, expected_owner=owned.owner_id))
            self.assertTrue((owned.directory/'cleanup.json').exists())
