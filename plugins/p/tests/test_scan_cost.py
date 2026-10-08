"""Long walks report progress on stderr, and a --days or --since window skips
files whose modification time proves they hold nothing inside it."""

import contextlib
import importlib.util
import io
import itertools
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures  # noqa: E402
import cache_ttl  # noqa: E402
import retro  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "stopped_scan_cost", BIN / "stopped-promises.py")
stopped = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stopped)

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
DAY = 86400


def fast_clock():
    """Ten seconds pass on every reading, so every step is past the grace
    period and past the interval."""
    return itertools.count(0, 10).__next__


def set_mtime(path, when):
    os.utime(path, (when.timestamp(), when.timestamp()))


def ttl_session(root, name, start, mtime):
    rows = [fixtures.usage_row(name + "-%d" % i, "claude-opus-5", 100, 5, 0, 1,
                               start + timedelta(seconds=600 * i))
            for i in range(3)]
    fixtures.build_corpus(root, [{"project": "p", "session": name, "rows": rows}])
    set_mtime(root / "p" / (name + ".jsonl"), mtime)


class CacheTtlScan(unittest.TestCase):
    def report(self, root, days, clock=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(retro, "progress_clock", clock or fast_clock()), \
                contextlib.redirect_stderr(err):
            code = cache_ttl.report(root, days, None, True, out, now=NOW)
        return code, json.loads(out.getvalue()), err.getvalue()

    def test_progress_goes_to_stderr_and_json_stays_parseable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(3):
                ttl_session(root, "s%d" % i, NOW - timedelta(days=1), NOW)
            code, body, err = self.report(root, None)
            self.assertEqual(9, body["main_requests"])
            self.assertEqual(3, len([l for l in err.splitlines()
                                     if l.startswith("cache_ttl: scanned ")]))

    def test_a_short_walk_prints_no_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ttl_session(root, "s", NOW - timedelta(days=1), NOW)
            _, _, err = self.report(root, None, clock=itertools.repeat(0.0).__next__)
            self.assertEqual("", err)

    def test_files_older_than_the_window_are_skipped_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = NOW - timedelta(days=40)
            ttl_session(root, "old", old, old)
            ttl_session(root, "new", NOW - timedelta(days=1), NOW)
            _, body, _ = self.report(root, 5)
            self.assertEqual(1, body["files_skipped_before_window"])
            self.assertEqual(3, body["main_requests"])

    def test_a_file_just_inside_the_slack_is_still_read(self):
        """Records inside the window, but the file's mtime sits up to a day
        before the cutoff (clock skew between writer and filesystem)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cutoff = NOW - timedelta(days=5)
            ttl_session(root, "edge", cutoff + timedelta(seconds=1),
                        cutoff - timedelta(hours=23))
            _, body, _ = self.report(root, 5)
            self.assertEqual(0, body["files_skipped_before_window"])
            self.assertEqual(3, body["main_requests"])

    def test_the_window_result_is_identical_with_and_without_the_prefilter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(4):
                when = NOW - timedelta(days=10 * i + 1)
                ttl_session(root, "s%d" % i, when, when + timedelta(hours=1))
            _, filtered, _ = self.report(root, 15)
            with mock.patch.object(cache_ttl, "MTIME_SLACK_SECONDS", float("inf")):
                _, unfiltered, _ = self.report(root, 15)
            self.assertEqual(2, filtered["files_skipped_before_window"])
            self.assertEqual(0, unfiltered["files_skipped_before_window"])
            for key in ("main_requests", "observed_cost", "counterfactual_cost",
                        "decisive_band_requests", "session_openers"):
                self.assertEqual(unfiltered[key], filtered[key], key)

    def test_a_window_that_skips_every_file_is_an_empty_window_not_cannot_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = NOW - timedelta(days=40)
            ttl_session(root, "old", old, old)
            code, body, _ = self.report(root, 5)
            self.assertEqual(cache_ttl.EXIT_CLEAN, code)
            self.assertEqual("no_main_thread_requests", body["reason"])


class StoppedPromisesScan(unittest.TestCase):
    def turn(self, day):
        return [
            {"type": "assistant", "timestamp": day + "T12:00:00Z",
             "message": {"id": "m-" + day, "content": "Finished."}},
            {"type": "user", "timestamp": day + "T12:00:01Z",
             "promptSource": "typed", "message": {"content": "Thanks."}},
        ]

    def run_stopped(self, files, since, clock=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "corpus"
            for name, (day, mtime) in files.items():
                path = stopped.write_fixture(root, name, self.turn(day))
                set_mtime(path, mtime)
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(stopped, "progress_clock", clock or fast_clock()), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = stopped.main(["--root", str(root), "--json", "--since", since,
                                       "--candidates", str(Path(directory) / "c.txt")])
            return status, json.loads(out.getvalue()), err.getvalue()

    def test_old_files_are_skipped_by_mtime_and_counted_outside_the_window(self):
        old = datetime(2026, 7, 1, tzinfo=timezone.utc)
        status, payload, err = self.run_stopped({
            "old.jsonl": ("2026-07-01", old),
            "new.jsonl": ("2026-08-20", NOW)}, "2026-08-01")
        census = payload["census"]
        self.assertEqual(1, census["files_skipped_by_mtime"])
        self.assertEqual(1, census["files_outside_window"])
        self.assertEqual(1, census["files_measured"])
        self.assertTrue(any(l.startswith("stopped-promises: scanned ")
                            for l in err.splitlines()))

    def test_a_file_inside_the_slack_is_still_read(self):
        status, payload, _ = self.run_stopped({
            "edge.jsonl": ("2026-08-01",
                           datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc))},
            "2026-08-01")
        self.assertEqual(0, payload["census"]["files_skipped_by_mtime"])
        self.assertEqual(1, payload["census"]["files_measured"])


class RetroExtractProgress(unittest.TestCase):
    def test_extract_reports_progress_on_stderr_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
            fixtures.build_corpus(base / "cc" / "projects", [
                {"project": "p", "session": "s%d" % i, "rows": [
                    fixtures.claude_user("hi", t0),
                    fixtures.claude_assistant("yo", t0)]} for i in range(3)])
            env = {"CLAUDE_CONFIG_DIR": str(base / "cc"),
                   "CODEX_HOME": str(base / "cx"), "RETRO_HOME": str(base / "w"),
                   "RETRO_ANTIGRAVITY_HOME": str(base / "agy")}
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, env), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                spec = importlib.util.spec_from_file_location(
                    "retro_progress_under_test", BIN / "retro.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.progress_clock = fast_clock()
                module.cmd_extract(mock.Mock(rebuild=False))
            self.assertEqual(3, len([l for l in err.getvalue().splitlines()
                                     if l.startswith("retro extract: measured ")]))
            self.assertNotIn("retro extract: measured", out.getvalue())


if __name__ == "__main__":
    unittest.main()
