"""Where the candidates file (redacted message text) lands, and when."""

import contextlib
import importlib.util
import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "stopped-promises.py"
spec = importlib.util.spec_from_file_location("stopped_candidates", SCRIPT)
stopped = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stopped)


def supported():
    """One turn that ends on a promise: exactly one candidate."""
    return [stopped._assistant("m1", text="Done. I'll run it now."),
            stopped._typed("ok")]


def unsupported():
    return [{"type": "response_item", "timestamp": "2026-08-10T12:00:00Z",
             "payload": {"type": "message", "role": "assistant",
                         "content": "I will do it now."}}]


class CandidatesFile(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.retro_home = self.base / "retro-home"
        self.system_temp = self.base / "system-temp"
        self.system_temp.mkdir()

    def run_stopped(self, rows):
        corpus = self.base / "corpus"
        stopped.write_fixture(corpus, "s.jsonl", rows)
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"RETRO_HOME": str(self.retro_home)}), \
                mock.patch.object(stopped.tempfile, "gettempdir",
                                  return_value=str(self.system_temp)), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            status = stopped.main(["--root", str(corpus), "--json"])
        return status, err.getvalue()

    def test_default_lands_in_the_private_work_directory_not_system_temp(self):
        status, _ = self.run_stopped(supported())
        self.assertEqual(1, status)
        written = self.retro_home / "stopped-promises-candidates.txt"
        self.assertTrue(written.is_file())
        self.assertEqual([], list(self.system_temp.iterdir()))

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_the_file_and_its_directory_are_private_to_the_operator(self):
        self.run_stopped(supported())
        written = self.retro_home / "stopped-promises-candidates.txt"
        self.assertEqual(0o600, written.stat().st_mode & 0o777)
        self.assertEqual(0o700, self.retro_home.stat().st_mode & 0o777)

    def test_the_stderr_line_names_a_path_the_operator_can_open(self):
        _, err = self.run_stopped(supported())
        match = re.search(r"^candidates written to (.+)$", err, re.M)
        self.assertIsNotNone(match)
        self.assertTrue(Path(match.group(1)).expanduser().is_file())

    def test_a_run_that_cannot_run_writes_no_file(self):
        status, err = self.run_stopped(unsupported())
        self.assertEqual(2, status)
        self.assertFalse(self.retro_home.exists())
        self.assertEqual([], list(self.system_temp.iterdir()))
        self.assertIsNone(re.search(r"^candidates written to", err, re.M))



if __name__ == "__main__":
    unittest.main()
