"""Usage counts response identities, without deduplicating their tool content."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.adapters.claude import ClaudeAdapter
from retro_eval.workflow.sources import source_observations
from retro_eval.workflow.sources import normalize_captured
from retro_eval.workflow.registry import Registry
from retro_eval.deterministic_scorers import InputTokensPerSourceCompletionScorer
from test_retro_extract import load_retro

retro = load_retro()


class ClaudeUsageIdentityTests(unittest.TestCase):
    def check_usage(self, messages, expected):
        rows = [{'type': 'user', 'promptSource': 'typed',
                 'message': {'role': 'user', 'content': 'Check the example.'}}]
        rows += [{'type': 'assistant', 'message': {
            'role': 'assistant', 'content': 'Observed response.', **message}}
                 for message in messages]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'session.jsonl'
            path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
            result = ClaudeAdapter(b'authored').read(path, root)
            trace = result.records[0]
            ledger = retro.measure(path, 'claude', root)
            observed = source_observations('claude', list(enumerate(rows, 1)), [], result)
        actual = (trace.input_tokens, trace.cached_input_tokens, trace.output_tokens)
        self.assertEqual(actual, expected)
        self.assertEqual((ledger['tokens_in'], ledger['cache_read'], ledger['tokens_out']), expected)
        self.assertEqual(tuple(observed[f]['value'] for f in
            ('input_tokens', 'cache_read_input_tokens', 'output_tokens')), expected)
        return result, observed

    @staticmethod
    def message(identity, amount):
        return {'id': identity, 'usage': {'input_tokens': amount,
            'cache_read_input_tokens': amount * 2, 'output_tokens': amount * 3}}

    def test_repeated_message_uses_last_snapshot_not_sum_or_maximum(self):
        self.check_usage([self.message('one', 10), self.message('one', 20)], (20, 40, 60))
        self.check_usage([self.message('one', 20), self.message('one', 10)], (10, 20, 30))

    def test_distinct_and_anonymous_messages_are_not_deduplicated_by_amount(self):
        self.check_usage([self.message('one', 10), self.message('two', 10)], (20, 40, 60))
        self.check_usage([self.message(None, 10), self.message(None, 10)], (20, 40, 60))

    def test_parallel_tool_content_survives_usage_deduplication(self):
        messages = []
        for index in range(2):
            message = self.message('shared', 10)
            message['content'] = [{'type': 'tool_use', 'id': 'call-' + str(index),
                                   'name': 'Read', 'input': {'file': str(index)}}]
            messages.append(message)
        result, observed = self.check_usage(messages, (10, 20, 30))
        self.assertEqual(result.tool_call_count, 2)
        self.assertEqual(observed['input_tokens']['coverage']['duplicate_snapshots'], 1)

    def test_last_missing_usage_does_not_reuse_an_earlier_snapshot(self):
        messages = [self.message('one', 10), {'id': 'one'}]
        rows = [{'type': 'assistant', 'message': m} for m in messages]
        observed = source_observations('claude', list(enumerate(rows, 1)), [],
                                      type('Result', (), {'records': (), 'is_subagent': False})())
        self.assertIsNone(observed['input_tokens']['value'])
        self.assertEqual(observed['input_tokens']['coverage']['observed'], 0)
        from retro_eval.claude_usage import ClaudeMessageUsage
        usage = ClaudeMessageUsage()
        for message in messages:
            usage.add(message)
        self.assertEqual(usage.usages, (None,))

    def test_nonstring_ids_are_anonymous_not_accidental_identity_keys(self):
        self.check_usage([self.message([], 10), self.message([], 10)], (20, 40, 60))

    def test_superseded_invalid_tokens_remain_in_legacy_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'session.jsonl'
            path.write_text(''.join(json.dumps({'type': 'assistant', 'message': m}) + '\n'
                for m in (self.message('one', -1), self.message('one', 10))))
            row = retro.measure(path, 'claude', root)
        self.assertEqual(row['tokens_in'], 10)
        self.assertEqual(row['skipped']['bad_token_count'], 3)

    def test_invalid_final_usage_is_missing_through_normalization_and_scorer(self):
        for invalid in ('invalid', {'bad': 1}, [], -1, True, 1.5, None):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                rows = [{'type': 'user', 'promptSource': 'typed', 'message': {
                    'role': 'user', 'content': 'Check the example.'}}]
                messages = [self.message('good', 5), self.message('one', 10),
                            self.message('one', 10)]
                messages[-1]['usage']['input_tokens'] = invalid
                rows += [{'type': 'assistant', 'message': {
                    'role': 'assistant', 'content': 'Observed response.', **m}} for m in messages]
                data = b''.join((json.dumps(r) + '\n').encode() for r in rows)
                store = Registry(root / 'store')
                try:
                    captured = normalize_captured('claude', 'example/session.jsonl', data, store)
                finally:
                    store.close()
                self.assertTrue(captured.included)
                self.assertIsNone(captured.observations['input_tokens']['value'])
                from retro_eval.schema import TraceRecord
                records = [TraceRecord.from_dict(r) for r in captured.records]
                score = InputTokensPerSourceCompletionScorer('usage', 1, None, .95, 0).score(records)
                self.assertTrue(score.abstained)
                self.assertEqual(score.eligible_population, 0)


if __name__ == '__main__':
    unittest.main()
