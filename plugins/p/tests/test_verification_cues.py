"""Authored transport checks: summary text is a cue, never execution proof."""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.packs import diagnosis
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.sources import normalize_captured
from retro_eval.workflow.records import canonical
from test_eval_workflow_sources import source_fixture


def fixture(source, outputs):
    rows = []
    for i, output in enumerate(outputs):
        if source == 'claude':
            rows.extend([
                {'type': 'assistant', 'message': {'role': 'assistant', 'content': [
                    {'type': 'tool_use', 'id': str(i), 'name': 'Bash', 'input': {'command': 'run unittest'}}]}},
                {'type': 'user', 'message': {'role': 'user', 'content': [
                    {'type': 'tool_result', 'tool_use_id': str(i), 'content': output}]}}])
        else:
            rows.extend([
                {'type': 'response_item', 'payload': {'type': 'function_call', 'call_id': str(i),
                 'name': 'exec_command', 'arguments': '{"cmd":"run unittest"}'}},
                {'type': 'response_item', 'payload': {'type': 'function_call_output',
                 'call_id': str(i), 'output': output}}])
    return source_fixture(source) + b''.join(canonical(row) + b'\n' for row in rows)


class VerificationCueTests(unittest.TestCase):
    def cues(self, source, outputs):
        with tempfile.TemporaryDirectory() as temp:
            store = Registry(Path(temp))
            try:
                captured = normalize_captured(source, 'sample/session.jsonl', fixture(source, outputs), store)
                context = {**captured.summary(), 'records': captured.records, 'excerpts': captured.excerpts}
                return [c for c in diagnosis(context)['candidates'] if c['category'] == 'verification_evidence']
            finally:
                store.close()

    def test_quotes_rejections_and_real_checks_are_unverified_cues(self):
        for source in ('claude', 'codex'):
            for result in ('Invocation rejected; proposed note: 3 passed',
                           'echo produced: 3 passed', '3 passed, 2 failed', 'Ran 3 tests\nOK'):
                with self.subTest(source=source, result=result):
                    cues = self.cues(source, [result])
                    self.assertEqual(len(cues), 1)
                    self.assertFalse(cues[0]['positive_practice'])
                    self.assertEqual(cues[0]['benefit_status'], 'unestablished')
                    self.assertIn('Execution is unverified', cues[0]['interpretation'])
                    self.assertNotIn('executed-check', cues[0]['fact'])

    def test_distinct_reruns_not_collapsed_by_equal_text(self):
        for source in ('claude', 'codex'):
            with self.subTest(source=source):
                cues = self.cues(source, ['Ran 3 tests\nOK'] * 2)
                self.assertEqual(len(cues), 2)
                self.assertNotEqual(cues[0]['key'], cues[1]['key'])

    def test_no_summary_is_not_invented(self):
        for source in ('claude', 'codex'):
            self.assertEqual(self.cues(source, ['No test output available']), [])


if __name__ == '__main__':
    unittest.main()
