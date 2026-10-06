import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOK = PLUGIN_ROOT / "bin" / "agy-format-hook"
MANIFEST = PLUGIN_ROOT / "hooks.json"
SPEC = (PLUGIN_ROOT / "style" / "response-format.md").read_text(encoding="utf-8")
REMINDER = (PLUGIN_ROOT / "style" / "turn-reminder.md").read_text(encoding="utf-8")
CONVERSATION = "-".join(("conv", "fixture", "a"))
KEEP = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "SHELL")


def flag(conversation, state):
    """format-ctl names a toggle file by the session id's SHA-256."""
    return hashlib.sha256(conversation.encode("utf-8")).hexdigest() + "." + state


def controlled_env(root, **extra):
    """A small explicit base; nothing of the caller's session leaks in."""
    root = Path(root)
    env = {k: os.environ[k] for k in KEEP if k in os.environ}
    env.update({
        "HOME": str(root / "userdir"),
        "TMPDIR": str(root / "tmp"),
        "POLSTOOLS_PYTHON": sys.executable,
        "PYTHONDONTWRITEBYTECODE": "1",
        "P_FORMAT_STATE_DIR": str(root / "state"),
        "P_FORMAT_CONFIG_FILE": str(root / "format.json"),
    })
    env.update(extra)
    return env


class AntigravityFormatHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "tmp").mkdir()
        (root / "userdir").mkdir()
        self.state = root / "state"
        self.config = root / "format.json"
        self.env = controlled_env(root)

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, invocation=None, conversation=CONVERSATION):
        payload = {
            "conversationId": conversation,
            "workspacePaths": ["/workspace/project"],
            "transcriptPath": "/data/brain/x/transcript.jsonl",
            "artifactDirectoryPath": "/data/brain/x",
            "modelName": "model",
            "initialNumSteps": 4,
        }
        if invocation is not None:
            payload["invocationNum"] = invocation
        return json.dumps(payload)

    def run_hook(self, stdin, **extra_env):
        env = dict(self.env, **extra_env)
        completed = subprocess.run(
            ["sh", str(HOOK)], input=stdin, text=True,
            encoding="utf-8", capture_output=True, env=env, cwd=PLUGIN_ROOT,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        return json.loads(completed.stdout)

    def message(self, output):
        steps = output["injectSteps"]
        self.assertEqual(1, len(steps))
        self.assertEqual({"ephemeralMessage"}, set(steps[0]))
        return steps[0]["ephemeralMessage"]

    def test_off_by_default_emits_an_empty_object(self):
        self.assertEqual({}, self.run_hook(self.event()))

    def test_first_invocation_injects_the_full_specification(self):
        output = self.run_hook(self.event(), P_FORMAT_DEFAULT="on")
        self.assertEqual(SPEC, self.message(output))

    def test_later_invocations_inject_the_turn_reminder(self):
        output = self.run_hook(self.event(invocation=3), P_FORMAT_DEFAULT="on")
        self.assertEqual(REMINDER, self.message(output))

    def test_session_toggle_is_keyed_by_conversation_id(self):
        self.state.mkdir(mode=0o700)
        (self.state / flag(CONVERSATION, "off")).touch()
        self.assertEqual({}, self.run_hook(self.event(), P_FORMAT_DEFAULT="on"))
        other = self.run_hook(self.event(conversation="other"), P_FORMAT_DEFAULT="on")
        self.assertEqual(SPEC, self.message(other))
        (self.state / flag(CONVERSATION, "off")).unlink()
        (self.state / flag(CONVERSATION, "on")).touch()
        self.assertEqual(SPEC, self.message(self.run_hook(self.event())))

    def test_antigravity_default_applies_and_other_harness_defaults_do_not(self):
        self.config.write_text(json.dumps({"codex": "on"}), encoding="utf-8")
        self.assertEqual({}, self.run_hook(self.event()))
        self.config.write_text(json.dumps({"antigravity": "on"}), encoding="utf-8")
        self.assertEqual(SPEC, self.message(self.run_hook(self.event())))

    def test_malformed_stdin_still_returns_a_valid_envelope(self):
        output = self.run_hook("{not json", P_FORMAT_DEFAULT="on")
        self.assertEqual(SPEC, self.message(output))

    def test_missing_payload_fails_soft_with_an_empty_object(self):
        moved = Path(self.tmp.name) / "plugin"
        subprocess.run(
            [sys.executable, "-c",
             "import shutil,sys; shutil.copytree(sys.argv[1], sys.argv[2], "
             "ignore=shutil.ignore_patterns('__pycache__', 'tests'))",
             str(PLUGIN_ROOT), str(moved)], check=True)
        (moved / "style" / "antigravity" / "response-format.json").unlink()
        completed = subprocess.run(
            ["sh", str(moved / "bin" / "agy-format-hook")],
            input=self.event(), text=True, encoding="utf-8",
            capture_output=True, env=dict(self.env, P_FORMAT_DEFAULT="on"),
        )
        self.assertEqual(0, completed.returncode)
        self.assertEqual({}, json.loads(completed.stdout))
        self.assertIn("format gate exited 1", completed.stderr)

    def test_manifest_command_runs_from_the_plugin_root_as_antigravity_does(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual({"p-format"}, set(manifest))
        self.assertEqual({"PreInvocation"}, set(manifest["p-format"]))
        handlers = manifest["p-format"]["PreInvocation"]
        self.assertEqual(1, len(handlers))
        completed = subprocess.run(
            ["sh", "-c", handlers[0]["command"]], input=self.event(),
            text=True, encoding="utf-8", capture_output=True,
            env=dict(self.env, P_FORMAT_DEFAULT="on"), cwd=PLUGIN_ROOT,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(SPEC, self.message(json.loads(completed.stdout)))
        self.assertLessEqual(handlers[0]["timeout"], 30)

    def test_off_path_starts_no_python(self):
        fake = Path(self.tmp.name) / "fakebin"
        fake.mkdir()
        marker = Path(self.tmp.name) / "python-started"
        for name in ("python3", "python", "py"):
            script = fake / name
            script.write_text("#!/bin/sh\ntouch \"$MARKER\"\nexit 1\n", encoding="utf-8")
            script.chmod(0o755)
        env = {k: v for k, v in self.env.items() if k != "POLSTOOLS_PYTHON"}
        env.update(PATH=str(fake) + os.pathsep + os.environ["PATH"], MARKER=str(marker))
        for invocation in (None, 2):
            completed = subprocess.run(
                ["sh", str(HOOK)], input=self.event(invocation), text=True,
                encoding="utf-8", capture_output=True, env=env, cwd=PLUGIN_ROOT)
            self.assertEqual({}, json.loads(completed.stdout))
        self.assertFalse(marker.exists())
        subprocess.run(
            ["sh", str(HOOK)], input=self.event(), text=True, encoding="utf-8",
            capture_output=True, env=dict(env, P_FORMAT_DEFAULT="on"), cwd=PLUGIN_ROOT)
        self.assertTrue(marker.exists())

    def test_envelopes_match_the_markdown_payloads(self):
        completed = subprocess.run(
            [sys.executable, "-B", str(PLUGIN_ROOT / "bin" / "agy-envelopes"), "--check"],
            text=True, encoding="utf-8", capture_output=True,
            env=controlled_env(self.tmp.name))
        self.assertEqual(0, completed.returncode, completed.stdout)

    def test_envelopes_help_exits_0_and_prints_the_usage(self):
        for flag_ in ("--help", "-h"):
            with self.subTest(flag=flag_):
                completed = subprocess.run(
                    [sys.executable, "-B", str(PLUGIN_ROOT / "bin" / "agy-envelopes"), flag_],
                    text=True, encoding="utf-8", capture_output=True,
                    env=controlled_env(self.tmp.name))
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertIn("agy-envelopes [--check]", completed.stdout)


if __name__ == "__main__":
    unittest.main()
