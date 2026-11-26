import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.pilots import prepare, compare, unwrap_user_request
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.records import RecordRef, strict_loads, canonical
from retro_eval.workflow.service import run_automatic
from retro_eval.workflow.errors import WorkflowError
from test_eval_workflow_sources import jsonl
from test_eval_workflow_records import TIME


class PilotTests(unittest.TestCase):
    def test_wrapper_recognition_preserves_ambiguous_or_unrecognized_content(self):
        self.assertEqual(unwrap_user_request('ordinary text')['text'], 'ordinary text')
        self.assertFalse(unwrap_user_request('<USER_REQUEST>incomplete')['changed'])
        self.assertFalse(unwrap_user_request('<USER_REQUEST>x</USER_REQUEST>unrecognized tail')['changed'])
        result = unwrap_user_request('<USER_REQUEST>\nAé中?\n</USER_REQUEST>\n<ADDITIONAL_METADATA>ignored')
        self.assertEqual(result['text'], 'Aé中?')
        self.assertTrue(result['changed'])

    def test_real_entrypoint_produces_registered_paired_comparison_and_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'sources/sample/.system_generated/logs/transcript_full.jsonl'
            path.parent.mkdir(parents=True)
            rows = [
                {'step_index': 0, 'type': 'USER_INPUT', 'source': 'USER_EXPLICIT', 'created_at': TIME,
                 'content': 'Start the task.'},
                {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'source': 'MODEL', 'created_at': TIME,
                 'content': 'An authored context sentence. ' * 20},
                {'step_index': 2, 'type': 'USER_INPUT', 'source': 'USER_EXPLICIT', 'created_at': TIME,
                 'content': '<USER_REQUEST>What time is it?</USER_REQUEST><ADDITIONAL_METADATA>info</ADDITIONAL_METADATA>'}]
            path.write_bytes(jsonl(rows))
            run_automatic(root / 'store', roots={'antigravity': root / 'sources'}, captured_at=TIME)
            store = Registry(root / 'store')
            try:
                registered = prepare(store, created_at=TIME)
                self.assertEqual(registered['status'], 'registered')
                labels = strict_loads(Path(registered['label_packet']).read_bytes())
                for label in labels['units']:
                    label.update(label=False, reason='Authored ordinary request or question.')
                ref = RecordRef.from_dict(registered['pilot_ref'])
                result = compare(store, ref, labels, created_at=TIME)
                self.assertEqual(result['estimates']['false_alert_count_difference'], -1)
                self.assertEqual(result['decision']['disposition'], 'insufficient_evidence')
                repeated = compare(store, ref, labels, created_at=TIME)
                self.assertEqual(result['analysis_ref'], repeated['analysis_ref'])
                self.assertEqual(len(store.heads(kind='analysis')), 1)
                reused = prepare(store, created_at=TIME)
                self.assertEqual(reused['status'], 'insufficient_evidence')
                self.assertEqual(reused['reason'], 'no_heldout_envelope_replay_units')
                labels['units'].pop()
                with self.assertRaises(WorkflowError):
                    compare(store, ref, labels)
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
