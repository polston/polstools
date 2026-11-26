"""Nested workflow completion must remain attributable after a parent process exits."""
import json
import io
import hashlib
from contextlib import redirect_stdout
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.investigation import Investigation
from retro_eval.investigation.workflow import collect_workflow
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.runner import Limits, Runner
from dataclasses import asdict
from retro_eval.workflow.records import RecordRef, canonical, digest
from retro_eval.workflow.errors import WorkflowError
from retro_eval.workflow.service import run_automatic
from test_investigation_state import frame
from test_eval_workflow_sources import source_fixture


def limits():
    return Limits(runtime_seconds=30, cases=1, source_files=10,
                  captured_bytes=1024 * 1024, derived_bytes=1024 * 1024, cards=1)


def crash_after_report(work, action):
    import retro_eval.investigation.workflow as bridge
    root = RecordRef.from_dict(json.loads((work / 'root.json').read_text()))
    store = Registry(work / 'store')
    api = Investigation(store)
    original = bridge.run_automatic

    def interrupted(*args, **kwargs):
        (work / (action + '.ready')).write_text('admitted\n')
        end = time.monotonic() + 30
        while not (work / (action + '.release')).exists():
            if time.monotonic() >= end:
                raise TimeoutError('fixture release was not observed')
            time.sleep(.01)
        report = original(*args, **kwargs)
        # Fixture-only ground truth: production recovery must use the store,
        # not this external observation of which process produced which run.
        with (work / (action + '.oracle')).open('w') as handle:
            json.dump(report, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os._exit(73)

    bridge.run_automatic = interrupted
    collect_workflow(api, root, api.checkpoint(root)['sequence'], action,
                     roots={'claude': work / 'source'}, limits=limits())
    raise AssertionError('fixture did not reach the crash boundary')


class WorkflowRecoveryTests(unittest.TestCase):
    def setup_store(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        store = Registry(base / 'store')
        self.addCleanup(store.close)
        api = Investigation(store)
        root = api.begin('bridge', frame(), deadline_ms=time.time_ns() // 1000000 + 60000,
            limits={'actions': 3}, authority=['local-session-history'],
            capabilities={'deadline': {'mode': 'helper_enforced', 'scope': 'admission'}})
        return base, store, api, root

    def test_unknown_legacy_and_multiple_links_do_not_guess_or_settle(self):
        base, store, api, root = self.setup_store()
        with patch('retro_eval.investigation.workflow.run_automatic', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                collect_workflow(api, root, 0, 'capture', roots={}, limits=limits())
        before = api.checkpoint(root)
        runner = Runner(store)
        runner.begin({'limits': asdict(limits())}, run_id='legacy')
        replay = collect_workflow(api, root, 0, 'capture', roots={}, limits=limits())
        self.assertEqual(replay['recovery']['link_status'], 'unknown')
        self.assertEqual(replay['recovery']['runs'], [])
        reference = replay['recovery']['invocation_ref']
        self.assertIsNotNone(reference)
        for name in ('one', 'two'):
            runner.begin({'limits': asdict(limits()), 'invocation_ref': reference}, run_id=name)
        replay = collect_workflow(api, root, 0, 'capture', roots={}, limits=limits())
        self.assertEqual({r['run_id'] for r in replay['recovery']['runs']}, {'one', 'two'})
        self.assertEqual(api.checkpoint(root)['actions'], before['actions'])
        self.assertEqual(api.checkpoint(root)['grants'], before['grants'])
        command = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve().parents[1] /
            'bin' / 'retro-eval-workflow'), '--store', str(store.root), 'status'],
            capture_output=True, text=True)
        self.assertEqual(command.returncode, 0, command.stderr)
        runs = {r['run_id']: r for r in json.loads(command.stdout)['runs']}
        self.assertNotIn('invocation_ref', runs['legacy'])
        self.assertEqual(runs['one']['invocation_ref'], reference)

    def test_readonly_inspection_cli_needs_no_capture_inputs_or_new_sequence(self):
        base, store, api, root = self.setup_store()
        with patch('retro_eval.investigation.workflow.run_automatic', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                collect_workflow(api, root, 0, 'capture', roots={}, limits=limits())
        reference = next(r.ref.to_dict() for r in store.history(kind='investigation_event')
                         if r.payload['event_type'] == 'action')
        Runner(store).begin({'limits': asdict(limits()), 'invocation_ref': reference}, run_id='linked')
        command = [sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'bin' / 'investigation'),
                   'inspect-workflow', '--store', str(store.root), '--root', root.key, '--action-id']
        def snapshot():
            return {str(p.relative_to(store.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in store.root.rglob('*') if p.is_file()}
        before = snapshot()
        result = subprocess.run(command + ['capture'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['invocation_ref'], reference)
        self.assertEqual([r['run_id'] for r in report['runs']], ['linked'])
        missing = subprocess.run(command + ['absent'], capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn('action admission not found', missing.stderr)
        self.assertEqual(snapshot(), before)
        # Direct read-only use must not need current roots, a budget or a clock.
        from retro_eval.investigation.workflow import inspect_workflow
        readonly = Registry(store.root, readonly=True)
        try:
            with patch('retro_eval.investigation.workflow.default_roots', side_effect=AssertionError('discovery')):
                self.assertEqual(inspect_workflow(Investigation(readonly), root, 'capture'), report)
        finally:
            readonly.close()

    def test_public_replay_command_returns_recovery_without_dispatch(self):
        from retro_eval.investigation.cli import main
        base, store, api, root = self.setup_store()
        with patch('retro_eval.investigation.workflow.default_roots', return_value={}):
            with patch('retro_eval.investigation.workflow.run_automatic', side_effect=RuntimeError('interrupted')):
                with self.assertRaises(RuntimeError):
                    collect_workflow(api, root, 0, 'capture')
            output = io.StringIO()
            with patch('retro_eval.investigation.workflow.run_automatic') as dispatch, redirect_stdout(output):
                code = main(['collect-workflow', '--store', str(store.root), '--root', root.key,
                             '--expected-sequence', '0', '--action-id', 'capture'])
                dispatch.assert_not_called()
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertFalse(result['admitted'])
            self.assertEqual(result['action']['state'], 'uncertain')
            self.assertEqual(result['recovery']['link_status'], 'unknown')
            self.assertTrue(result['recovery']['next_action'])

    def test_invalid_and_missing_origin_rejected_before_inventory(self):
        base, store, api, root = self.setup_store()
        missing = {**root.to_dict(), 'key': 'missing'}
        with patch('retro_eval.workflow.service.inventory_sources') as inventory:
            for reference in ({}, missing):
                with self.assertRaises(WorkflowError):
                    run_automatic(store.root, roots={}, invocation_ref=reference)
                with self.assertRaises(WorkflowError):
                    Runner(store).begin({'limits': asdict(limits()), 'invocation_ref': reference})
            inventory.assert_not_called()
        self.assertEqual(Runner(store).list_runs(), [])

    def test_resume_cannot_retrofit_or_replace_origin(self):
        base, store, api, root = self.setup_store()
        original = run_automatic(store.root, roots={}, limits=limits())
        with self.assertRaisesRegex(WorkflowError, 'frozen plan'):
            run_automatic(store.root, roots={}, resume=original['run_id'], invocation_ref=root.to_dict())
        linked = run_automatic(store.root, roots={}, limits=limits(), invocation_ref=root.to_dict())
        other = api.begin('other', frame(), deadline_ms=time.time_ns() // 1000000 + 60000,
            limits={}, authority=[], capabilities={'deadline': {'mode': 'helper_enforced', 'scope': 'admission'}})
        with self.assertRaisesRegex(WorkflowError, 'frozen plan'):
            run_automatic(store.root, roots={}, resume=linked['run_id'], invocation_ref=other.to_dict())
        for args in ({}, {'invocation_ref': root.to_dict()}):
            resumed = run_automatic(store.root, roots={}, resume=linked['run_id'], **args)
            self.assertEqual(resumed['run_id'], linked['run_id'])
            self.assertEqual(Runner(store).status(linked['run_id'])['plan']['invocation_ref'], root.to_dict())

    def test_import_rejects_dangling_run_origin_even_without_plan_record(self):
        base, store, api, root = self.setup_store()
        Runner(store).begin({'limits': asdict(limits())}, run_id='generic')
        bundle = store.export_bundle()
        bundle['runs'][0]['plan']['invocation_ref'] = {**root.to_dict(), 'key': 'missing'}
        bundle['runs'][0]['plan_digest'] = digest(canonical(bundle['runs'][0]['plan']))
        restored = Registry(base / 'restored')
        self.addCleanup(restored.close)
        with self.assertRaisesRegex(WorkflowError, 'invocation record is missing'):
            restored.import_bundle(bundle)
        self.assertEqual(Runner(restored).list_runs(), [])

    def test_interleaved_crashes_preserve_exact_parent_link_and_never_redispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            source = work / 'source'
            source.mkdir()
            (source / 'authored.jsonl').write_bytes(source_fixture('claude'))
            store = Registry(work / 'store')
            self.addCleanup(store.close)
            api = Investigation(store)
            deadline = time.time_ns() // 1000000 + 120000
            root = api.begin('bridge', frame(), deadline_ms=deadline,
                limits={'actions': 3}, authority=['local-session-history'],
                capabilities={'deadline': {'mode': 'helper_enforced', 'scope': 'admission'}})
            (work / 'root.json').write_text(json.dumps(root.to_dict()))
            children = {}
            try:
                for action in ('first', 'second'):
                    child = subprocess.Popen([sys.executable, '-B', __file__, 'crash', str(work), action],
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    children[action] = child
                    end = time.monotonic() + 20
                    while not (work / (action + '.ready')).exists():
                        if child.poll() is not None:
                            self.fail('child exited before admission: ' + child.communicate()[1].decode())
                        if time.monotonic() >= end:
                            self.fail('child admission did not arrive within fixture bound')
                        time.sleep(.01)
                # Both admissions precede both runs; run order deliberately
                # disagrees with admission order. Time is not an identity key.
                for action in ('second', 'first'):
                    (work / (action + '.release')).write_text('release\n')
                    _, error = children[action].communicate(timeout=40)
                    self.assertEqual(children[action].returncode, 73, error.decode())
                state = api.checkpoint(root)
                self.assertEqual(state['grants']['root']['held'], {'actions': 2})
                self.assertEqual({a['state'] for a in state['actions'].values()}, {'admitted'})
                references = {r.payload['data']['id']: r.ref.to_dict()
                    for r in store.history(kind='investigation_event')
                    if r.payload['event_type'] == 'action'}
                oracles = {a: json.loads((work / (a + '.oracle')).read_text()) for a in children}
                runs = {r['run_id']: r for r in Runner(store).list_runs()}
                self.assertEqual(len(runs), 2)
                for action, oracle in oracles.items():
                    self.assertEqual(runs[oracle['run_id']]['plan'].get('invocation_ref'), references[action])
                restored = Registry(work / 'restored')
                try:
                    restored.import_bundle(store.export_bundle())
                    for current in (store, restored):
                        late = Investigation(current, clock=lambda: deadline + 1)
                        with patch('retro_eval.investigation.workflow.run_automatic',
                                   side_effect=AssertionError('repeated capture')) as run:
                            for action, oracle in oracles.items():
                                replay = collect_workflow(late, root, 0, action,
                                    roots={'claude': source}, limits=limits())
                                self.assertFalse(replay['admitted'])
                                related = replay['recovery']['runs']
                                self.assertEqual([r['run_id'] for r in related], [oracle['run_id']])
                                self.assertEqual(related[0]['invocation_ref'], references[action])
                                self.assertTrue(related[0]['published_views'])
                                for ref in related[0]['published_views']:
                                    current.read_artifact(ref)
                            run.assert_not_called()
                        retained = late.checkpoint(root)
                        self.assertEqual(retained['grants']['root']['deadline_ms'], deadline)
                        self.assertEqual(retained['grants']['root']['held'], {'actions': 2})
                        self.assertEqual({a['state'] for a in retained['actions'].values()}, {'admitted'})
                        self.assertEqual(current.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                finally:
                    restored.close()
            finally:
                for child in children.values():
                    if child.poll() is None:
                        child.kill()
                    child.communicate()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'crash':
        crash_after_report(Path(sys.argv[2]), sys.argv[3])
    else:
        unittest.main()
