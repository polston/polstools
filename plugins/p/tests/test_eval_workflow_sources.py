"""Cross-harness adapter conformance from authored captured source bytes."""
import sys
import errno
import os
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.workflow.sources import capture, normalize_captured, reporting_session_ids
from retro_eval.workflow.registry import Registry
from retro_eval.workflow.errors import PolicyDenied
from retro_eval.adapters.registry import AdapterRegistry, default_registry
from retro_eval.workflow.records import canonical, Record, evaluate_delivery
from retro_eval.workflow.importing import candidate_records, confirm_link, linked_attempts, import_manifest, legacy_observation
from retro_eval.workflow.errors import WorkflowError
from test_eval_workflow_records import TIME, case, outcome


def jsonl(rows):
    return b''.join(canonical(row) + b'\n' for row in rows)


def source_fixture(source, *, usage=True):
    if source == 'claude':
        message = {'role': 'assistant', 'id': 'message-a',
                   'content': [{'type': 'text', 'text': 'I will perform the check.'}]}
        if usage:
            message['usage'] = {'input_tokens': 10, 'output_tokens': 5, 'cache_read_input_tokens': 0}
        return jsonl([
            {'type': 'user', 'timestamp': TIME, 'promptSource': 'typed',
             'message': {'role': 'user', 'content': 'Please perform the check.'}},
            {'type': 'assistant', 'timestamp': '2026-01-01T00:00:03Z', 'message': message}])
    if source == 'codex':
        rows = [
            {'type': 'session_meta', 'payload': {'thread_source': 'user'}},
            {'type': 'response_item', 'timestamp': TIME, 'payload': {'type': 'message',
                'role': 'user', 'content': [{'type': 'input_text', 'text': 'Please perform the check.'}]}},
            {'type': 'response_item', 'timestamp': '2026-01-01T00:00:03Z', 'payload': {'type': 'message',
                'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'I will perform the check.'}]}},
            {'type': 'event_msg', 'payload': {'type': 'task_complete', 'duration_ms': 1000}},
        ]
        if usage:
            rows.append({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                'total_token_usage': {'input_tokens': 10, 'output_tokens': 5, 'cached_input_tokens': 0}}}})
        return jsonl(rows)
    return jsonl([
        {'step_index': 0, 'type': 'USER_INPUT', 'source': 'USER_EXPLICIT',
         'created_at': TIME, 'content': 'Please perform the check.'},
        {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'source': 'MODEL',
         'created_at': '2026-01-01T00:00:03Z', 'content': 'I will perform the check.'}])


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Registry(Path(self.temp.name) / 'store')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def read(self, source, data=None, usage=True):
        return normalize_captured(source, 'sample/session.jsonl',
            source_fixture(source, usage=usage) if data is None else data, self.store)

    def test_H01_H09_same_acceptance_independent_of_source_accounting(self):
        for source in ('claude', 'codex', 'antigravity'):
            captured = self.read(source)
            self.assertTrue(captured.included)
            c = case(source)
            checks = [outcome(c, k, 'pass') for k in ('build', 'behavior')]
            self.assertEqual(evaluate_delivery(c, checks, lambda r: True).status, 'pass')
            if source == 'antigravity':
                self.assertEqual(captured.observations['input_tokens']['availability'], 'not_supported')
                self.assertIsNone(captured.observations['input_tokens']['value'])

    def test_H02_prose_is_not_terminal_event_or_delivery(self):
        left, right = self.read('claude'), self.read('codex')
        self.assertNotEqual(left.observations['completion']['method'], right.observations['completion']['method'])
        for captured in (left, right):
            c = next(r for r in candidate_records(captured, created_at=TIME) if r.ref.kind == 'case')
            self.assertEqual(evaluate_delivery(c, [], lambda r: True).status, 'unknown')

    def test_H03_available_capability_missing_field_is_not_zero(self):
        for source in ('claude', 'codex'):
            captured = self.read(source, usage=False)
            self.assertEqual(captured.observations['input_tokens']['availability'], 'not_observed')
            self.assertIsNone(captured.observations['input_tokens']['value'])

    def test_H04_unknown_wrapper_remains_heuristic(self):
        raw = source_fixture('codex').replace(b'Please perform the check.', b'<unknown-wrapper>system text</unknown-wrapper>')
        captured = self.read('codex', raw)
        self.assertEqual(captured.observations['origin']['value'],
                         'heuristic_user_role_after_known_context_and_wrapper_filter')

    def test_H05_no_usage_observation_from_profile_alone(self):
        captured = self.read('claude', usage=False)
        self.assertFalse(any(r['span_kind'] == 'retro.skill.start' for r in captured.records))
        self.assertIsNone(captured.observations['input_tokens']['value'])

    def test_H06_append_after_capture_cannot_change_fingerprint_or_trace(self):
        root = Path(self.temp.name) / 'source'
        root.mkdir()
        path = root / 'session.jsonl'
        before = source_fixture('claude')
        path.write_bytes(before)
        data = capture(path, root, 10000)
        path.write_bytes(before + b'{"type":"unsupported"}\n')
        captured = self.read('claude', data)
        self.assertEqual(self.store.read_artifact(captured.raw_ref)[0], before)
        self.assertEqual(captured.diagnostics, ())

    def test_capture_links_preserve_containment_and_loop_errors(self):
        root = Path(self.temp.name) / 'links'
        root.mkdir()
        (root / 'ordinary').write_bytes(b'allowed')
        outside = root.parent / 'outside'
        outside.write_bytes(b'outside')
        alias, escape, loop = [root / name for name in ('alias', 'escape', 'loop')]
        try:
            alias.symlink_to('ordinary')
            escape.symlink_to(outside)
            loop.symlink_to('loop')
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links unavailable')
        self.assertEqual(capture(alias, root, 100), b'allowed')
        observed = []
        with self.assertRaises(PolicyDenied):
            capture(escape, root, 100, on_read=lambda *args: observed.append(args))
        self.assertEqual(observed, [])
        with self.assertRaises(OSError) as raised:
            capture(loop, root, 100)
        self.assertEqual(raised.exception.errno, errno.ELOOP)

    def test_capture_rejects_ancestor_substitution_before_read_callback(self):
        root = Path(self.temp.name) / 'source'
        nested = root / 'nested'
        nested.mkdir(parents=True)
        (nested / 'record').write_bytes(b'authorized')
        outside = root.parent / 'outside'
        outside.mkdir()
        (outside / 'record').write_bytes(b'outside')
        original_open = os.open
        swapped, observed = [], []
        def racing_open(path, flags, *args, **kwargs):
            if not swapped:
                nested.rename(root / 'retained')
                nested.symlink_to(outside, target_is_directory=True)
                swapped.append(True)
            return original_open(path, flags, *args, **kwargs)
        with patch('retro_eval.workflow.sources.os.open', side_effect=racing_open):
            with self.assertRaises((OSError, PolicyDenied)):
                capture(nested / 'record', root, 100,
                        on_read=lambda *args: observed.append(args))
        self.assertTrue(swapped)
        self.assertEqual(observed, [])

    def test_capture_preserves_byte_budget_and_denies_unsupported_open(self):
        from retro_eval.workflow.errors import BudgetExhausted
        root = Path(self.temp.name) / 'source'
        root.mkdir()
        path = root / 'record'
        path.write_bytes(b'123456789')
        observed = []
        with self.assertRaises(BudgetExhausted):
            capture(path, root, 3, on_read=lambda *args: observed.append(args))
        self.assertEqual(observed, [(b'1234', False)])
        with patch('retro_eval.workflow.sources.ANCHORED_OPEN_SUPPORTED', False):
            with patch('retro_eval.workflow.sources.os.open') as opened:
                with self.assertRaisesRegex(PolicyDenied, 'directory-relative'):
                    capture(path, root, 100)
                opened.assert_not_called()
        with self.assertRaises(PolicyDenied):
            capture(root, root, 100)
        if hasattr(os, 'mkfifo'):
            fifo = root / 'pipe'
            os.mkfifo(fifo)
            with self.assertRaises(PolicyDenied):
                capture(fifo, root, 100)

    def test_closed_window_excludes_future_events_from_interpretation(self):
        for source in ('claude', 'codex', 'antigravity'):
            captured = normalize_captured(source, 'sample/session.jsonl',
                source_fixture(source), self.store, window_end=TIME)
            self.assertTrue(captured.included)
            self.assertIn('after_closed_window', {d['reason'] for d in captured.diagnostics})
            self.assertNotIn('I will perform', canonical(captured.excerpts).decode())
            self.assertEqual(self.store.read_artifact(captured.raw_ref)[0], source_fixture(source))

    def test_reporting_session_is_excluded_before_prompt_evidence(self):
        raw = source_fixture('codex').replace(b'"thread_source":"user"',
            b'"thread_source":"user","id":"authored-current-session"')
        captured = normalize_captured('codex', 'rollout-sample.jsonl', raw,
            self.store, exclude_session_ids={'authored-current-session'})
        self.assertFalse(captured.included)
        self.assertFalse(captured.excerpts['prompts'])
        self.assertEqual(reporting_session_ids({'CODEX_THREAD_ID': 'SAMPLE'}), {'sample'})

    def test_H07_parse_and_mapping_gaps_are_visible(self):
        raw = source_fixture('codex') + b'broken json\n' + b'{"type":"future_type"}\n'
        captured = self.read('codex', raw)
        self.assertEqual({d['reason'] for d in captured.diagnostics},
                         {'malformed_json_record', 'unmapped_record_type'})
        self.assertEqual(captured.observations['input_tokens']['availability'], 'not_observed')

    def test_configured_and_runtime_exclusions_both_prevent_candidate_evidence(self):
        for source in ('claude', 'codex', 'antigravity'):
            original = default_registry().get(source)
            options = dict(original.options, excluded_session_ids=['CONFIGURED'])
            adapters = AdapterRegistry([replace(original, options=options)])
            for runtime in ((), ('reporting',)):
                for identity in ('configured', 'reporting', 'ordinary'):
                    with self.subTest(source=source, runtime=runtime, identity=identity):
                        raw = source_fixture(source)
                        if source == 'claude':
                            raw += jsonl([{'type': 'assistant', 'timestamp': TIME,
                                'message': {'role': 'assistant', 'content': [
                                    {'type': 'tool_use', 'id': 'authored-tool',
                                     'name': 'Read', 'input': {'path': 'sample.txt'}}]}}])
                        relative = 'project/' + identity + '.jsonl'
                        if source == 'antigravity':
                            relative = identity + '/transcript_full.jsonl'
                        if source == 'codex':
                            raw = raw.replace(b'"thread_source":"user"',
                                ('"thread_source":"user","id":"%s"' % identity).encode())
                        captured = normalize_captured(source, relative, raw, self.store,
                            adapters=adapters, exclude_session_ids=runtime)
                        excluded = identity == 'configured' or identity in runtime
                        self.assertEqual(captured.included, not excluded)
                        self.assertEqual(bool(captured.excerpts['prompts']), not excluded)
                        if source == 'claude':
                            self.assertEqual(bool(captured.excerpts['tools']), not excluded)
                        self.assertEqual(bool(list(candidate_records(captured,
                            created_at=TIME))), not excluded)
                        self.assertEqual(self.store.read_artifact(captured.raw_ref)[0], raw)
            self.assertEqual(options['excluded_session_ids'], ['CONFIGURED'])

    def test_H08_explicit_common_duration_mapping(self):
        for source in ('claude', 'codex', 'antigravity'):
            measure = self.read(source).observations['captured_span_seconds']
            self.assertEqual(measure['method'], 'captured_timestamp_range')
            self.assertEqual(measure['value'], 3)
        # The Codex terminal duration is 1 second; it is not pooled as 3 seconds
        # of active work. Captured timestamp range is explicitly a different unit.

    def test_claude_repeated_message_usage_is_not_double_counted(self):
        raw = source_fixture('claude')
        raw += raw.splitlines()[-1] + b'\n'
        measure = self.read('claude', raw).observations['input_tokens']
        self.assertEqual(measure['value'], 10)
        self.assertEqual(measure['coverage']['duplicate_snapshots'], 1)

    def test_F26_legacy_defaults_are_unobserved(self):
        for field in ('status', 'main_or_subagent', 'input_tokens'):
            self.assertEqual(legacy_observation({field: 0}, field)['availability'], 'not_observed')

    def test_F27_confirmed_cross_source_and_F28_inferred_link(self):
        cases = []
        for source in ('claude', 'codex'):
            records = candidate_records(self.read(source), created_at=TIME)
            self.store.import_records(records)
            cases.append(next(r for r in records if r.ref.kind == 'case'))
        self.assertNotEqual(cases[0].ref, cases[1].ref)
        self.assertTrue(all(not c.payload['independent_unit_eligible'] for c in cases))
        evidence = self.store.publish_artifact(b'authored common-task mapping', 'public_synthetic')
        linked = confirm_link(cases, key='common', intent='One shared task', criteria=[],
            created_at=TIME, provenance={'status': 'authoritative', 'evidence_refs': [evidence]})
        self.store.append(linked, None)
        self.assertEqual(len(linked.payload['links']), 2)
        self.assertTrue(linked.payload['independent_unit_eligible'])
        attempts = linked_attempts(self.store, linked)
        self.assertEqual(len(attempts), 2)
        self.assertEqual({a.payload['harness'] for a in attempts}, {'claude', 'codex'})
        with self.assertRaises(WorkflowError):
            confirm_link(cases, key='bad', intent='Unconfirmed', criteria=[],
                created_at=TIME, provenance={'status': 'inferred', 'evidence_refs': [evidence]})

    def test_F32_explicit_import_requires_manifest(self):
        with self.assertRaises(WorkflowError):
            import_manifest(None, self.store)
        self.assertEqual(self.store.heads(), [])


if __name__ == '__main__':
    unittest.main()
