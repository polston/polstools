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


class TestChains(unittest.TestCase):
    def _corpus(self, root, offsets):
        rows = [fixtures.usage_row("r%d" % i, "claude-opus-5", 100, 5, 0, 1,
                                   T0 + timedelta(seconds=off))
                for i, off in enumerate(offsets)]
        fixtures.build_corpus(root, [
            {"project": "p", "session": "s", "rows": rows}])

    def test_chain_is_ordered_by_start_not_file_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("late", "claude-opus-5", 1, 1, 0, 1,
                                       T0 + timedelta(seconds=60)),
                    fixtures.usage_row("early", "claude-opus-5", 1, 1, 0, 1, T0),
                ]}])
            requests, _ = cache_ttl.collect(root)
            chain = list(cache_ttl.chains(requests).values())[0]
            self.assertEqual([r["rid"] for r in chain], ["early", "late"])

    def test_opener_has_no_gap_and_others_measure_from_previous_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._corpus(root, [0, 30, 630])
            requests, _ = cache_ttl.collect(root)
            chain = list(cache_ttl.chains(requests).values())[0]
            gaps = [gap for _, gap in cache_ttl.gap_seconds(chain)]
            self.assertIsNone(gaps[0])
            self.assertEqual(gaps[1], 30.0)
            self.assertEqual(gaps[2], 600.0)

    def test_subagent_requests_are_excluded_from_main_chains(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("m", "claude-opus-5", 1, 1, 0, 1, T0)]},
                {"project": "p", "session": "s", "subagent": True, "rows": [
                    fixtures.usage_row("s1", "claude-sonnet-5", 1, 0, 1, 1, T0)]},
            ])
            requests, _ = cache_ttl.collect(root)
            main = cache_ttl.chains(requests, main_only=True)
            subs = cache_ttl.chains(requests, main_only=False)
            self.assertEqual(sum(len(c) for c in main.values()), 1)
            self.assertEqual(sum(len(c) for c in subs.values()), 1)

    def test_band_table_puts_each_gap_in_the_right_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # gaps after the opener: 30s, 120s, 400s, 700s, 1800s, 7200s
            self._corpus(root, [0, 30, 150, 550, 1250, 3050, 10250])
            requests, _ = cache_ttl.collect(root)
            chain = list(cache_ttl.chains(requests).values())[0]
            table = cache_ttl.band_table(cache_ttl.gap_seconds(chain))
            self.assertEqual(table["0-1m"]["n"], 1)
            self.assertEqual(table["1-5m"]["n"], 1)
            self.assertEqual(table["5-10m"]["n"], 1)
            self.assertEqual(table["10-15m"]["n"], 1)
            self.assertEqual(table["15-60m"]["n"], 1)
            self.assertEqual(table[">60m"]["n"], 1)


if __name__ == "__main__":
    unittest.main()
