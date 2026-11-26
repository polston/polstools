"""Authored origin boundaries and end-to-end notification regressions."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_eval_ledger_parity import retro, SALT, T0, STAMP, LONG
from fixtures import claude_user, claude_assistant, rollout_meta, rollout_user, rollout_assistant
from retro_eval.adapters.codex import CodexAdapter
from retro_eval.adapters.claude import ClaudeAdapter
from retro_eval.schema import SpanKind
from retro_eval.text_rules import machine_prompt_origin
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.sources import normalize_captured

HOOK = '<hook_prompt hook_run_id="example">No, the automatic check failed.</hook_prompt>'


class OriginBoundaries(unittest.TestCase):
    def test_complete_notifications(self):
        for body in (HOOK, ' \n' + HOOK + '\t',
                     "<hook_prompt extra='check'\n hook_run_id = 'example' >\nwrong\n</hook_prompt>"):
            with self.subTest(body=body):
                self.assertEqual(machine_prompt_origin(body, ()), 'hook_prompt_envelope')

    def test_mentions_and_uncertain_shapes_are_preserved(self):
        for body in ('Please fix ' + HOOK, '```xml\n' + HOOK + '\n```',
                     HOOK + '\nNo, fix this.\n' + HOOK, '> ' + HOOK, '"' + HOOK + '"', HOOK + '\nThat is wrong.',
                     HOOK.replace('hook_prompt', 'hook_prompt_extra'),
                     HOOK.replace('hook_run_id', 'other'),
                     HOOK.replace('example', ''), HOOK.replace('</hook_prompt>', ''),
                     HOOK.replace('hook_run_id="example"', 'hook_run_id=example'),
                     HOOK.replace('hook_run_id="example"', 'hook_run_id="a" hook_run_id="b"'),
                     '<unknown>no, wrong</unknown>'):
            with self.subTest(body=body):
                self.assertIsNone(machine_prompt_origin(body, ()))


class OriginReaders(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'session.jsonl'

    def write(self, rows):
        self.path.write_text(''.join(json.dumps(r) + '\n' for r in rows))

    def test_notification_does_not_consume_real_correction_context(self):
        self.write([rollout_meta(STAMP), rollout_assistant(LONG, STAMP),
                    rollout_user(HOOK, STAMP), rollout_user('no, wrong file', STAMP)])
        row = retro.measure_codex(self.path, self.root)
        self.assertEqual((row['user_prompts'], row['correction_candidates']), (1, 1))
        adapter = CodexAdapter(SALT)
        result = adapter.read(self.path, self.root)
        self.assertEqual(result.human_prompt_count, 1)
        self.assertEqual(sum(r.span_kind == SpanKind.PROMPT for r in result.records), 1)
        self.assertEqual(result.records[0].attributes['machine_message_origins'],
                         [{'sequence': 2, 'origin': 'machine', 'method': 'hook_prompt_envelope'}])
        evidence = adapter.private_prompt_evidence(self.path, self.root, lambda x: x)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(next(iter(evidence.values()))['context_chars'], len(LONG.strip()))
        with mock.patch.object(retro, 'codex_sessions_dir', return_value=self.root):
            moments = retro._moments_codex({'transcript': self.path.name})
        self.assertEqual(len(moments), 1)
        self.assertNotIn('hook_prompt', json.dumps(moments))

    def test_codex_human_markup_reaches_counts_and_annotation(self):
        texts = ['Please fix ' + HOOK, '```xml\n' + HOOK + '\n```',
                 HOOK + '\nThat is wrong.', '<unknown>no, wrong</unknown>']
        self.write([rollout_meta(STAMP)] + [rollout_user(t, STAMP) for t in texts])
        adapter = CodexAdapter(SALT)
        result = adapter.read(self.path, self.root)
        self.assertEqual(result.human_prompt_count, len(texts))
        self.assertEqual(retro.measure_codex(self.path, self.root)['user_prompts'], len(texts))
        evidence = adapter.private_prompt_evidence(self.path, self.root, lambda x: x)
        self.assertEqual([e['user_turn'] for e in evidence.values()], texts)
        self.assertEqual(result.machine_message_origins, ())

    def test_notification_only_has_no_human_packet_but_retains_machine_evidence(self):
        self.write([rollout_meta(STAMP), rollout_assistant(LONG, STAMP), rollout_user(HOOK, STAMP)])
        row = retro.measure_codex(self.path, self.root)
        self.assertEqual((row['user_prompts'], row['correction_candidates']), (0, 0))
        with contextlib.closing(Registry(self.root / 'store')) as store:
            captured = normalize_captured('codex', self.path.name, self.path.read_bytes(), store)
            self.assertFalse(captured.included)
            self.assertEqual(captured.excerpts['prompts'], {})
            self.assertEqual(captured.records, ())
            self.assertEqual(captured.observations['machine_messages']['value'][0]['origin'], 'machine')
            self.assertEqual(store.read_artifact(captured.raw_ref)[0], self.path.read_bytes())

    def test_explicit_human_provenance_preserves_exact_markup(self):
        self.write([claude_assistant(LONG, T0), claude_user(HOOK, T0)])
        self.assertEqual(retro.measure(self.path, 'claude', self.root)['user_prompts'], 1)
        self.assertEqual(ClaudeAdapter(SALT).read(self.path, self.root).human_prompt_count, 1)
        raw = {'step_index': 0, 'type': 'USER_INPUT', 'source': 'USER_EXPLICIT',
               'created_at': STAMP, 'content': HOOK}
        with contextlib.closing(Registry(self.root / 'store')) as store:
            captured = normalize_captured('antigravity', 'session.jsonl',
                                         (json.dumps(raw) + '\n').encode(), store)
            self.assertEqual(len(captured.excerpts['prompts']), 1)

    def context_message(self, kinds=None):
        row = rollout_user('# AGENTS.md instructions\nFollow repository rules.', STAMP)
        row['payload']['content'].append(
            {'type': 'input_text', 'text': '<environment_context>fixture</environment_context>'})
        row['payload']['internal_chat_message_metadata_passthrough'] = {
            'content_item_kinds': kinds if kinds is not None else [
                'agents_md.instructions', 'environments.environment_context']}
        return row

    def test_codex_context_metadata_preserves_real_correction_context(self):
        self.write([rollout_meta(STAMP), rollout_assistant(LONG, STAMP),
                    self.context_message(), rollout_user('no, wrong file', STAMP)])
        row = retro.measure_codex(self.path, self.root)
        self.assertEqual((row['user_prompts'], row['correction_candidates']), (1, 1))
        adapter = CodexAdapter(SALT)
        result = adapter.read(self.path, self.root)
        self.assertEqual(result.human_prompt_count, 1)
        self.assertEqual(result.machine_message_origins, ({'sequence': 2,
            'origin': 'machine', 'method': 'codex_context_item_kinds'},))
        evidence = adapter.private_prompt_evidence(self.path, self.root, lambda x: x)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(next(iter(evidence.values()))['context_chars'], len(LONG.strip()))
        with mock.patch.object(retro, 'codex_sessions_dir', return_value=self.root):
            moments = retro._moments_codex({'transcript': self.path.name})
        self.assertEqual(len(moments), 1)
        self.assertNotIn('AGENTS.md', json.dumps(moments))

    def test_codex_context_only_retains_raw_without_task_candidate(self):
        self.write([rollout_meta(STAMP), self.context_message()])
        with contextlib.closing(Registry(self.root / 'store')) as store:
            captured = normalize_captured('codex', self.path.name, self.path.read_bytes(), store)
            self.assertFalse(captured.included)
            self.assertEqual(captured.excerpts['prompts'], {})
            self.assertEqual(captured.records, ())
            self.assertEqual(store.read_artifact(captured.raw_ref)[0], self.path.read_bytes())
            self.assertEqual(captured.observations['machine_messages']['value'][0]['method'],
                             'codex_context_item_kinds')

    def test_codex_mixed_unknown_and_malformed_metadata_do_not_drop_requests(self):
        variants = [None, {}, [], 'agents_md.instructions',
                    {'content_item_kinds': []},
                    {'content_item_kinds': ['agents_md.instructions']},
                    {'content_item_kinds': ['agents_md.instructions'] * 3},
                    {'content_item_kinds': ['agents_md.instructions', 'user.text']},
                    {'content_item_kinds': ['agents_md.instructions', 'unknown.future']},
                    {'content_item_kinds': ['agents_md.instructions', {}]}]
        for metadata in variants:
            with self.subTest(metadata=metadata):
                message = self.context_message()
                message['payload']['internal_chat_message_metadata_passthrough'] = metadata
                self.write([rollout_meta(STAMP), message])
                self.assertEqual(CodexAdapter(SALT).read(self.path, self.root).human_prompt_count, 1)
                self.assertEqual(retro.measure_codex(self.path, self.root)['user_prompts'], 1)

    def test_codex_human_agents_quote_is_not_a_new_text_opener(self):
        message = rollout_user('# AGENTS.md instructions\nPlease critique this example.', STAMP)
        message['payload']['internal_chat_message_metadata_passthrough'] = {
            'content_item_kinds': ['user.text']}
        self.write([rollout_meta(STAMP), message])
        self.assertEqual(CodexAdapter(SALT).read(self.path, self.root).human_prompt_count, 1)
        self.assertEqual(retro.measure_codex(self.path, self.root)['user_prompts'], 1)

    def test_codex_image_feedback_is_retained_with_or_without_metadata(self):
        for metadata in (None, {'content_item_kinds': [
                'user.text', 'user.image', 'user.text', 'user.text']}):
            with self.subTest(metadata=metadata):
                message = rollout_user('', STAMP)
                message['payload']['content'] = [
                    {'type': 'input_text', 'text': '<image name="example">'},
                    {'type': 'input_image', 'image_url': 'data:image/png;base64,AA=='},
                    {'type': 'input_text', 'text': '</image>'},
                    {'type': 'input_text', 'text': 'no, wrong result in this screenshot'}]
                message['payload']['internal_chat_message_metadata_passthrough'] = metadata
                self.write([rollout_meta(STAMP), rollout_assistant(LONG, STAMP), message])
                adapter = CodexAdapter(SALT)
                self.assertEqual(adapter.read(self.path, self.root).human_prompt_count, 1)
                row = retro.measure_codex(self.path, self.root)
                self.assertEqual((row['user_prompts'], row['correction_candidates']), (1, 1))
                evidence = adapter.private_prompt_evidence(self.path, self.root, lambda x: x)
                self.assertEqual(len(evidence), 1)
                item = next(iter(evidence.values()))
                self.assertIn('no, wrong result', item['user_turn'])
                self.assertEqual(item['context_chars'], len(LONG.strip()))
                with mock.patch.object(retro, 'codex_sessions_dir', return_value=self.root):
                    moments = retro._moments_codex({'transcript': self.path.name})
                self.assertEqual(len(moments), 1)

    def test_codex_image_markup_is_not_machine_provenance(self):
        body = '<image name="example">an example</image> Explain this markup.'
        self.write([rollout_meta(STAMP), rollout_user(body, STAMP)])
        self.assertEqual(CodexAdapter(SALT).read(self.path, self.root).human_prompt_count, 1)
        self.assertEqual(retro.measure_codex(self.path, self.root)['user_prompts'], 1)
        # Explicit caller policy and the older Claude fallback are unchanged.
        self.assertEqual(CodexAdapter(SALT, machine_prompt_openers=['<image']).read(
            self.path, self.root).human_prompt_count, 0)
        self.assertFalse(retro.is_human_prompt({}, body))

    def test_existing_ledger_is_rebuilt_after_definition_change(self):
        self.write([rollout_meta(STAMP), rollout_assistant(LONG, STAMP), rollout_user(HOOK, STAMP)])
        old = retro.measure_codex(self.path, self.root)
        old.update(schema=8, user_prompts=1, correction_candidates=1)
        with mock.patch.object(retro, 'ensure_work_dir'), \
             mock.patch.object(retro, 'transcript_roots', return_value=[('codex', self.root)]), \
             mock.patch.object(retro, 'load_rows', return_value=[old]), \
             mock.patch.object(retro, 'load_state') as state, \
             mock.patch.object(retro, 'METRICS_FILE', self.root / 'metrics.jsonl'), \
             mock.patch.object(retro, 'STATE_FILE', self.root / 'state.json'), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            retro.cmd_extract(mock.Mock(rebuild=False))
        state.assert_not_called()
        self.assertIn('rebuilding all', output.getvalue())
        rows = [json.loads(line) for line in (self.root / 'metrics.jsonl').read_text().splitlines()]
        self.assertEqual(rows[0]['user_prompts'], 0)
        self.assertEqual(rows[0]['schema'], retro.SCHEMA_VERSION)


if __name__ == '__main__':
    unittest.main()
