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
            main_ids = [r["rid"] for c in main.values() for r in c]
            sub_ids = [r["rid"] for c in subs.values() for r in c]
            # Identity, not just counts: an inverted selector would still
            # leave both sides holding exactly one record each.
            self.assertEqual(main_ids, ["m"])
            self.assertEqual(sub_ids, ["s1"])

    def test_chains_groups_one_entry_per_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s1", "rows": [
                    fixtures.usage_row("a", "claude-opus-5", 1, 1, 0, 1, T0)]},
                {"project": "p", "session": "s2", "rows": [
                    fixtures.usage_row("b", "claude-opus-5", 1, 1, 0, 1, T0)]},
            ])
            requests, _ = cache_ttl.collect(root)
            grouped = cache_ttl.chains(requests)
            # A grouping key collapsed to a constant would merge both
            # transcripts into a single chain.
            self.assertEqual(len(grouped), 2)
            ids = sorted(r["rid"] for c in grouped.values() for r in c)
            self.assertEqual(ids, ["a", "b"])

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

    def test_band_table_accumulates_read_write_and_zero_read(self):
        """One band, two records: a cache hit and a zero-read miss, so the
        band's read/write/zero_read fields must each add up rather than stay
        at their zeroed defaults or count only the opener's n."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("open", "claude-opus-5", 100, 5, 0, 1,
                                       T0),
                    fixtures.usage_row("hit", "claude-opus-5", 500, 20, 0, 1,
                                       T0 + timedelta(seconds=10)),
                    fixtures.usage_row("miss", "claude-opus-5", 0, 0, 15, 1,
                                       T0 + timedelta(seconds=20)),
                ]}])
            requests, _ = cache_ttl.collect(root)
            chain = list(cache_ttl.chains(requests).values())[0]
            table = cache_ttl.band_table(cache_ttl.gap_seconds(chain))
            band = table["0-1m"]
            self.assertEqual(band["n"], 2)
            self.assertEqual(band["read"], 500)
            self.assertEqual(band["write"], 35)
            self.assertEqual(band["zero_read"], 1)


class TestCostModel(unittest.TestCase):
    def _evaluate(self, offsets, read=1000, w1=100, w5=0,
                  model="claude-opus-5"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [fixtures.usage_row("r%d" % i, model, read, w1, w5, 1,
                                       T0 + timedelta(seconds=off))
                    for i, off in enumerate(offsets)]
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": rows}])
            requests, _ = cache_ttl.collect(root)
            return cache_ttl.evaluate(cache_ttl.chains(requests))

    def test_gap_under_five_minutes_costs_the_same_read_under_both_policies(self):
        result = self._evaluate([0, 60])
        # Second request: 1h policy pays 100 tokens at the 1h write rate plus
        # 1000 read; 5m policy pays the same tokens at the 5m write rate.
        expected_observed = (100 * 10.00e-6 + 1000 * 0.50e-6) * 2
        expected_counter = (100 * 6.25e-6 + 1000 * 0.50e-6) * 2
        self.assertAlmostEqual(result["observed"], expected_observed, places=9)
        self.assertAlmostEqual(result["counterfactual"], expected_counter,
                               places=9)
        self.assertLess(result["ratio"], 1.0)

    def test_gap_past_five_minutes_rewrites_the_prefix_under_the_counterfactual(self):
        result = self._evaluate([0, 600])
        opener_obs = 100 * 10.00e-6 + 1000 * 0.50e-6
        opener_cf = 100 * 6.25e-6 + 1000 * 0.50e-6
        # The missing request rewrites read + writes at the 5m rate, no read.
        miss_cf = (1000 + 100) * 6.25e-6
        self.assertAlmostEqual(result["counterfactual"], opener_cf + miss_cf,
                               places=9)
        self.assertAlmostEqual(result["observed"], opener_obs * 2, places=9)
        self.assertGreater(result["ratio"], 1.0)
        self.assertEqual(result["bands"]["5-60m"], 1)

    def test_session_opener_takes_the_unchanged_branch(self):
        result = self._evaluate([0])
        self.assertEqual(result["openers"], 1)
        self.assertEqual(sum(result["bands"].values()), 0)

    def test_decisive_and_neutral_read_tokens_are_separated(self):
        result = self._evaluate([0, 60, 600])
        self.assertEqual(result["neutral_read"], 1000)
        self.assertEqual(result["decisive_read"], 1000)

    def test_unknown_model_is_reported_not_priced_at_a_default(self):
        result = self._evaluate([0, 60], model="claude-unknown-9")
        self.assertEqual(result["observed"], 0.0)
        self.assertEqual(result["unpriced"]["claude-unknown-9"], 2)

    def test_price_rows_hold_the_exact_published_multipliers(self):
        """Every row is base x1.25 (5m), x2.0 (1h), x0.1 (read). Expressed
        against the 5m write so no base column is needed: 1h is 1.6x the 5m
        write and a read is 0.08x it. An ordering-only assertion would let a
        tenfold typo through."""
        for model, (w5, w1, read) in cache_ttl.PRICES.items():
            self.assertAlmostEqual(w1, w5 * 1.6, places=12, msg=model)
            self.assertAlmostEqual(read, w5 * 0.08, places=12, msg=model)

    def test_unpriced_record_keeps_its_place_in_the_chain_for_the_next_gap(self):
        """The brief requires an unpriced request to stay in the chain
        rather than being dropped, because dropping it would invent a
        longer gap for the request after it. A chain where every record is
        unpriced cannot exercise this -- there is no priced successor whose
        gap could be corrupted. This chain interleaves priced, unpriced,
        priced records so the third request's true gap (60s, measured from
        the unpriced second request) lands in the 0-5m band, while the gap
        a chain that dropped the unpriced record would compute (350s,
        measured from the first request) would cross into the 5-60m band."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                fixtures.usage_row("r0", "claude-opus-5", 1000, 100, 0, 1, T0),
                fixtures.usage_row("r1", "claude-unknown-9", 1000, 100, 0, 1,
                                   T0 + timedelta(seconds=290)),
                fixtures.usage_row("r2", "claude-opus-5", 1000, 100, 0, 1,
                                   T0 + timedelta(seconds=350)),
            ]
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": rows}])
            requests, _ = cache_ttl.collect(root)
            result = cache_ttl.evaluate(cache_ttl.chains(requests))
        self.assertEqual(result["unpriced"]["claude-unknown-9"], 1)
        self.assertEqual(result["bands"]["0-5m"], 1)
        self.assertEqual(result["bands"]["5-60m"], 0)


import io  # noqa: E402


class TestBoundariesAndBranches(unittest.TestCase):
    """The comparisons the verdict turns on. Mutation testing showed the suite
    stayed green when either boundary was moved, so each is pinned from both
    sides at its exact value."""

    def _at_gap(self, seconds):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("a", "claude-opus-5", 1000, 100, 0, 1, T0),
                    fixtures.usage_row("b", "claude-opus-5", 1000, 100, 0, 1,
                                       T0 + timedelta(seconds=seconds))]}])
            requests, _ = cache_ttl.collect(root)
            return cache_ttl.evaluate(cache_ttl.chains(requests))

    def test_gap_of_exactly_five_minutes_still_counts_as_a_hit(self):
        result = self._at_gap(300)
        self.assertEqual(result["bands"]["0-5m"], 1)
        self.assertEqual(result["neutral_read"], 1000)
        self.assertEqual(result["decisive_read"], 0)

    def test_one_second_past_five_minutes_counts_as_a_miss(self):
        result = self._at_gap(301)
        self.assertEqual(result["bands"]["5-60m"], 1)
        self.assertEqual(result["decisive_read"], 1000)
        self.assertEqual(result["neutral_read"], 0)

    def test_gap_of_exactly_one_hour_is_still_the_decisive_band(self):
        result = self._at_gap(3600)
        self.assertEqual(result["bands"]["5-60m"], 1)
        self.assertEqual(result["bands"][">60m"], 0)

    def test_one_second_past_an_hour_leaves_the_decisive_band(self):
        result = self._at_gap(3601)
        self.assertEqual(result["bands"][">60m"], 1)
        self.assertEqual(result["bands"]["5-60m"], 0)
        self.assertEqual(result["decisive_read"], 0)

    def test_delta_is_counterfactual_minus_observed(self):
        result = self._at_gap(600)
        self.assertAlmostEqual(result["delta"],
                               result["counterfactual"] - result["observed"],
                               places=12)
        self.assertGreater(result["delta"], 0.0)

    def test_malformed_json_rows_are_tallied_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("a", "claude-opus-5", 1, 1, 0, 1, T0)]}])
            with open(root / "p" / "s.jsonl", "a", encoding="utf-8") as handle:
                handle.write('{"type":"assistant","usage": BROKEN\n')
            _, skipped = cache_ttl.collect(root)
            self.assertEqual(skipped["bad_json"], 1)

    def test_a_timestamp_without_a_zone_is_skipped_rather_than_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            naive = fixtures.usage_row("n", "claude-opus-5", 1, 1, 0, 1, T0)
            naive["timestamp"] = "2026-08-01T12:00:00"
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    naive,
                    fixtures.usage_row("a", "claude-opus-5", 1, 1, 0, 1, T0)]}])
            requests, skipped = cache_ttl.collect(root)
            self.assertEqual(skipped["naive_timestamp"], 1)
            self.assertEqual(list(requests), ["a"])

    def test_unpriced_main_thread_yields_no_verdict_rather_than_a_false_one(self):
        """observed reaches zero when every main model is unpriced, and a zero
        denominator rendered as a confident 'switch the TTL'."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("x", "claude-zzz-unknown", 5, 1, 0, 1, T0),
                    fixtures.usage_row("y", "claude-zzz-unknown", 5, 1, 0, 1,
                                       T0 + timedelta(seconds=30))]}])
            stream = io.StringIO()
            code = cache_ttl.report(root, None, None, False, stream)
            self.assertEqual(code, cache_ttl.EXIT_CLEAN)
            self.assertIn("nothing to decide", stream.getvalue())
            self.assertNotIn("FORCE_PROMPT_CACHING_5M", stream.getvalue())

    def test_a_subagent_only_unknown_model_still_reaches_the_unpriced_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("m1", "claude-opus-5", 100, 5, 0, 1, T0),
                    fixtures.usage_row("m2", "claude-opus-5", 100, 5, 0, 1,
                                       T0 + timedelta(seconds=30))]},
                {"project": "p", "session": "s", "subagent": True, "rows": [
                    fixtures.usage_row("s1", "claude-mystery-7", 900, 0, 9, 1,
                                       T0)]},
            ])
            stream = io.StringIO()
            cache_ttl.report(root, None, None, True, stream)
            body = json.loads(stream.getvalue())
            self.assertIn("claude-mystery-7", body["unpriced_requests"])
            self.assertGreater(body["unpriced_tokens"]["claude-mystery-7"], 0)

    def test_ttl_in_force_is_read_from_the_write_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("a", "claude-opus-5", 10, 0, 900, 1, T0),
                    fixtures.usage_row("b", "claude-opus-5", 10, 0, 900, 1,
                                       T0 + timedelta(seconds=30))]}])
            stream = io.StringIO()
            cache_ttl.report(root, None, None, True, stream)
            self.assertEqual(json.loads(stream.getvalue())["ttl_in_force"],
                             "five minutes")

    def test_validation_table_is_fed_from_subagent_chains(self):
        """The skill calls this table the validation. Fed from main chains it
        would validate nothing, and no exit code would move."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("m1", "claude-opus-5", 100, 5, 0, 1, T0),
                    fixtures.usage_row("m2", "claude-opus-5", 100, 5, 0, 1,
                                       T0 + timedelta(seconds=30))]},
                {"project": "p", "session": "s", "subagent": True, "rows": [
                    fixtures.usage_row("s1", "claude-sonnet-5", 50, 0, 5, 1, T0),
                    fixtures.usage_row("s2", "claude-sonnet-5", 50, 0, 5, 1,
                                       T0 + timedelta(seconds=7200))]},
            ])
            stream = io.StringIO()
            cache_ttl.report(root, None, None, False, stream)
            text = stream.getvalue()
            validation = text.split("validation:")[1].split("main-thread gap bands")[0]
            self.assertIn(">60m", validation)
            self.assertNotIn("0-1m", validation)

    def test_workflow_nested_subagents_are_still_classified_as_subagents(self):
        """Most real subagent transcripts sit under subagents/workflows/<id>/,
        two levels deeper than the shallow case the other tests build."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("m", "claude-opus-5", 1, 1, 0, 1, T0)]},
                {"project": "p", "session": "s", "subagent": True,
                 "workflow": "wf_abc123", "rows": [
                     fixtures.usage_row("w", "claude-sonnet-5", 9, 0, 1, 1, T0)]},
            ])
            requests, _ = cache_ttl.collect(root)
            self.assertTrue(requests["m"]["main"])
            self.assertFalse(requests["w"]["main"])

    def test_a_projects_directory_with_no_transcripts_cannot_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "empty-project").mkdir()
            code = cache_ttl.report(root, None, None, False, io.StringIO())
            self.assertEqual(code, cache_ttl.EXIT_CANNOT_RUN)


class TestPrivacyAndCli(unittest.TestCase):
    def test_project_label_never_reveals_the_directory_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            name = "C--Users-someone-git-secretproject"
            path = root / name / "s.jsonl"
            label = cache_ttl.project_label(path, root)
            self.assertNotIn("secretproject", label)
            self.assertNotIn("someone", label)
            self.assertNotIn("C--", label)
            self.assertTrue(label.startswith("project-"))

    def test_project_label_is_stable_for_the_same_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = cache_ttl.project_label(root / "same" / "one.jsonl", root)
            b = cache_ttl.project_label(root / "same" / "two.jsonl", root)
            self.assertEqual(a, b)

    def test_window_filter_keeps_only_recent_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("old", "claude-opus-5", 1, 1, 0, 1,
                                       T0 - timedelta(days=90)),
                    fixtures.usage_row("new", "claude-opus-5", 1, 1, 0, 1, T0),
                ]}])
            requests, _ = cache_ttl.collect(root)
            kept = cache_ttl.within_window(requests, 30, T0)
            self.assertEqual(list(kept), ["new"])

    def test_missing_projects_directory_exits_cannot_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope"
            code = cache_ttl.report(missing, None, None, False, io.StringIO())
            self.assertEqual(code, cache_ttl.EXIT_CANNOT_RUN)

    def test_empty_window_is_an_ordinary_result_not_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("old", "claude-opus-5", 1, 1, 0, 1,
                                       T0 - timedelta(days=900))]}])
            stream = io.StringIO()
            code = cache_ttl.report(root, 1, None, False, stream)
            self.assertEqual(code, cache_ttl.EXIT_CLEAN)
            # Pinned to the exact sentence so this guard cannot be deleted
            # silently: the observed<=0.0 guard below it would still catch
            # this same empty case and keep the exit code green, but with a
            # less accurate message ("no priced main-thread requests"
            # instead of "no main-thread requests").
            self.assertEqual(
                stream.getvalue(),
                "no main-thread requests in this window; nothing to decide\n")

    def test_json_output_carries_no_project_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "C--Users-someone-git-secretproject",
                 "session": "s", "rows": [
                     fixtures.usage_row("a", "claude-opus-5", 100, 5, 0, 1, T0),
                     fixtures.usage_row("b", "claude-opus-5", 100, 5, 0, 1,
                                        T0 + timedelta(seconds=600))]}])
            stream = io.StringIO()
            cache_ttl.report(root, None, None, True, stream)
            payload = stream.getvalue()
            self.assertNotIn("secretproject", payload)
            self.assertNotIn("someone", payload)
            self.assertNotIn("C--", payload)
            # Asserting only absence passes even when no label is emitted at
            # all, so it could never fail for the reason it names. Require the
            # hashed label to be present, and to be this directory's hash.
            body = json.loads(payload)
            expected = cache_ttl.project_label(
                root / "C--Users-someone-git-secretproject" / "s.jsonl", root)
            self.assertIn(expected, body["requests_by_project"])
            self.assertEqual(body["requests_by_project"][expected], 2)

    def test_verdict_flags_when_the_counterfactual_is_cheaper(self):
        """All gaps under five minutes: nothing is ever rewritten, so the
        cheaper five-minute writes win and the tool should say so."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [fixtures.usage_row("r%d" % i, "claude-opus-5", 10, 5000, 0,
                                       1, T0 + timedelta(seconds=i * 30))
                    for i in range(5)]
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": rows}])
            code = cache_ttl.report(root, None, None, False, io.StringIO())
            self.assertEqual(code, cache_ttl.EXIT_FLAGGED)


class TestJsonOnEarlyReturns(unittest.TestCase):
    """A --json caller must always get parseable JSON back, including on the
    early, no-verdict return paths. Reported against commit 40bb59f: those
    paths wrote a plain-English sentence and exited 0 regardless of
    as_json, so `cache_ttl.py report --json` could exit clean with output
    that fails json.loads -- exactly the silent-wrong-result failure this
    tool exists to prevent. Every existing guard test before this class
    passed as_json=False, which is why nothing caught it."""

    def test_json_output_is_valid_when_no_main_thread_requests_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("a", "claude-opus-5", 100, 5, 0, 1, T0)]}])
            stream = io.StringIO()
            code = cache_ttl.report(root, None, "does-not-match-anything",
                                    True, stream)
            self.assertEqual(code, cache_ttl.EXIT_CLEAN)
            body = json.loads(stream.getvalue())
            self.assertEqual(body["reason"], "no_main_thread_requests")
            self.assertIsNone(body["keep_current_ttl"])

    def test_json_output_is_valid_when_every_main_thread_model_is_unpriced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "p", "session": "s", "rows": [
                    fixtures.usage_row("x", "claude-zzz-unknown", 5, 1, 0, 1,
                                       T0),
                    fixtures.usage_row("y", "claude-zzz-unknown", 5, 1, 0, 1,
                                       T0 + timedelta(seconds=30))]}])
            stream = io.StringIO()
            code = cache_ttl.report(root, None, None, True, stream)
            self.assertEqual(code, cache_ttl.EXIT_CLEAN)
            body = json.loads(stream.getvalue())
            self.assertEqual(body["reason"], "no_priced_main_thread_requests")
            self.assertIsNone(body["keep_current_ttl"])
            self.assertIn("claude-zzz-unknown", body["unpriced_requests"])

    def test_json_output_is_valid_when_the_projects_directory_is_missing(self):
        """Lower severity than the exit-0 guards above -- exit 2 already
        tells an automated caller something is wrong -- but a --json caller
        should still get JSON rather than a sentence."""
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope"
            stream = io.StringIO()
            code = cache_ttl.report(missing, None, None, True, stream)
            self.assertEqual(code, cache_ttl.EXIT_CANNOT_RUN)
            body = json.loads(stream.getvalue())
            self.assertEqual(body["reason"], "no_session_directory")

    def test_json_output_is_valid_when_there_are_no_readable_transcripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "empty-project").mkdir()
            stream = io.StringIO()
            code = cache_ttl.report(root, None, None, True, stream)
            self.assertEqual(code, cache_ttl.EXIT_CANNOT_RUN)
            body = json.loads(stream.getvalue())
            self.assertEqual(body["reason"], "no_readable_transcripts")


if __name__ == "__main__":
    unittest.main()
