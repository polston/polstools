import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PROBE = PLUGIN_ROOT / "bin" / "p-session-probe"
HOOK = PLUGIN_ROOT / "bin" / "agy-format-hook"
SECRET = "-".join(("conv", "secret", "probe"))
POSIX_MODES = hasattr(os, "getuid")
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
        self.state = self.root / "state"
        self.tmpdir = self.root / "tmp"
        self.tmpdir.mkdir()
        (self.root / "userdir").mkdir()
        # A small explicit base: nothing of the caller's session leaks in.
        self.env = {k: os.environ[k] for k in KEEP if k in os.environ}
        self.env.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "POLSTOOLS_PYTHON": sys.executable,
            "TMPDIR": str(self.tmpdir),
            "HOME": str(self.root / "userdir"),
            "P_FORMAT_STATE_DIR": str(self.state),
            "P_FORMAT_CONFIG_FILE": str(self.root / "config" / "format.json"),
        })

    def tearDown(self):
        self.tmp.cleanup()

    def probe(self, *args, **extra):
        return subprocess.run(
            [sys.executable, "-B", str(PROBE), *args], text=True,
            encoding="utf-8", capture_output=True, env=dict(self.env, **extra))

    def run_hook(self, conversation=SECRET, invocation=None, **extra):
        event = {"conversationId": conversation, "initialNumSteps": 3}
        if invocation is not None:
            event["invocationNum"] = invocation
        completed = subprocess.run(
            ["sh", str(HOOK)], input=json.dumps(event), text=True,
            encoding="utf-8", capture_output=True, env=dict(self.env, **extra))
        self.assertEqual(0, completed.returncode)
        self.assertEqual({}, json.loads(completed.stdout))
        return completed

    def arm(self):
        armed = self.probe("--arm")
        self.assertEqual(0, armed.returncode, armed.stderr)

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
        self.assertIsNone(report["format_hook"])

    def test_flags_a_session_without_any_session_variable(self):
        completed = self.probe("--json", "--harness", "codex")
        self.assertEqual(1, completed.returncode)
        self.assertEqual([], json.loads(completed.stdout)["session_variables"])

    # The six trace-safety cases, each through the real hook entry.

    def test_unarmed_probe_writes_nothing_anywhere(self):
        before = snapshot(self.root)
        for invocation in (None, 1):
            self.run_hook(invocation=invocation)
        self.assertEqual(before, snapshot(self.root))

    @unittest.skipUnless(POSIX_MODES, "POSIX permission bits")
    def test_group_or_world_accessible_directory_is_refused(self):
        self.arm()
        os.chmod(self.state, 0o777)
        before = snapshot(self.root)
        completed = self.run_hook()
        self.assertIn("not private", completed.stderr)
        self.assertEqual(before, snapshot(self.root))

    @unittest.skipUnless(POSIX_MODES, "POSIX symlinks")
    def test_symlinked_state_directory_is_refused(self):
        self.arm()
        link = self.root / "linked-state"
        link.symlink_to(self.state, target_is_directory=True)
        before = (self.state / "probe.log").read_bytes()
        completed = self.run_hook(P_FORMAT_STATE_DIR=str(link))
        self.assertIn("not a real directory", completed.stderr)
        self.assertEqual(before, (self.state / "probe.log").read_bytes())

    @unittest.skipUnless(POSIX_MODES, "POSIX symlinks")
    def test_symlinked_trace_file_leaves_the_victim_unchanged(self):
        self.arm()
        victim = self.root / "victim.txt"
        victim.write_text("untouched\n", encoding="utf-8")
        (self.state / "probe.log").unlink()
        (self.state / "probe.log").symlink_to(victim)
        completed = self.run_hook()
        self.assertIn("missing or a link", completed.stderr)
        self.assertEqual("untouched\n", victim.read_text(encoding="utf-8"))

    def test_path_shaped_conversation_id_stays_inside_the_directory(self):
        self.arm()
        outside = [p for p in snapshot(self.root) if not p[0].startswith("state")]
        self.run_hook(conversation="../../escape/x")
        self.run_hook(conversation="a/../../b")
        self.assertEqual(outside, [p for p in snapshot(self.root)
                                   if not p[0].startswith("state")])
        self.assertEqual(["probe.log"], sorted(p.name for p in self.state.iterdir()))
        for line in (self.state / "probe.log").read_text(encoding="utf-8").splitlines():
            self.assertNotIn("/", line.split("\t")[0])

    def test_a_key_that_regex_matches_an_earlier_key_still_gets_its_names_line(self):
        self.arm()
        self.run_hook(conversation="ab")
        self.run_hook(conversation="..")
        lines = (self.state / "probe.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(["N ab", "N .."],
                         [line.split("\t")[0] for line in lines if line.startswith("N ")])

    def test_help_exits_0_and_prints_the_usage(self):
        for flag_ in ("--help", "-h"):
            with self.subTest(flag=flag_):
                completed = self.probe(flag_)
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertIn("--harness", completed.stdout)

    def test_a_bad_argument_still_exits_2(self):
        self.assertEqual(2, self.probe("--nonsense").returncode)

    def test_the_antigravity_trace_is_unsupported_on_windows(self):
        loader = importlib.machinery.SourceFileLoader("p_session_probe_win", str(PROBE))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        module.WINDOWS = True
        env = dict(self.env, ANTIGRAVITY_CONVERSATION_ID="x")
        for argv in (["--arm"], ["--harness", "antigravity"]):
            with self.subTest(argv=argv):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    code = module.main(argv, env)
                self.assertEqual(2, code)
                self.assertIn("not supported on Windows", stderr.getvalue())
        self.assertFalse(self.state.exists())

    def test_armed_trace_is_private_bound_and_removed_by_the_reader(self):
        self.arm()
        trace = self.state / "probe.log"
        if POSIX_MODES:
            self.assertEqual(0o600, stat.S_IMODE(trace.stat().st_mode))
            self.assertEqual(0o700, stat.S_IMODE(self.state.stat().st_mode))
        for invocation in (None, 1, 2):
            self.run_hook(invocation=invocation, ANTIGRAVITY_CONVERSATION_ID="v")
        self.run_hook(conversation="other")
        self.assertNotIn(SECRET, self.probe("--json", "--harness", "antigravity").stdout)
        # The read above consumed the trace; arm again for the bound read.
        self.arm()
        for invocation in (None, 1, 2):
            self.run_hook(invocation=invocation, ANTIGRAVITY_CONVERSATION_ID="v")
        completed = self.probe("--json", ANTIGRAVITY_CONVERSATION_ID=SECRET)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertNotIn(SECRET, completed.stdout)
        hook = json.loads(completed.stdout)["format_hook"]
        self.assertTrue(hook["bound_to_this_session"])
        self.assertEqual([[0, 3], [1, 3], [2, 3]], hook["invocations"])
        self.assertEqual("off", hook["gate"])
        self.assertIn("conversationId", hook["stdin_keys"])
        self.assertIn("ANTIGRAVITY_CONVERSATION_ID", hook["hook_env_names"])
        self.assertFalse(trace.exists())
        self.assertFalse((self.root / "config" / "session-probe.armed").exists())
        before = snapshot(self.root)
        self.run_hook()
        self.assertEqual(before, snapshot(self.root))

    @unittest.skipUnless(POSIX_MODES, "POSIX ownership")
    def test_arming_refuses_a_state_directory_that_is_a_link(self):
        real = self.root / "real-state"
        real.mkdir(mode=0o700)
        self.state.symlink_to(real, target_is_directory=True)
        completed = self.probe("--arm")
        self.assertEqual(2, completed.returncode)
        self.assertEqual([], list(real.iterdir()))


if __name__ == "__main__":
    unittest.main()
