"""Oversized source batches preserve every call and its evidence identity."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_eval.adapters import ClaudeAdapter
from retro_eval.adapters.antigravity import AntigravityAdapter


def kind(record):
    return getattr(record.span_kind, "value", record.span_kind)


def claude_rows(count):
    def call(index):
        return {"type": "tool_use", "id": "call-" + str(index),
                "name": "Read", "input": {"index": index}}
    def result(index):
        return {"type": "tool_result", "tool_use_id": "call-" + str(index),
                "content": "result-" + str(index)}
    def message(role, content):
        return {"type": role, "message": {"role": role, "content": content}}
    return [
        {"type": "user", "promptSource": "typed", "message": {
            "role": "user", "content": "inspect"}},
        message("assistant", [call(i) for i in range(count)]),
        message("assistant", [call(count)]),
        message("user", [result(i) for i in range(count)]),
        message("user", [result(count)]),
    ]


def ag_rows(count):
    return [
        {"step_index": 0, "type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "inspect"},
        {"step_index": 1, "type": "PLANNER_RESPONSE", "source": "MODEL", "tool_calls": [
            {"name": "read_file", "args": {"index": i}} for i in range(count)]},
        {"step_index": 2, "type": "PLANNER_RESPONSE", "source": "MODEL", "tool_calls": [
            {"name": "read_file", "args": {"index": count}}]},
    ]


class IndexedSpanIdentityTests(unittest.TestCase):
    def test_exact_and_oversized_batches_keep_identity_order_and_evidence(self):
        for adapter_class, make_rows in ((ClaudeAdapter, claude_rows),
                                         (AntigravityAdapter, ag_rows)):
            for count in (1000, 1001):
                with self.subTest(adapter=adapter_class.__name__, count=count):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        path = root / "example.jsonl"
                        adapter = adapter_class(b"example")
                        rows = make_rows(count)
                        def write(values):
                            path.write_text("".join(json.dumps(r) + "\n" for r in values))
                        write(rows[:2])
                        prefix = [r.span_id for r in adapter.read(path, root).records
                                  if kind(r) == "tool"]
                        write(rows)
                        normalized = adapter.read(path, root).records
                        relevant = [r for r in normalized if kind(r) in ("tool", "retro.tool_result")]
                        evidence = adapter.private_tool_evidence(path, root, lambda s: s)
                        self.assertEqual(len(relevant), len({r.span_id for r in relevant}))
                        self.assertEqual({r.span_id for r in relevant}, set(evidence))
                        calls = [r for r in relevant if kind(r) == "tool"]
                        self.assertEqual(prefix, [r.span_id for r in calls[:count]])
                        for category in ("tool", "retro.tool_result"):
                            spans = [r for r in relevant if kind(r) == category]
                            self.assertTrue(all(a.sequence < b.sequence for a, b in zip(spans, spans[1:])))
                            for index, record in enumerate(spans):
                                item = evidence[record.span_id]
                                self.assertEqual({"index": index}, json.loads(item["tool_input"]))
                                if adapter_class is ClaudeAdapter:
                                    self.assertEqual("result-" + str(index), json.loads(item["tool_result"]))
                                else:
                                    self.assertNotIn("tool_result", item)
                        for index in range(min(count, 1000)):
                            expected = adapter.ids.make(adapter.trace_id(path, root), 1000 + index, "tool")
                            self.assertEqual(expected, calls[index].span_id)


if __name__ == "__main__":
    unittest.main()
