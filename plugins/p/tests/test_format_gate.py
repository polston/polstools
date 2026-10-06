import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
FORMAT_CTL = PLUGIN_ROOT / "bin" / "format-ctl"
SESSION_VARS = (
    "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID",
    "ANTIGRAVITY_SESSION_ID", "ANTIGRAVITY_CONVERSATION_ID",
    "AGY_SESSION_ID", "AGY_CONVERSATION_ID",
)
FORMAT_VARS = (
    "P_FORMAT_DEFAULT", "P_FORMAT_HARNESS", "P_FORMAT_STATE_DIR",
    "P_FORMAT_CONFIG_FILE", "RETRO_HOME", "XDG_RUNTIME_DIR",
)


def hermetic_env(root, **extra):
    env = {k: v for k, v in os.environ.items()
           if k not in SESSION_VARS + FORMAT_VARS}
    env["P_FORMAT_STATE_DIR"] = str(Path(root) / "state")
    env["P_FORMAT_CONFIG_FILE"] = str(Path(root) / "format.json")
    env.update(extra)
    return env


def run_ctl(args, env, stdin=""):
    return subprocess.run(
        [sys.executable, "-B", str(FORMAT_CTL), *args], input=stdin,
        text=True, encoding="utf-8", capture_output=True, env=env)


class GateInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.payload = self.root / "payload.md"
        self.payload.write_text("payload\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_non_object_or_non_string_session_input_follows_the_default(self):
        env = hermetic_env(self.root, P_FORMAT_DEFAULT="on")
        for stdin in ("[]", "null", '"text"', "5", '{"session_id": 5}',
                      '{"session_id": null}', '{"session_id": ["a"]}'):
            with self.subTest(stdin=stdin):
                result = run_ctl(["gate", str(self.payload)], env, stdin)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "payload\n")
                self.assertNotIn("Traceback", result.stderr)

    def test_gate_with_bad_arguments_never_exits_2(self):
        env = hermetic_env(self.root)
        for args in (["gate"], ["gate", str(self.payload), "--unknown", "x"]):
            with self.subTest(args=args):
                result = run_ctl(args, env, "{}")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
