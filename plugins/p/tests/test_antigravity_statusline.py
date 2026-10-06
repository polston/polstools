import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
RENDERER = PLUGIN_ROOT / "renderer" / "antigravity-statusline.py"
CTL = PLUGIN_ROOT / "bin" / "statusline-ctl"
SAMPLE = {
    "cwd": "@HOME@/project",
    "conversation_id": "conv-1",
    "session_id": "conv-1",
    "model": {"id": "m", "display_name": "Gemini Model"},
    "workspace": {"current_dir": "@HOME@/project", "project_dir": "@HOME@/project"},
    "context_window": {"used_percentage": 30.0, "remaining_percentage": 70.0},
    "quota": {
        "weekly": {"remaining_fraction": 0.9},
        "daily": {"remaining_fraction": 0.25, "reset_in_seconds": 60},
    },
    "vcs": {"type": "git", "branch": "main", "dirty": False},
    "agent_state": "idle",
}


class AntigravityStatuslineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.settings = root / "settings.json"
        self.userdir = root / "userdir"
        self.tmpdir = root / "tmp"
        self.userdir.mkdir()
        self.tmpdir.mkdir()
        self.env = {
            "HOME": str(self.userdir),
            "TMPDIR": str(self.tmpdir),
            "PATH": os.environ.get("PATH", ""),
            "NO_COLOR": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "P_STATUSLINE_NO_REFRESH": "1",
            "P_STATUSLINE_NOW_MS": "1700000000000",
            "P_SKILL_CONFIG_FILE": str(root / "global.json"),
            "P_SKILL_STATE_DIR": str(root / "sessions"),
            "STATUSLINE_ANTIGRAVITY_SETTINGS": str(self.settings),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, payload, **extra):
        payload = json.loads(json.dumps(payload).replace("@HOME@", self.env["HOME"]))
        completed = subprocess.run(
            [sys.executable, "-B", str(RENDERER)], input=json.dumps(payload),
            text=True, encoding="utf-8", capture_output=True,
            env=dict(self.env, **extra))
        self.assertEqual(0, completed.returncode, completed.stderr)
        return completed.stdout.splitlines()

    def ctl(self):
        return subprocess.run(
            [sys.executable, "-B", str(CTL), "antigravity"], text=True,
            encoding="utf-8", capture_output=True, env=self.env)

    def test_renders_antigravity_fields_in_the_shared_layout(self):
        lines = self.render(SAMPLE)
        self.assertEqual(2, len(lines))
        first = [part.strip() for part in lines[0].split("|")]
        self.assertEqual("Gemini Model", first[0])
        self.assertEqual("~/project", first[1])
        self.assertEqual("main", first[2])
        self.assertTrue(first[3].endswith("70% left"))
        self.assertEqual("p:h", first[4])
        second = [part.strip() for part in lines[1].split("|")]
        self.assertTrue(second[0].startswith("daily "))
        self.assertTrue(second[0].endswith("25% left"))
        self.assertTrue(second[1].startswith("weekly "))
        self.assertTrue(second[1].endswith("90% left"))
        self.assertNotIn("\x1b[", "".join(lines))

    def test_colour_is_on_for_a_piped_stdout_unless_no_color_is_set(self):
        env = dict(self.env)
        env.pop("NO_COLOR")
        completed = subprocess.run(
            [sys.executable, "-B", str(RENDERER)],
            input=json.dumps(SAMPLE).replace("@HOME@", self.env["HOME"]),
            text=True, encoding="utf-8", capture_output=True, env=env)
        self.assertIn("\x1b[", completed.stdout)

    def test_sparse_or_broken_payloads_still_render(self):
        self.assertEqual(["p:h"], [p.strip() for p in self.render({})[0].split("|")])
        completed = subprocess.run(
            [sys.executable, "-B", str(RENDERER)], input="not json", text=True,
            encoding="utf-8", capture_output=True, env=self.env)
        self.assertEqual(0, completed.returncode)
        self.assertTrue(completed.stdout.strip())

    def test_status_reports_and_prints_the_selecting_command_without_writing(self):
        missing = self.ctl()
        self.assertEqual(1, missing.returncode, missing.stderr)
        self.assertFalse(self.settings.exists())
        command = missing.stdout.splitlines()[-1][len("/statusline "):]
        self.assertEqual(
            ["sh", (PLUGIN_ROOT / "bin" / "python-launcher").as_posix(), RENDERER.as_posix()],
            shlex.split(command))
        self.settings.write_text(json.dumps({
            "colorScheme": "dark",
            "statusLine": {"type": "command", "command": command},
        }), encoding="utf-8")
        before = self.settings.read_bytes()
        self.assertEqual(0, self.ctl().returncode)
        self.assertEqual(before, self.settings.read_bytes())
        self.settings.write_text(json.dumps({
            "statusLine": {"type": "command", "command": command, "enabled": False},
        }), encoding="utf-8")
        self.assertEqual(1, self.ctl().returncode)

    def test_unreadable_settings_cannot_run(self):
        self.settings.write_text("{broken", encoding="utf-8")
        self.assertEqual(2, self.ctl().returncode)

    def test_the_printed_command_renders_the_sample(self):
        command = self.ctl().stdout.splitlines()[-1][len("/statusline "):]
        completed = subprocess.run(
            ["sh", "-c", command], input=json.dumps(SAMPLE).replace("@HOME@", self.env["HOME"]), text=True,
            encoding="utf-8", capture_output=True,
            env=dict(self.env, POLSTOOLS_PYTHON=sys.executable))
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(completed.stdout.startswith("Gemini Model"))

    def test_terminal_controls_in_the_payload_never_reach_the_output(self):
        hostile = dict(
            SAMPLE,
            model={"display_name": "m\x1b]0;title\x07odel\x1b[31m"},
            workspace={"current_dir": "/w\x1b[2Jork\x07"},
            vcs={"branch": "br\x1b[1manch\x9b"},
            quota={"da\x1bily": {"remaining_fraction": 0.5}},
        )
        env = {k: v for k, v in self.env.items() if k != "NO_COLOR"}
        completed = subprocess.run(
            [sys.executable, "-B", str(RENDERER)], input=json.dumps(hostile),
            text=True, encoding="utf-8", capture_output=True, env=env)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("\x1b[", completed.stdout)
        stripped = re.sub(r"\x1b\[[0-9;]*m", "", completed.stdout)
        bad = [ch for ch in stripped if ch != "\n" and (ord(ch) < 32 or 127 <= ord(ch) <= 159)]
        self.assertEqual([], bad)

    def test_a_render_writes_nothing_outside_the_private_cache_directory(self):
        self.render(SAMPLE)
        suffix = "-" + str(os.getuid()) if hasattr(os, "getuid") else ""
        private = self.tmpdir / ("claude-statusline" + suffix)
        for base in (self.userdir, self.tmpdir):
            for path in base.rglob("*"):
                self.assertTrue(path == private or private in path.parents, str(path))
        if private.exists() and hasattr(os, "getuid"):
            self.assertEqual(0, private.stat().st_mode & 0o077)


class StatuslineCtlHelpTests(unittest.TestCase):
    def test_help_keeps_one_subcommand_per_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "HOME": tmp,
                "TMPDIR": tmp,
                "PATH": os.environ.get("PATH", ""),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            result = subprocess.run(
                [sys.executable, "-B", str(CTL), "--help"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(0, result.returncode)
        for name in ("sync", "check", "preview", "apply", "profile-sync", "restore", "antigravity"):
            with self.subTest(subcommand=name):
                self.assertRegex(result.stdout, r"(?m)^  %s\b" % re.escape(name))


if __name__ == "__main__":
    unittest.main()
