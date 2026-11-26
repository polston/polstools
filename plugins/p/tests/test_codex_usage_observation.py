"""Last-counter observations preserve missingness without guessing lifetime cost."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.sources import normalize_captured
from retro_eval.workflow.registry import Registry
from retro_eval.schema import TraceRecord
from retro_eval.deterministic_scorers import InputTokensPerSourceCompletionScorer
from test_eval_workflow_sources import source_fixture


class RangeBackend:
    name = 'authored-range-only'

    def interval(self, values, *args):
        return min(values), max(values)


class CodexUsageObservationTests(unittest.TestCase):
    def normalize(self, counters):
        rows = [json.loads(line) for line in source_fixture('codex', usage=False).splitlines()]
        for counter in counters:
            rows.append({'type': 'event_msg', 'payload': {'type': 'token_count',
                'info': None if counter is None else {'total_token_usage': counter}}})
        data = b''.join((json.dumps(r) + '\n').encode() for r in rows)
        with tempfile.TemporaryDirectory() as tmp:
            store = Registry(Path(tmp) / 'store')
            try:
                captured = normalize_captured('codex', 'example/session.jsonl', data, store)
            finally:
                store.close()
        self.assertTrue(captured.included)
        return captured, TraceRecord.from_dict(captured.records[0])

    def score(self, roots):
        return InputTokensPerSourceCompletionScorer(
            'usage', 1, RangeBackend(), .95, 0).score(roots)

    def test_invalid_final_fields_do_not_abort_or_enter_comparisons(self):
        for invalid in ('invalid', '12', {}, [], -1, True, 1.5, None):
            with self.subTest(invalid=invalid):
                captured, root = self.normalize([
                    {'input_tokens': 20}, {'input_tokens': invalid, 'output_tokens': 4}])
                self.assertIsNone(captured.observations['input_tokens']['value'])
                self.assertEqual(root.input_tokens, 0)
                self.assertFalse(root.attributes['usage_complete']['input_tokens'])
                self.assertEqual(root.output_tokens, 4)
                self.assertTrue(self.score([root]).abstained)

    def test_observed_zero_is_included_but_missing_zero_is_not(self):
        roots = [self.normalize([{'input_tokens': value}])[1] for value in (0, 10)]
        roots.append(self.normalize([{}])[1])
        score = self.score(roots)
        self.assertEqual(score.value, 5)
        self.assertEqual(score.eligible_population, 2)

    def test_last_dictionary_scope_not_sum_max_or_backfill(self):
        captured, root = self.normalize([
            {'input_tokens': 100, 'output_tokens': 40}, {'input_tokens': 10}, None])
        self.assertEqual(root.input_tokens, 10)
        self.assertEqual(captured.observations['input_tokens']['value'], 10)
        self.assertFalse(root.attributes['usage_complete']['output_tokens'])
        self.assertIsNone(captured.observations['output_tokens']['value'])
        self.assertEqual(root.attributes['usage_scope'], 'last_captured_cumulative_counter')
        self.assertEqual(root.attributes['usage_accounting_events'], 2)

    def test_each_dimension_is_validated_without_losing_prompt_content(self):
        fields = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                  'output_tokens', 'reasoning_output_tokens')
        for field in fields:
            with self.subTest(field=field):
                usage = dict.fromkeys(fields, 5)
                usage[field] = {'invalid': True}
                captured, root = self.normalize([usage])
                self.assertEqual(getattr(root, field), 0)
                self.assertFalse(root.attributes['usage_complete'][field])
                self.assertEqual(len(captured.records), 3)
                self.assertTrue(captured.excerpts['prompts'])


if __name__ == '__main__':
    unittest.main()
