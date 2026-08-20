"""Tests for cache_ttl. Stdlib unittest; no third-party runner."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plugins" / "retro" / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures  # noqa: E402

T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestFixtures(unittest.TestCase):
    def test_build_corpus_writes_main_and_subagent_transcripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "proj-a", "session": "s1", "rows": [
                    fixtures.usage_row("r1", "claude-opus-5", 100, 10, 0, 5, T0),
                ]},
                {"project": "proj-a", "session": "s1", "subagent": True, "rows": [
                    fixtures.usage_row("r2", "claude-sonnet-5", 200, 0, 20, 5, T0),
                ]},
            ])
            main = root / "proj-a" / "s1.jsonl"
            sub = root / "proj-a" / "s1" / "subagents" / "agent-0.jsonl"
            self.assertTrue(main.is_file())
            self.assertTrue(sub.is_file())
            rec = json.loads(main.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(rec["requestId"], "r1")
            self.assertEqual(rec["message"]["usage"]["cache_read_input_tokens"], 100)


import cache_ttl  # noqa: E402


class TestCollect(unittest.TestCase):
    def test_classifies_main_thread_and_subagent_by_depth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("main1", "claude-opus-5", 10, 1, 0, 1, T0)]},
                {"project": "p", "session": "s", "subagent": True, "rows": [
                    fixtures.usage_row("sub1", "claude-sonnet-5", 20, 0, 2, 1, T0)]},
            ])
            requests, _ = cache_ttl.collect(root)
            self.assertTrue(requests["main1"]["main"])
            self.assertFalse(requests["sub1"]["main"])

    def test_deduplicates_the_same_request_id_across_two_files(self):
        """A resumed session copies rows into a new transcript. Counting each
        file separately would double-count the request."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = fixtures.usage_row("dup", "claude-opus-5", 1000, 50, 0, 5, T0)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "first", "rows": [row]},
                {"project": "p", "session": "second", "rows": [row]},
            ])
            requests, _ = cache_ttl.collect(root)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests["dup"]["read"], 1000)

    def test_settled_row_wins_when_rows_of_one_request_disagree(self):
        """Streaming writes a zeroed placeholder beside the full row. Taking
        the first row would zero out a real request."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("r", "claude-opus-5", 0, 26, 0, 0, T0),
                    fixtures.usage_row("r", "claude-opus-5", 992810, 26, 0, 40,
                                       T0 + timedelta(seconds=30)),
                ]},
            ])
            requests, _ = cache_ttl.collect(root)
            self.assertEqual(requests["r"]["read"], 992810)

    def test_request_start_is_the_earliest_of_its_rows(self):
        """Rows of one request span up to several minutes; the gap measures
        from when the request started."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            late = T0 + timedelta(seconds=353)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("r", "claude-opus-5", 0, 1, 0, 0, T0),
                    fixtures.usage_row("r", "claude-opus-5", 500, 1, 0, 9, late),
                ]},
            ])
            requests, _ = cache_ttl.collect(root)
            self.assertEqual(requests["r"]["start"], T0)

    def test_rows_without_a_timestamp_are_skipped_and_tallied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = fixtures.usage_row("r", "claude-opus-5", 1, 1, 0, 1, T0)
            del bad["timestamp"]
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [bad]}])
            requests, skipped = cache_ttl.collect(root)
            self.assertEqual(len(requests), 0)
            self.assertEqual(skipped["no_timestamp"], 1)


if __name__ == "__main__":
    unittest.main()
