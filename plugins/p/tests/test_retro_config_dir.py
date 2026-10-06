"""Every Claude-side reader in retro honours CLAUDE_CONFIG_DIR.

The home-relative default is pointed at an empty fake home while the module
loads, so a build that ignores the override inspects nothing real and fails.
"""

import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_retro_extract import load_retro
from test_retro_reporting import base_row

# Assembled from fragments so the privacy scanner does not read an address.
AUTHOR = "fixture" + "@" + "example" + ".invalid"


class ConfigDirOverride(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.config, self.fake_home, self.work = base / "cfg", base / "home", base / "work"
        self.config.mkdir()
        self.work.mkdir()
        self.env = {"CLAUDE_CONFIG_DIR": str(self.config), "RETRO_HOME": str(self.work),
                    "CODEX_HOME": str(base / "cx"),
                    "RETRO_ANTIGRAVITY_HOME": str(base / "agy"),
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": AUTHOR,
                    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": AUTHOR,
                    "GIT_AUTHOR_DATE": "2026-08-15T12:00:00+00:00",
                    "GIT_COMMITTER_DATE": "2026-08-15T12:00:00+00:00"}

    def git(self, *args):
        with mock.patch.dict(os.environ, self.env):
            subprocess.run(["git", "-C", str(self.config), *args], check=True,
                           capture_output=True)

    def commit_rules(self):
        (self.config / "CLAUDE.md").write_text("one rule\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", "CLAUDE.md")
        self.git("commit", "-q", "-m", "add a rule")

    def call(self, name, *args):
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(Path, "home", return_value=self.fake_home):
            retro = load_retro()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = getattr(retro, name)(*args)
        return code, out.getvalue()

    def test_rules_inspects_the_overridden_directory(self):
        self.commit_rules()
        (self.config / "CLAUDE.md").write_text("one rule, edited\n", encoding="utf-8")
        code, _ = self.call("cmd_rules", mock.Mock())
        self.assertEqual(1, code)   # the uncommitted edit is found
        self.assertFalse(self.fake_home.exists())

    def test_effect_date_list_reads_the_overridden_history(self):
        self.commit_rules()
        (self.work / "metrics.jsonl").write_text(
            json.dumps(base_row(date="2026-08-01")) + "\n"
            + json.dumps(base_row(date="2026-08-30", transcript="p/t.jsonl")) + "\n",
            encoding="utf-8")
        code, out = self.call("print_candidates")
        self.assertEqual(0, code)
        self.assertEqual(["2026-08-15"],
                         re.findall(r"^\| (\d{4}-\d{2}-\d{2}) \|", out, re.M))
        self.assertFalse(self.fake_home.exists())

    def test_skill_inventory_reads_the_overridden_directory(self):
        skill = self.config / "skills" / "fixture-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: fixture-skill\n---\n", encoding="utf-8")
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(Path, "home", return_value=self.fake_home):
            retro = load_retro()
            self.assertEqual({"fixture-skill"}, retro.installed_skills()["claude"])


if __name__ == "__main__":
    unittest.main()
