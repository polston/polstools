import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from home_env import home_vars


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PROBE = PLUGIN_ROOT / "bin" / "p-session-probe"
SECRET = "-".join(("conv", "secret", "probe"))
KEEP = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "SHELL")


def snapshot(root):
    return sorted(
        (path.relative_to(root).as_posix(), path.read_bytes() if path.is_file()
         and not path.is_symlink() else b"")
        for path in Path(root).rglob("*"))


class SessionProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tmpdir = self.root / "tmp"
        self.tmpdir.mkdir()
        (self.root / "userdir").mkdir()
        # A small explicit base: nothing of the caller's session leaks in.
        self.env = {k: os.environ[k] for k in KEEP if k in os.environ}
        self.env.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "POLSTOOLS_PYTHON": sys.executable,
            "TMPDIR": str(self.tmpdir),
            **home_vars(str(self.root / "userdir")),
        })

    def tearDown(self):
        self.tmp.cleanup()

    def probe(self, *args, **extra):
        return subprocess.run(
            [sys.executable, "-B", str(PROBE), *args], text=True,
            encoding="utf-8", capture_output=True, env=dict(self.env, **extra))

    # Names only.

    def test_reports_names_and_never_values(self):
        completed = self.probe(
            "--json", CLAUDE_CODE_SESSION_ID=SECRET,
            CLAUDE_PLUGIN_ROOT="fixture-root-value", CLAUDE_EXTRA_NAME="v")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertNotIn(SECRET, completed.stdout)
        self.assertNotIn("fixture-root-value", completed.stdout)
        report = json.loads(completed.stdout)
        self.assertEqual("claude", report["harness"])
        self.assertEqual(["CLAUDE_CODE_SESSION_ID"], report["session_variables"])
        self.assertEqual(["CLAUDE_PLUGIN_ROOT"], report["plugin_root_variables"])
        self.assertIn("CLAUDE_EXTRA_NAME", report["other_variable_names"])

    def test_flags_a_session_without_any_session_variable(self):
        completed = self.probe("--json", "--harness", "codex")
        self.assertEqual(1, completed.returncode)
        self.assertEqual([], json.loads(completed.stdout)["session_variables"])

    def test_antigravity_probe_is_read_only_without_hook_evidence(self):
        before = snapshot(self.root)
        completed = self.probe("--json", ANTIGRAVITY_CONVERSATION_ID=SECRET)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("antigravity", json.loads(completed.stdout)["harness"])
        self.assertNotIn(SECRET, completed.stdout)
        self.assertEqual(before, snapshot(self.root))

    def test_help_exits_0_and_prints_the_usage(self):
        for flag_ in ("--help", "-h"):
            with self.subTest(flag=flag_):
                completed = self.probe(flag_)
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertIn("--harness", completed.stdout)

    def test_a_bad_argument_still_exits_2(self):
        self.assertEqual(2, self.probe("--nonsense").returncode)


if __name__ == "__main__":
    unittest.main()
