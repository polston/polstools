import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CTL_PATH = PLUGIN_ROOT / "bin" / "skill-profile-ctl"


class NativeActivationScopeTests(unittest.TestCase):
    def test_sync_native_reports_which_harnesses_it_cannot_hide_skills_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            userdir = root / "userdir"
            userdir.mkdir()
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                   "HOME": str(userdir), "TMPDIR": str(root)}
            env.update({
                "P_SKILL_CONFIG_FILE": str(root / "global.json"),
                "P_SKILL_STATE_DIR": str(root / "sessions"),
                "P_CODEX_CONFIG_FILE": str(root / "config.toml"),
                "P_SKILL_SKIP_STATUS_SYNC": "1",
                "CODEX_THREAD_ID": "session-a",
                "PYTHONDONTWRITEBYTECODE": "1",
            })
            completed = subprocess.run(
                [sys.executable, str(CTL_PATH), "sync-native"], text=True,
                encoding="utf-8", capture_output=True, env=env)
            self.assertEqual(0, completed.returncode, completed.stderr)
            lines = completed.stdout.splitlines()
            self.assertEqual(2, len(lines))
            self.assertTrue(lines[0].startswith("Codex skill catalog adapter "))
            self.assertTrue(lines[1].startswith("Claude Code and Antigravity: "))


if __name__ == "__main__":
    unittest.main()
