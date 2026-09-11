"""Unsupported histories cannot masquerade as a clean behavior audit."""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "stopped-promises.py"
spec = importlib.util.spec_from_file_location("stopped_coverage", SCRIPT)
stopped = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stopped)


class CoverageTests(unittest.TestCase):
    def run_corpus(self, *corpora, bounded=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for i, rows in enumerate(corpora):
                stopped.write_fixture(root / "corpus", str(i) + ".jsonl", rows)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                args = [
                    "--root", str(root / "corpus"), "--json",
                    "--candidates", str(root / "candidates.txt"),
                ]
                if bounded:
                    args += ["--since", "2026-08-01", "--until", "2026-08-31"]
                status = stopped.main(args)
            return status, json.loads(out.getvalue())

    def supported(self):
        return [
            {"type": "assistant", "timestamp": "2026-08-10T12:00:00Z",
             "message": {"id": "fixture-reply", "content": "Finished."}},
            {"type": "user", "timestamp": "2026-08-10T12:00:01Z",
             "promptSource": "typed", "message": {"content": "Thanks."}},
        ]

    def unsupported(self):
        return [{"type": "response_item", "timestamp": "2026-08-10T12:00:00Z",
                 "payload": {"type": "message", "role": "assistant",
                             "content": "I will do it now."}}]

    def test_supported_clean_window_remains_clean(self):
        status, payload = self.run_corpus(self.supported())
        self.assertEqual(0, status)
        self.assertEqual(1, payload["counts"]["turn_ends"])
        self.assertEqual("supported", payload["coverage"]["status"])

    def test_supported_all_history_does_not_require_date_bounds(self):
        status, payload = self.run_corpus(self.supported(), bounded=False)
        self.assertEqual(0, status)
        self.assertEqual(0, payload["census"]["files_unknown_window"])
        self.assertEqual("supported", payload["coverage"]["status"])

    def test_unsupported_only_is_cannot_run(self):
        status, payload = self.run_corpus(self.unsupported())
        self.assertEqual(2, status)
        self.assertEqual(1, payload["census"]["files_unsupported"])
        self.assertEqual("cannot_run", payload["coverage"]["status"])

    def test_mixed_corpus_preserves_supported_counts_and_flags_coverage(self):
        status, payload = self.run_corpus(self.supported(), self.unsupported())
        self.assertEqual(1, status)
        self.assertEqual(1, payload["census"]["files_measured"])
        self.assertEqual(1, payload["census"]["files_unsupported"])
        self.assertEqual(1, payload["counts"]["turn_ends"])
        self.assertEqual("partial", payload["coverage"]["status"])

    def test_empty_file_has_no_measured_population(self):
        status, payload = self.run_corpus([])
        self.assertEqual(2, status)
        self.assertEqual(1, payload["census"]["files_empty"])
        self.assertEqual(0, payload["census"]["files_measured"])

    def test_unsupported_history_outside_the_window_does_not_taint_it(self):
        rows = self.unsupported()
        rows[0]["timestamp"] = "2026-07-31T23:59:59Z"
        status, payload = self.run_corpus(self.supported(), rows)
        self.assertEqual(0, status)
        self.assertEqual(0, payload["census"]["files_unsupported"])
        self.assertEqual(1, payload["census"]["files_outside_window"])

    def test_nonobject_json_is_unsupported_instead_of_crashing(self):
        for value in (None, 0, -1, "文字", []):
            with self.subTest(value=value):
                status, payload = self.run_corpus([value])
                self.assertEqual(2, status)
                self.assertEqual(1, payload["census"]["files_unsupported"])
                status, payload = self.run_corpus(self.supported(), [value])
                self.assertEqual(1, status)
                self.assertEqual(1, payload["counts"]["ended_by_speaker"])

    def test_unknown_window_is_visible_and_cannot_be_a_clean_exclusion(self):
        rows = self.unsupported()
        del rows[0]["timestamp"]
        status, payload = self.run_corpus(self.supported(), rows)
        self.assertEqual(1, status)
        self.assertEqual(1, payload["census"]["files_unknown_window"])

    def test_child_or_sdk_only_histories_have_no_eligible_population(self):
        for field, value, counter in (
                ("isSidechain", True, "sidechain_records_excluded"),
                ("entrypoint", "sdk-py", "sdk_records_excluded")):
            rows = self.supported()
            for row in rows:
                row[field] = value
            status, payload = self.run_corpus(rows)
            self.assertEqual(2, status)
            self.assertEqual(0, payload["counts"]["ended_by_speaker"])
            self.assertEqual(2, payload["census"][counter])

    def test_mixed_record_formats_are_not_a_supported_transcript(self):
        status, payload = self.run_corpus(self.supported() + self.unsupported())
        self.assertEqual(2, status)
        self.assertEqual(1, payload["census"]["files_unsupported"])


if __name__ == "__main__":
    unittest.main()
