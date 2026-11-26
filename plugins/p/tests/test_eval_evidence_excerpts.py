"""Bounded private review evidence retains boundaries and declares omissions."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.adapters import ClaudeAdapter, CodexAdapter
from retro_eval.text_rules import evidence_excerpt
from retro_eval.taxonomy_packets import _shown


class EvidenceExcerptTests(unittest.TestCase):
    def test_boundaries_short_text_and_nested_display(self):
        text = "START" + "x" * 2000 + "END"
        shown = evidence_excerpt(text, 1200)
        self.assertEqual(1200, len(shown))
        self.assertTrue(shown.startswith("START"))
        self.assertTrue(shown.endswith("END"))
        self.assertIn("excerpt truncated", shown)
        nested = _shown(shown, 240)
        self.assertEqual(240, len(nested))
        self.assertTrue(nested.startswith("START"))
        self.assertTrue(nested.endswith("END"))
        self.assertIn("excerpt truncated", nested)
        self.assertEqual("short", evidence_excerpt("short", 240))
        self.assertEqual("x" * 240, evidence_excerpt("x" * 240, 240))
        tail = evidence_excerpt(text, 240, tail_only=True)
        self.assertIn("excerpt truncated", tail)
        self.assertTrue(tail.endswith("END"))

    def test_adapters_redact_before_excerpt_and_preserve_result_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = "BEGIN private " + "x" * 2000 + " private No such file END"
            cases = [
                (ClaudeAdapter, [
                    {"type": "assistant", "message": {"content": [
                        {"type": "tool_use", "id": "call", "name": "Bash",
                         "input": {"command": raw}}]}},
                    {"type": "user", "message": {"content": [
                        {"type": "tool_result", "tool_use_id": "call",
                         "content": raw, "is_error": True}]}}]),
                (CodexAdapter, [
                    {"type": "session_meta", "payload": {"thread_source": "user"}},
                    {"type": "response_item", "payload": {
                        "type": "function_call", "call_id": "call", "name": "shell",
                        "arguments": raw}},
                    {"type": "response_item", "payload": {
                        "type": "function_call_output", "call_id": "call", "output": raw}}]),
            ]
            for adapter_class, rows in cases:
                with self.subTest(adapter=adapter_class.__name__):
                    path = root / (adapter_class.__name__ + ".jsonl")
                    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
                    observed = []
                    def redactor(text):
                        observed.append(text)
                        return text.replace("private", "<removed>")
                    evidence = adapter_class(b"local").private_tool_evidence(path, root, redactor)
                    self.assertTrue(any(len(text) > 2000 for text in observed))
                    for item in evidence.values():
                        for key in ("tool_input", "tool_result"):
                            self.assertNotIn("private", item[key])
                            self.assertIn("excerpt truncated", item[key])
                            self.assertIn("BEGIN", item[key])
                            self.assertIn("No such file END", item[key])


if __name__ == "__main__":
    unittest.main()
