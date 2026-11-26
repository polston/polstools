"""Repeat analysis must distinguish absent input from observed empty input."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.adapters import ClaudeAdapter, CodexAdapter
from retro_eval.adapters.antigravity import AntigravityAdapter
from retro_eval.deterministic_scorers import RepeatedCallScorer
from retro_eval.schema import SpanKind
from retro_eval.taxonomies import repeated_call_candidates


MISSING = object()
ADAPTERS = {'claude': ClaudeAdapter, 'codex': CodexAdapter,
            'antigravity': AntigravityAdapter}


class AdapterSignatureTests(unittest.TestCase):
    def capture(self, source, inputs, names=None, custom=False):
        names = names or ['read_file'] * len(inputs)
        calls = []
        for index, (value, name) in enumerate(zip(inputs, names)):
            call = {'name': name, 'id': str(index), 'call_id': str(index)}
            if source == 'claude':
                call['type'] = 'tool_use'
                field = 'input'
            elif source == 'codex':
                call['type'] = 'custom_tool_call' if custom else 'function_call'
                field = 'input' if custom else 'arguments'
            else:
                field = 'args'
            if value is not MISSING:
                call[field] = value
            calls.append(call)
        if source == 'claude':
            rows = [{'type': 'user', 'promptSource': 'typed',
                     'message': {'role': 'user', 'content': 'inspect'}},
                    {'type': 'assistant', 'message': {'role': 'assistant', 'content': calls}}]
        elif source == 'codex':
            rows = [{'type': 'session_meta', 'payload': {'thread_source': 'user', 'id': 'authored'}},
                    {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': 'inspect'}},
                    *({'type': 'response_item', 'payload': call} for call in calls)]
        else:
            rows = [{'step_index': 0, 'type': 'USER_INPUT', 'source': 'USER_EXPLICIT', 'content': 'inspect'},
                    {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'source': 'MODEL', 'tool_calls': calls}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'capture.jsonl'
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            adapter = ADAPTERS[source](b'authored')
            result = adapter.read(path, root)
            evidence = adapter.private_tool_evidence(path, root, lambda text: text)
        self.assertTrue(result.included)
        tools = [r for r in result.records if r.span_kind == SpanKind.TOOL]
        self.assertEqual(len(inputs), len(tools))
        return result.records, tools, evidence

    def test_absent_and_null_input_abstain_through_real_adapters(self):
        for source in ADAPTERS:
            for value in (MISSING, None):
                with self.subTest(source=source, value=value):
                    records, calls, evidence = self.capture(source, [value, value])
                    self.assertTrue(all(not r.call_signature for r in calls))
                    self.assertEqual((), repeated_call_candidates(records))
                    score = RepeatedCallScorer('repeat', minimum_n=1).score(records)
                    self.assertEqual(2, score.details['missing_call_signatures'])
                    self.assertTrue(score.abstained)
                    self.assertTrue(all('tool_input' not in evidence[r.span_id] for r in calls))

    def test_observed_empty_and_false_values_remain_known(self):
        for source in ADAPTERS:
            for value in ({}, [], '', False, 0):
                with self.subTest(source=source, value=value):
                    records, calls, evidence = self.capture(source, [value, value])
                    self.assertTrue(all(r.call_signature for r in calls))
                    self.assertEqual(1, len(repeated_call_candidates(records)))
                    self.assertTrue(all('tool_input' in evidence[r.span_id] for r in calls))

    def test_codex_equivalent_json_encodings_share_identity_even_when_empty(self):
        for value in ({}, [], False, 0, {'b': 2, 'a': 1}):
            with self.subTest(value=value):
                records, calls, _ = self.capture('codex', [value, json.dumps(value)])
                self.assertEqual(calls[0].call_signature, calls[1].call_signature)
                self.assertEqual(1, len(repeated_call_candidates(records)))

    def test_codex_custom_empty_input_is_observed_but_absence_is_unknown(self):
        records, calls, evidence = self.capture('codex', ['', '', MISSING, None], custom=True)
        self.assertTrue(calls[0].call_signature)
        self.assertEqual(calls[0].call_signature, calls[1].call_signature)
        self.assertEqual(['', ''], [r.call_signature for r in calls[2:]])
        self.assertEqual(1, len(repeated_call_candidates(records)))
        self.assertNotIn('tool_input', evidence[calls[2].span_id])

    def test_unknown_input_preserves_intervening_mutation(self):
        for source in ADAPTERS:
            with self.subTest(source=source):
                records, calls, _ = self.capture(source, [{'path': 'x'}, MISSING, {'path': 'x'}],
                                                names=['read_file', 'Edit', 'read_file'])
                self.assertFalse(calls[1].call_signature)
                repeats = repeated_call_candidates(records)
                self.assertEqual(1, len(repeats))
                self.assertEqual('post_state_change', repeats[0].candidate_class)

    def test_absent_tool_identity_cannot_establish_repetition(self):
        for source in ADAPTERS:
            with self.subTest(source=source):
                records, calls, _ = self.capture(source, [{}, {}], names=['', ''])
                self.assertTrue(all(not r.call_signature for r in calls))
                self.assertEqual((), repeated_call_candidates(records))


if __name__ == '__main__':
    unittest.main()
