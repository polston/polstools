"""Owned-hook lifecycle telemetry stays complete, bounded, and content-free."""

import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.hook_lifecycle import evaluate_owned_hook_events  # noqa: E402


def lifecycle(invocation, hook="format.user-prompt-submit"):
    base = {
        "schema_version": 1, "source": "polstools.format",
        "hook_id": hook, "hook_version": "1",
        "trigger_kind": "UserPromptSubmit", "invocation_id": invocation,
    }
    return [
        {**base, "event": "opportunity", "observed_at_ns": 1},
        {**base, "event": "start", "observed_at_ns": 2},
        {**base, "event": "end", "observed_at_ns": 3, "status": "ok",
         "latency_ms": 1, "injected_bytes": 10,
         "content_sha256": "a" * 64},
    ]


class OwnedHookLifecycleTests(unittest.TestCase):
    def write(self, path, events):
        path.write_text("".join(json.dumps(item) + "\n" for item in events),
                        encoding="utf-8")

    def test_complete_fixture_meets_owned_capture_thresholds_only(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "owned-hook-events.jsonl"
            self.write(path, lifecycle("a") + lifecycle("b"))
            report = evaluate_owned_hook_events(
                path, expected_invocations=2, baseline_normalized_bytes=100_000)
        self.assertEqual(1.0, report["capture_precision"])
        self.assertEqual(1.0, report["capture_recall"])
        self.assertEqual(0.0, report["unmatched_terminal_rate"])
        self.assertLessEqual(report["added_normalized_byte_share"], 0.02)
        self.assertEqual("repository_owned_wrapped_hooks_only",
                         report["coverage"])
        self.assertEqual("not_observable",
                         report["harness_wide_opportunity_coverage"])

    def test_missing_terminal_is_visible_and_not_retried_away(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "owned-hook-events.jsonl"
            events = lifecycle("a") + lifecycle("b")[:-1]
            self.write(path, events)
            report = evaluate_owned_hook_events(
                path, expected_invocations=2, baseline_normalized_bytes=100_000)
        self.assertEqual(0.5, report["capture_recall"])
        self.assertEqual(0.5, report["unmatched_terminal_rate"])

    def test_message_content_or_unknown_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "owned-hook-events.jsonl"
            events = lifecycle("a")
            events[-1]["message"] = "must not persist"
            self.write(path, events)
            with self.assertRaisesRegex(ValueError, "content-free"):
                evaluate_owned_hook_events(
                    path, expected_invocations=1,
                    baseline_normalized_bytes=100_000)


if __name__ == "__main__":
    unittest.main()
