"""Real process exit must not turn pending effects into repeatable work."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.runner import Runner

PLUGIN = Path(__file__).resolve().parents[1]
PLAN = {'limits': {'runtime_seconds': 10, 'provider_calls': 1, 'cases': 2}}
CHILD = '''
import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.runner import Runner
base, stage, provider = pathlib.Path(sys.argv[2]), sys.argv[3], sys.argv[4] == 'True'
store = Registry(base / 'store')
runner = Runner(store, clock=lambda: 100)
runner.begin({'limits': {'runtime_seconds': 10, 'provider_calls': 1, 'cases': 2}}, run_id='probe')
def callback():
    if stage == 'reserved':
        os._exit(73)
    with (base / 'effects').open('ab', buffering=0) as out:
        out.write(b'effect\\n')
        os.fsync(out.fileno())
    if stage == 'effect':
        os._exit(73)
    return {'delivered': 'authored marker'}
if stage == 'staged':
    original = store.stage_artifact
    def interrupted(*args):
        original(*args)
        os._exit(73)
    store.stage_artifact = interrupted
if stage == 'binding':
    original = store._bind_artifact
    def interrupted(*args):
        original(*args)
        os._exit(73)
    store._bind_artifact = interrupted
runner.unit('probe', 'one', 'frozen', callback, provider=provider, reservation={'cases': 1})
os._exit(73)
'''


class ProcessRecoveryTests(unittest.TestCase):
    def test_process_exit_at_each_effect_publication_boundary(self):
        for provider in (False, True):
            for stage in ('reserved', 'effect', 'staged', 'binding', 'committed'):
                with self.subTest(provider=provider, stage=stage), tempfile.TemporaryDirectory() as tmp:
                    base = Path(tmp)
                    child = subprocess.run([sys.executable, '-B', '-c', CHILD,
                        str(PLUGIN), str(base), stage, str(provider)],
                        capture_output=True, timeout=15)
                    self.assertEqual(child.returncode, 73, child.stderr)
                    effects = base / 'effects'
                    expected = b'' if stage == 'reserved' else b'effect\n'
                    self.assertEqual(effects.read_bytes() if effects.exists() else b'', expected)
                    store = Registry(base / 'store')
                    try:
                        now = [104]
                        runner = Runner(store, clock=lambda: now[0])
                        before = runner.status('probe')
                        self.assertEqual(runner.begin(PLAN, run_id='probe')['deadline'], 110)
                        self.assertEqual(len(list(store.objects.iterdir())),
                            int(stage in ('staged', 'binding', 'committed')))
                        self.assertEqual(store.db.execute('SELECT count(*) FROM artifacts').fetchone()[0],
                            int(stage == 'committed'))
                        def forbidden():
                            self.fail('restart repeated an uncertain or committed effect')
                        replay = runner.unit('probe', 'one', 'frozen', forbidden,
                            provider=provider, reservation={'cases': 1})
                        self.assertEqual(replay['status'],
                            'completed' if stage == 'committed' else 'ambiguous_operation')
                        after = runner.status('probe')
                        self.assertEqual(after['used'], before['used'])
                        self.assertEqual(after['operations'][0]['charge_status'],
                            'unknown' if provider else 'not_applicable')
                        if stage == 'committed':
                            self.assertEqual(json.loads(store.read_artifact(replay['artifact_ref'])[0]),
                                {'delivered': 'authored marker'})
                        else:
                            self.assertEqual(after['operations'][0]['state'], 'uncertain')
                        now[0] = 112
                        self.assertEqual(runner.begin(PLAN, run_id='probe')['deadline'], 110)
                        self.assertEqual(runner.unit('probe', 'new', 'new', forbidden,
                            reservation={'cases': 1}),
                            {'status': 'budget_exhausted', 'reason': 'deadline_exhausted'})
                        self.assertEqual(store.db.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
                    finally:
                        store.close()
