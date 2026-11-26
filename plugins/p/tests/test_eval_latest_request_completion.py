"""A prior response cannot complete a newer pending request."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.adapters import ClaudeAdapter, CodexAdapter
from retro_eval.adapters.antigravity import AntigravityAdapter


def claude_user(text, source="typed"):
    return {"type": "user", "promptSource": source,
            "message": {"role": "user", "content": text}}


def claude_answer():
    return {"type": "assistant", "message": {"role": "assistant", "content": "Done"}}


def codex_user(text):
    return {"type": "response_item", "payload": {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": text}]}}


def codex_event(kind):
    return {"type": "event_msg", "payload": {"type": kind, "duration_ms": 900}}


def ag_step(index, kind, text, source):
    return {"step_index": index, "type": kind, "source": source, "content": text}


class LatestRequestCompletionTests(unittest.TestCase):
    def read(self, adapter_class, rows):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "example.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            return adapter_class(b"example").read(path, root).records[0]

    def test_new_request_is_open_until_new_source_completion(self):
        cases = [
            (ClaudeAdapter, [claude_user("first"), claude_answer(), claude_user("second")],
             claude_answer()),
            (CodexAdapter, [
                {"type": "session_meta", "payload": {"thread_source": "user"}},
                codex_user("first"), codex_event("task_complete"), codex_user("second")],
             codex_event("task_complete")),
            (AntigravityAdapter, [
                ag_step(0, "USER_INPUT", "first", "USER_EXPLICIT"),
                ag_step(1, "PLANNER_RESPONSE", "Done", "MODEL"),
                ag_step(2, "USER_INPUT", "second", "USER_EXPLICIT")],
             ag_step(3, "PLANNER_RESPONSE", "Done again", "MODEL")),
        ]
        for adapter, rows, answer in cases:
            with self.subTest(adapter=adapter.__name__):
                pending = self.read(adapter, rows)
                self.assertEqual("open", pending.status)
                if adapter is CodexAdapter:
                    self.assertEqual(0, pending.duration_ms)
                self.assertEqual("complete", self.read(adapter, rows + [answer]).status)

    def test_codex_new_native_turn_clears_completed_or_aborted_state(self):
        for previous in ("task_complete", "turn_aborted"):
            for next_item in (codex_user("next"), codex_event("task_started")):
                with self.subTest(previous=previous, next_item=next_item):
                    result = self.read(CodexAdapter, [
                        {"type": "session_meta", "payload": {"thread_source": "user"}},
                        codex_user("first"), codex_event(previous), next_item])
                    self.assertEqual("open", result.status)
                    self.assertEqual(0, result.duration_ms)

    def test_nonhuman_claude_input_does_not_open_a_new_request(self):
        result = self.read(ClaudeAdapter, [claude_user("first"), claude_answer(),
                                          claude_user("injected context", "system")])
        self.assertEqual("complete", result.status)


if __name__ == "__main__":
    unittest.main()
