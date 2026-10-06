import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
FORMAT_CTL = PLUGIN_ROOT / "bin" / "format-ctl"
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

import skill_activation  # noqa: E402

# Names that the Antigravity executable never sets (absent from its strings).
NOT_ANTIGRAVITY = ("ANTIGRAVITY_SESSION_ID", "AGY_SESSION_ID", "AGY_CONVERSATION_ID")


class AntigravitySessionIdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = root / "format.json"
        self.state = root / "state"
        userdir = root / "userdir"
        userdir.mkdir()
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(userdir),
            "TMPDIR": str(root),
            "POLSTOOLS_PYTHON": sys.executable,
            "PYTHONDONTWRITEBYTECODE": "1",
            "P_FORMAT_CONFIG_FILE": str(self.config),
            "P_FORMAT_STATE_DIR": str(self.state),
        }
        self.config.write_text(json.dumps({"antigravity": "on"}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def format_ctl(self, *args, **extra):
        return subprocess.run(
            [sys.executable, "-B", str(FORMAT_CTL), *args], text=True,
            encoding="utf-8", capture_output=True, env=dict(self.env, **extra))

    def test_conversation_id_selects_the_antigravity_session_and_default(self):
        status = self.format_ctl("status", ANTIGRAVITY_CONVERSATION_ID="conv-1")
        self.assertEqual(0, status.returncode, status.stderr)
        self.assertIn("ON", status.stdout)
        self.assertIn("antigravity default", status.stdout)
        toggled = self.format_ctl("off", ANTIGRAVITY_CONVERSATION_ID="conv-1")
        self.assertEqual(0, toggled.returncode, toggled.stderr)
        digest = hashlib.sha256(b"conv-1").hexdigest()
        self.assertTrue((self.state / (digest + ".off")).is_file())

    def test_guessed_names_are_not_session_ids(self):
        for name in NOT_ANTIGRAVITY:
            with self.subTest(name=name):
                completed = self.format_ctl("status", **{name: "conv-2"})
                self.assertEqual(2, completed.returncode)
                self.assertIsNone(skill_activation.session_id_from_env({name: "conv-2"}))


    def test_retro_excludes_the_reporting_antigravity_conversation(self):
        from test_retro_extract import load_retro

        retro = load_retro()
        with mock.patch.dict(os.environ, {
                "ANTIGRAVITY_CONVERSATION_ID": "conv-4", "AGY_SESSION_ID": "guess-1"}):
            ids = retro.reporting_session_ids([])
        self.assertIn("conv-4", ids)
        self.assertNotIn("guess-1", ids)

if __name__ == "__main__":
    unittest.main()
