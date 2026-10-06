"""cache_ttl withholds a verdict the measured data cannot support, and says
which harness it measured."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures  # noqa: E402
import cache_ttl  # noqa: E402

T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
PRICED = "claude-opus-5"
UNPRICED = "claude-not-in-the-price-table"


def chain(prefix, model, read, gaps, w1=0, w5=0):
    """One request at T0, then one per gap (seconds after the previous)."""
    rows, when = [], T0
    for index, gap in enumerate([0] + list(gaps)):
        when = when + timedelta(seconds=gap)
        rows.append(fixtures.usage_row("%s%d" % (prefix, index), model,
                                       read, w1, w5, 1, when))
    return rows


class Verdicts(unittest.TestCase):
    def report(self, sessions):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, sessions)
            stream = io.StringIO()
            code = cache_ttl.report(root, None, None, True, stream)
            return code, json.loads(stream.getvalue())

    def test_a_window_whose_reads_are_mostly_unpriced_gets_no_verdict(self):
        code, body = self.report([
            {"project": "p", "session": "a",
             "rows": chain("a", PRICED, 100, [600, 600], w1=50)},
            {"project": "p", "session": "b",
             "rows": chain("b", UNPRICED, 100000, [30, 30])},
        ])
        self.assertEqual(cache_ttl.EXIT_FLAGGED, code)
        self.assertEqual("insufficient_evidence", body["verdict"])
        self.assertIsNone(body["keep_current_ttl"])
        self.assertIn("unpriced_read_share_above_limit", body["insufficient_reasons"])
        self.assertGreater(body["unpriced_share_of_main_read_tokens"], 99.0)

    def test_a_small_unpriced_share_still_gets_a_verdict_and_is_printed(self):
        code, body = self.report([
            {"project": "p", "session": "a",
             "rows": chain("a", PRICED, 100000, [600, 600], w1=50)},
            {"project": "p", "session": "b",
             "rows": chain("b", UNPRICED, 1000, [30, 30])},
        ])
        self.assertIn(body["verdict"], ("keep", "switch"))
        self.assertEqual([], body["insufficient_reasons"])
        self.assertAlmostEqual(1.0, body["unpriced_share_of_main_read_tokens"], 0)

    def test_keep_with_an_empty_decisive_band_is_withheld(self):
        """With no 5-60 minute gaps every hit costs less under five minutes,
        so a keep can only come from the over-an-hour miss branch, which the
        spec records as overcharging the counterfactual."""
        code, body = self.report([
            {"project": "p", "session": "a",
             "rows": chain("a", PRICED, 1000000, [7200], w1=1000)},
        ])
        self.assertEqual(0, body["decisive_band_requests"])
        self.assertEqual(cache_ttl.EXIT_FLAGGED, code)
        self.assertEqual("insufficient_evidence", body["verdict"])
        self.assertEqual(["decisive_band_empty"], body["insufficient_reasons"])

    def test_switch_with_an_empty_decisive_band_stands(self):
        code, body = self.report([
            {"project": "p", "session": "a",
             "rows": chain("a", PRICED, 10, [30, 30, 30], w1=5000)},
        ])
        self.assertEqual(cache_ttl.EXIT_FLAGGED, code)
        self.assertEqual("switch", body["verdict"])
        self.assertIs(False, body["keep_current_ttl"])

    def test_every_main_model_unpriced_with_reads_is_insufficient_not_clean(self):
        code, body = self.report([
            {"project": "p", "session": "a",
             "rows": chain("a", UNPRICED, 5, [30], w1=1)},
        ])
        self.assertEqual(cache_ttl.EXIT_FLAGGED, code)
        self.assertEqual("insufficient_evidence", body["reason"])
        self.assertIsNone(body["keep_current_ttl"])

    def test_zero_token_unpriced_rows_alone_remain_nothing_to_decide(self):
        code, body = self.report([
            {"project": "p", "session": "a",
             "rows": chain("a", "<synthetic>", 0, [30])},
        ])
        self.assertEqual(cache_ttl.EXIT_CLEAN, code)
        self.assertEqual("no_priced_main_thread_requests", body["reason"])


class HarnessScope(unittest.TestCase):
    def test_every_json_shape_names_the_measured_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            for sessions in ([], [{"project": "p", "session": "a",
                                   "rows": chain("a", PRICED, 100, [600], w1=5)}]):
                root = Path(tmp) / str(len(sessions))
                root.mkdir()
                fixtures.build_corpus(root, sessions)
                stream = io.StringIO()
                cache_ttl.report(root, None, None, True, stream)
                body = json.loads(stream.getvalue())
                self.assertEqual("claude", body["harness"])
                self.assertEqual({"codex", "antigravity"},
                                 set(body["not_applicable_harnesses"]))

    def test_the_cli_reads_the_claude_root_from_claude_config_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixtures.build_corpus(Path(tmp) / "projects", [
                {"project": "p", "session": "a",
                 "rows": chain("a", PRICED, 100, [600], w1=5)}])
            out = io.StringIO()
            # The home-relative default is pointed at nothing, so a build that
            # ignores CLAUDE_CONFIG_DIR fails here instead of reading the
            # machine's real history.
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}), \
                    mock.patch.object(cache_ttl, "PROJECTS_DIR",
                                      Path(tmp) / "absent", create=True), \
                    redirect_stdout(out):
                code = cache_ttl.main(["report", "--json"])
            self.assertNotEqual(cache_ttl.EXIT_CANNOT_RUN, code)
            self.assertEqual(2, json.loads(out.getvalue())["main_requests"])


    def test_days_must_be_a_positive_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixtures.build_corpus(Path(tmp) / "projects", [
                {"project": "p", "session": "a",
                 "rows": chain("a", PRICED, 100, [600], w1=5)}])
            for days in ("0", "-3"):
                out = io.StringIO()
                with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}), \
                        mock.patch.object(cache_ttl, "PROJECTS_DIR",
                                          Path(tmp) / "absent", create=True), \
                        mock.patch("sys.stderr", io.StringIO()), \
                        redirect_stdout(out):
                    try:
                        code = cache_ttl.main(["report", "--days", days, "--json"])
                    except SystemExit as stop:
                        code = stop.code
                self.assertEqual(cache_ttl.EXIT_CANNOT_RUN, code, days)
                self.assertEqual("", out.getvalue(), days)


if __name__ == "__main__":
    unittest.main()
