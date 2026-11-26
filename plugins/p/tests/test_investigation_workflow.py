"""The general investigation actually reuses source capture with shared bounds."""
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.investigation import Investigation
from retro_eval.investigation.workflow import collect_workflow
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.runner import Limits, Runner
from retro_eval.workflow.records import strict_loads
from retro_eval.workflow.errors import WorkflowError
from test_investigation_state import frame
from test_eval_workflow_sources import source_fixture


class WorkflowBridgeTests(unittest.TestCase):
    def test_actual_capture_into_format_two_store(self):
        import time
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / 'source'
            source.mkdir()
            (source / 'authored.jsonl').write_bytes(source_fixture('claude'))
            store = Registry(base / 'store')
            self.addCleanup(store.close)
            api = Investigation(store)
            root = api.begin('workflow', frame(), deadline_ms=time.time_ns() // 1000000 + 60000,
                limits={'actions': 3}, authority=['local-session-history'], capabilities={
                    'deadline': {'mode': 'helper_enforced', 'scope': 'capture admission'}})
            result = collect_workflow(api, root, 0, 'capture', roots={'claude': source})
            self.assertGreater(result['coverage']['claude']['included'], 0)
            report, _ = store.read_artifact(result['report_ref'])
            self.assertEqual(strict_loads(report)['run_id'], result['run_id'])
            self.assertEqual(api.checkpoint(root)['actions']['capture']['state'], 'succeeded')
            again = collect_workflow(api, root, 0, 'capture', roots={'claude': source})
            self.assertFalse(again['admitted'])
            self.assertEqual(store.db.execute('SELECT count(*) FROM runs').fetchone()[0], 1)

    def test_root_deadline_and_authority_prevent_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Registry(Path(temp))
            self.addCleanup(store.close)
            api = Investigation(store, clock=lambda: 1000)
            root = api.begin('short', frame(), deadline_ms=1500, limits={}, authority=[],
                capabilities={'deadline': {'mode': 'helper_enforced', 'scope': 'admission'}})
            with patch('retro_eval.investigation.workflow.run_automatic') as run:
                with self.assertRaises(WorkflowError):
                    collect_workflow(api, root, 0, 'denied')
                run.assert_not_called()

    def test_helper_episode_cannot_reset_root_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Registry(Path(temp))
            self.addCleanup(store.close)
            now = [1000]
            api = Investigation(store, clock=lambda: now[0])
            root = api.begin('short', frame(), deadline_ms=4000, limits={'actions': 2},
                authority=['local-session-history'], capabilities={
                    'deadline': {'mode': 'helper_enforced', 'scope': 'admission'}})
            def run(path, *, limits, clock, **kwargs):
                self.assertEqual(limits.runtime_seconds, 3)
                self.assertEqual(clock(), 1)
                now[0] = 4000
                clock()
            with patch('retro_eval.investigation.workflow.run_automatic', side_effect=run):
                with self.assertRaises(WorkflowError):
                    collect_workflow(api, root, 0, 'expired', limits=Limits(runtime_seconds=600))
            state = api.checkpoint(root)
            self.assertEqual(state['actions']['expired']['state'], 'uncertain')
            self.assertEqual(state['grants']['root']['held'], {'actions': 1})

    def test_byte_settlement_includes_planning_and_published_reports(self):
        import time
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / 'source'
            source.mkdir()
            (source / 'authored.jsonl').write_bytes(source_fixture('claude'))
            store = Registry(base / 'store')
            self.addCleanup(store.close)
            api = Investigation(store)
            root = api.begin('metered', frame(), deadline_ms=time.time_ns() // 1000000 + 60000,
                limits={'actions': 2, 'bytes': 10_000_000}, authority=['local-session-history'],
                capabilities={'admission': {'mode': 'helper_enforced', 'scope': 'capture'}})
            result = collect_workflow(api, root, 0, 'capture', roots={'claude': source})
            used = Runner(store).status(result['run_id'])['used']
            state = api.checkpoint(root)
            self.assertEqual(result['state'], 'completed')
            self.assertEqual(state['grants']['root']['spent']['bytes'],
                             used['captured_bytes'] + used['derived_bytes'])
            self.assertGreater(used['derived_bytes'], 0)
