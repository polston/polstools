"""Source-specific adapter tests over minimal synthetic transcripts."""

import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.adapters import ClaudeAdapter, CodexAdapter  # noqa: E402
from retro_eval.schema import CapabilityState, SpanKind  # noqa: E402


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class ClaudeAdapterTests(unittest.TestCase):
    def test_skill_outcome_stays_bound_to_the_attributed_skill_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "session.jsonl"
            write_jsonl(path, [
                {"type": "user", "promptSource": "typed",
                 "message": {"role": "user", "content": "work"}},
                {"type": "assistant", "attributionSkill": "skill-a",
                 "attributionSkillOutcome": "failure",
                 "attributionSkillChainId": "chain-a",
                 "attributionSkillChainPosition": "not-an-integer",
                 "message": {"role": "assistant", "content": "working"}},
                {"type": "assistant", "attributionSkillOutcome": "success",
                 "message": {"role": "assistant", "content": "done"}},
            ])

            result = ClaudeAdapter(id_salt=b"local").read(path, root)
            terminal = next(record for record in result.records
                            if record.span_kind == "retro.skill.end")
            start = next(record for record in result.records
                         if record.span_kind == "retro.skill.start")
            self.assertEqual("failure", terminal.attributes["outcome"])
            self.assertEqual(0, start.attributes["chain_position"])

    def test_direct_human_is_kept_and_tool_generated_user_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project" / "session.jsonl"
            rows = [
                {"type": "user", "sessionId": "private-session", "timestamp": "2026-08-01T12:00:00Z",
                 "promptSource": "typed", "message": {"role": "user", "content": "do the work"}},
                {"type": "assistant", "sessionId": "private-session", "timestamp": "2026-08-01T12:00:01Z",
                 "attributionSkill": "skill-a", "message": {"role": "assistant", "content": [
                     {"type": "tool_use", "id": "call-1", "name": "Read", "input": {"path": "secret"}}]}},
                {"type": "user", "sessionId": "private-session", "timestamp": "2026-08-01T12:00:02Z",
                 "sourceToolAssistantUUID": "call-1", "message": {"role": "user", "content": [
                     {"type": "tool_result", "tool_use_id": "call-1", "is_error": True,
                      "content": "No such file or directory: private output"}]}},
                {"type": "assistant", "sessionId": "private-session", "timestamp": "2026-08-01T12:00:03Z",
                 "message": {"role": "assistant", "content": "done", "usage": {
                     "input_tokens": 10, "cache_read_input_tokens": 3, "output_tokens": 4}}},
            ]
            write_jsonl(path, rows)
            result = ClaudeAdapter(id_salt=b"local").read(path, Path(tmp))
            self.assertTrue(result.included)
            self.assertEqual(result.human_prompt_count, 1)
            self.assertEqual(result.tool_result_count, 1)
            self.assertEqual(result.skills, ("skill-a",))
            self.assertNotIn("private-session", repr(result.records))
            self.assertNotIn("private output", repr(result.records))
            self.assertNotIn("skill-a", repr(result.records))
            self.assertIn(SpanKind.TOOL, {record.span_kind for record in result.records})
            starts = [record for record in result.records
                      if record.span_kind == "retro.skill.start"]
            ends = [record for record in result.records
                    if record.span_kind == "retro.skill.end"]
            self.assertEqual(1, len(starts))
            self.assertEqual(1, len(ends))
            self.assertEqual(starts[0].attributes["invocation_id"],
                             ends[0].attributes["invocation_id"])
            self.assertEqual("explicit_attribution",
                             starts[0].attributes["start_basis"])
            self.assertEqual("not_observable",
                             ends[0].attributes["outcome"])
            tool_result = next(record for record in result.records
                               if record.span_kind == "retro.tool_result")
            self.assertEqual("error", tool_result.status)
            self.assertEqual("Read", tool_result.tool_kind)
            self.assertEqual("missing_target",
                             tool_result.attributes["error_kind"])
            self.assertEqual("source_missing_target",
                             tool_result.attributes["error_reason"])
            root_record = next(record for record in result.records
                               if record.span_kind is SpanKind.TRACE)
            self.assertEqual(root_record.input_tokens, 10)
            self.assertEqual(root_record.cached_input_tokens, 3)
            self.assertEqual(root_record.output_tokens, 4)
            self.assertEqual(root_record.status, "complete")
            self.assertEqual(1, root_record.attributes["skill_count"])

    def test_subagent_and_non_direct_session_are_explicitly_classified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub = root / "project" / "session" / "subagents" / "agent.jsonl"
            write_jsonl(sub, [{"type": "user", "sessionId": "s", "promptSource": "typed",
                               "message": {"role": "user", "content": "work"}}])
            sub_result = ClaudeAdapter(id_salt=b"local").read(sub, root)
            self.assertTrue(sub_result.is_subagent)

            injected = root / "project" / "injected.jsonl"
            write_jsonl(injected, [{"type": "user", "sessionId": "s2", "promptSource": "system",
                                    "message": {"role": "user", "content": "injected"}}])
            result = ClaudeAdapter(id_salt=b"local").read(injected, root)
            self.assertFalse(result.included)
            self.assertEqual(result.exclusion_reason, "no direct-human prompt")


class CodexAdapterTests(unittest.TestCase):
    def rows(self, thread_source="user"):
        return [
            {"type": "session_meta", "timestamp": "2026-08-01T12:00:00Z", "payload": {
                "id": "private-session", "thread_source": thread_source, "cli_version": "1.0"}},
            {"type": "response_item", "timestamp": "2026-08-01T12:00:01Z", "payload": {
                "type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "please inspect it"}]}},
            {"type": "response_item", "timestamp": "2026-08-01T12:00:02Z", "payload": {
                "type": "function_call", "name": "exec_command", "call_id": "call-1",
                "arguments": "{\"cmd\":\"private command\"}"}},
            {"type": "response_item", "timestamp": "2026-08-01T12:00:03Z", "payload": {
                "type": "function_call_output", "call_id": "call-1", "output": "private result"}},
            {"type": "response_item", "timestamp": "2026-08-01T12:00:04Z", "payload": {
                "type": "message", "role": "assistant", "phase": "final_answer", "content": [
                    {"type": "output_text", "text": "done"}]}},
            {"type": "event_msg", "timestamp": "2026-08-01T12:00:05Z", "payload": {
                "type": "token_count", "info": {"total_token_usage": {
                    "input_tokens": 100, "cached_input_tokens": 60,
                    "output_tokens": 12, "reasoning_output_tokens": 5,
                    "cache_write_input_tokens": 7, "total_tokens": 117}}}},
            {"type": "event_msg", "timestamp": "2026-08-01T12:00:06Z", "payload": {
                "type": "task_complete", "duration_ms": 6000}},
        ]

    def test_only_user_main_threads_are_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "rollout.jsonl"
            write_jsonl(path, self.rows())
            result = CodexAdapter(id_salt=b"local").read(path, root)
            self.assertTrue(result.included)
            self.assertEqual(result.human_prompt_count, 1)
            self.assertEqual(result.tool_call_count, 1)
            self.assertEqual(result.tool_result_count, 1)
            self.assertNotIn("private-session", repr(result.records))
            self.assertNotIn("private command", repr(result.records))
            self.assertNotIn("private result", repr(result.records))
            root_record = next(record for record in result.records
                               if record.span_kind is SpanKind.TRACE)
            self.assertEqual(root_record.input_tokens, 100)
            self.assertEqual(root_record.cached_input_tokens, 60)
            self.assertEqual(root_record.cache_write_input_tokens, 7)
            self.assertEqual(root_record.reasoning_output_tokens, 5)
            self.assertEqual(root_record.duration_ms, 6000)
            self.assertEqual(root_record.status, "complete")

            automation = root / "automation.jsonl"
            write_jsonl(automation, self.rows("automation"))
            excluded = CodexAdapter(id_salt=b"local").read(automation, root)
            self.assertFalse(excluded.included)
            self.assertEqual(excluded.exclusion_reason, "thread_source:automation")

    def test_capabilities_are_source_honest(self):
        adapter = CodexAdapter(id_salt=b"local")
        self.assertEqual(adapter.capabilities["human_provenance"].state,
                         CapabilityState.AVAILABLE)
        self.assertEqual(adapter.capabilities["skill_attribution"].state,
                         CapabilityState.UNAVAILABLE)

    def test_every_source_explicitly_declares_standard_capabilities(self):
        expected = set(json.loads(
            (PLUGIN_ROOT / "profiles" / "capabilities.json").read_text(encoding="utf-8")
        )["capabilities"])
        self.assertEqual(expected, set(ClaudeAdapter(id_salt=b"local").capabilities))
        self.assertEqual(expected, set(CodexAdapter(id_salt=b"local").capabilities))

    def test_explicit_session_exclusion_and_handoff_span(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "rollout.jsonl"
            rows = self.rows()
            rows.insert(3, {"type": "response_item", "timestamp": "2026-08-01T12:00:02Z",
                            "payload": {"type": "custom_tool_call", "name": "spawn_agent",
                                        "call_id": "h1", "input": "private task"}})
            write_jsonl(path, rows)
            included = CodexAdapter(id_salt=b"local").read(path, root)
            self.assertIn(SpanKind.HANDOFF, {record.span_kind for record in included.records})
            excluded = CodexAdapter(
                id_salt=b"local", excluded_session_ids={"private-session"}).read(path, root)
            self.assertFalse(excluded.included)
            self.assertEqual(excluded.exclusion_reason, "active_or_explicitly_excluded")


if __name__ == "__main__":
    unittest.main()
