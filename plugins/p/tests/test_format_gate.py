import json
import os
import shlex
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
FORMAT_CTL = PLUGIN_ROOT / "bin" / "format-ctl"
SESSION_VARS = (
    "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID",
    "ANTIGRAVITY_CONVERSATION_ID",
)
FORMAT_VARS = (
    "RETRO_HOME", "XDG_RUNTIME_DIR", "POLSTOOLS_PYTHON", "POLSTOOLS_SINGLE_START",
)
FORMAT_PREFIXES = ("P_FORMAT_", "P_SKILL_", "P_STATUSLINE_")


def hermetic_env(root, **extra):
    """The caller's environment minus everything that changes gate behaviour
    (notably RETRO_HOME, which turns on telemetry writes)."""
    env = {k: v for k, v in os.environ.items()
           if k not in SESSION_VARS + FORMAT_VARS
           and not k.startswith(FORMAT_PREFIXES)}
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


class SessionStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.payload = self.root / "payload.md"
        self.payload.write_text("payload\n", encoding="utf-8")

    def tearDown(self):
        for path in (self.root / "ro", self.state):
            if path.exists():
                os.chmod(path, 0o700)
        self.tmp.cleanup()

    def gate(self, sid, **extra):
        return run_ctl(["gate", str(self.payload)], hermetic_env(self.root, **extra),
                       json.dumps({"session_id": sid}))

    def toggle(self, state, sid, **extra):
        return run_ctl([state], hermetic_env(
            self.root, CLAUDE_CODE_SESSION_ID=sid, **extra))

    def test_unsafe_session_ids_stay_inside_the_state_directory(self):
        (self.root / "escape").mkdir()
        for sid in ("a/b", "../escape/x", "..", "c:\\d", "line\nbreak"):
            with self.subTest(sid=sid):
                result = self.toggle("on", sid)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.gate(sid).stdout, "payload\n")
        self.assertEqual(list((self.root / "escape").iterdir()), [])
        for entry in self.state.iterdir():
            self.assertTrue(entry.is_file())
            self.assertNotIn("escape", entry.name)

    def test_gate_does_not_probe_paths_outside_the_state_directory(self):
        self.state.mkdir()
        (self.root / "probe.on").write_text("", encoding="utf-8")
        self.assertEqual(self.gate("../probe").stdout, "")

    def test_unwritable_state_directory_exits_2_without_a_traceback(self):
        parent = self.root / "ro"
        parent.mkdir()
        os.chmod(parent, 0o500)
        if os.access(parent, os.W_OK):
            self.skipTest("this account can write to read-only directories")
        result = self.toggle("on", "s", P_FORMAT_STATE_DIR=str(parent / "state"))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("format-ctl:", result.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_state_directory_and_flags_are_private(self):
        self.state.mkdir(mode=0o755)
        os.chmod(self.state, 0o755)
        self.assertEqual(self.toggle("on", "s").returncode, 0)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        for entry in self.state.iterdir():
            self.assertEqual(entry.stat().st_mode & 0o777, 0o600)

    def test_symlinked_state_directory_is_not_trusted(self):
        real = self.root / "real"
        real.mkdir()
        try:
            self.state.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        result = self.toggle("on", "s")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(real.iterdir()), [])

    def test_a_session_that_keeps_reading_its_toggle_survives_pruning(self):
        self.assertEqual(self.toggle("on", "long-lived").returncode, 0)
        (flag,) = list(self.state.iterdir())
        old = flag.stat().st_mtime - 15 * 24 * 3600
        os.utime(flag, (old, old))
        self.assertEqual(self.gate("long-lived").stdout, "payload\n")
        self.assertEqual(self.toggle("off", "other").returncode, 0)
        self.assertEqual(self.gate("long-lived").stdout, "payload\n")

    def test_an_abandoned_toggle_is_pruned_by_the_next_toggle(self):
        self.assertEqual(self.toggle("on", "abandoned").returncode, 0)
        (flag,) = list(self.state.iterdir())
        os.utime(flag, (1, 1))
        self.assertEqual(self.toggle("off", "other").returncode, 0)
        self.assertEqual(self.gate("abandoned").stdout, "")
        self.assertEqual(len(list(self.state.iterdir())), 1)


class TelemetryTests(unittest.TestCase):
    def test_one_lifecycle_outside_a_checkout_and_none_inside_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "payload.md"
            payload.write_text("payload\n", encoding="utf-8")
            outside = root / "retro"
            inside = root / "checkout" / "retro"
            (root / "checkout" / ".git").mkdir(parents=True)
            for home in (outside, inside):
                result = run_ctl(["gate", str(payload)], hermetic_env(
                    root, P_FORMAT_DEFAULT="on", RETRO_HOME=str(home)),
                    '{"session_id": "s"}')
                self.assertEqual(result.stdout, "payload\n")
            log = outside / "telemetry" / "owned-hook-events.jsonl"
            events = [json.loads(line)["event"]
                      for line in log.read_text("utf-8").splitlines()]
            self.assertEqual(events, ["opportunity", "start", "end"])
            self.assertFalse(inside.exists())


class DefaultsFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "format.json"
        self.payload = self.root / "payload.md"
        self.payload.write_text("payload\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_malformed_defaults_file_is_never_silently_replaced(self):
        for broken in (b'{"default": "on"', b"[]", b"\xff"):
            with self.subTest(broken=broken):
                self.config.write_bytes(broken)
                before = self.config.read_bytes()
                env = hermetic_env(self.root)
                for args in (["default", "on", "--harness", "claude"],
                             ["default", "clear"], ["default"]):
                    result = run_ctl(args, env)
                    self.assertEqual(result.returncode, 2, args)
                    self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.config.read_bytes(), before)
                gate = run_ctl(["gate", str(self.payload)], env, '{"session_id": "s"}')
                self.assertEqual((gate.returncode, gate.stdout), (0, ""))

    def test_default_writes_replace_the_file_atomically(self):
        env = hermetic_env(self.root)
        self.assertEqual(run_ctl(["default", "on"], env).returncode, 0)
        self.assertEqual(run_ctl(["default", "off", "--harness", "agy"], env).returncode, 0)
        self.assertEqual(json.loads(self.config.read_text("utf-8")),
                         {"default": "on", "antigravity": "off"})
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith(".")], [])


class NestedHarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.payload = self.root / "payload.md"
        self.payload.write_text("payload\n", encoding="utf-8")
        self.nested = {"CLAUDE_CODE_SESSION_ID": "outer-claude",
                       "CODEX_THREAD_ID": "inner-codex"}

    def tearDown(self):
        self.tmp.cleanup()

    def gate(self, sid, **extra):
        return run_ctl(["gate", str(self.payload)], hermetic_env(self.root, **extra),
                       json.dumps({"session_id": sid}))

    def test_toggle_refuses_when_two_harness_sessions_are_visible(self):
        result = run_ctl(["on"], hermetic_env(self.root, **self.nested))
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / "state").exists())

    def test_named_harness_toggles_its_own_session_only(self):
        result = run_ctl(["on"], hermetic_env(
            self.root, P_FORMAT_HARNESS="codex", **self.nested))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.gate("inner-codex").stdout, "payload\n")
        self.assertEqual(self.gate("outer-claude").stdout, "")

    def test_ambiguous_hook_environment_uses_the_global_default(self):
        (self.root / "format.json").write_text(
            json.dumps({"default": "on", "claude": "off"}), encoding="utf-8")
        self.assertEqual(self.gate("inner-codex", **self.nested).stdout, "payload\n")
        self.assertEqual(
            self.gate("inner-codex", P_FORMAT_HARNESS="claude", **self.nested).stdout, "")


FORMAT_GATE = PLUGIN_ROOT / "bin" / "format-gate"
SH = shutil.which("sh")


def run_hook(env, stdin, payload, *extra):
    return subprocess.run(
        [SH, str(FORMAT_GATE), "gate", str(payload), *extra], input=stdin,
        text=True, encoding="utf-8", capture_output=True, env=env)


class HookEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.payload = self.root / "payload.md"
        self.payload.write_text("payload\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_hooks_enter_through_format_gate(self):
        hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text("utf-8"))
        for event in ("SessionStart", "UserPromptSubmit"):
            argv = shlex.split(hooks["hooks"][event][0]["hooks"][0]["command"])
            self.assertEqual(argv[:3], ["sh", "${CLAUDE_PLUGIN_ROOT}/bin/format-gate", "gate"])

    def location_envs(self):
        """Every way the toggle directory can be located, each in scratch."""
        temp = self.root / "temp"
        temp.mkdir()
        bare = {k: v for k, v in hermetic_env(self.root).items()
                if k not in ("P_FORMAT_STATE_DIR", "TMPDIR", "TEMP", "TMP")}
        return {
            "P_FORMAT_STATE_DIR": hermetic_env(self.root),
            "XDG_RUNTIME_DIR": dict(bare, XDG_RUNTIME_DIR=str(self.root / "run")),
            "TMPDIR": dict(bare, TMPDIR=str(temp)),
            "TEMP": dict(bare, TEMP=str(temp)),
            "TMP": dict(bare, TMP=str(temp)),
        }

    def test_hook_entry_and_python_gate_always_agree(self):
        (self.root / "run").mkdir(mode=0o700)
        for location, env in self.location_envs().items():
            scenarios = [
                ("no toggles", {}, None),
                ("default off env", {"P_FORMAT_DEFAULT": "off"}, None),
                ("this session on", {}, "this"),
                ("another session on", {}, "other"),
                ("on under env off", {"P_FORMAT_DEFAULT": "off"}, "this"),
                ("hook env names another session", {"CLAUDE_CODE_SESSION_ID": "other"}, "this"),
            ]
            for name, extra, toggled in scenarios:
                with self.subTest(location=location, scenario=name):
                    for state in ("state", "run", "temp"):
                        shutil.rmtree(self.root / state, ignore_errors=True)
                    (self.root / "run").mkdir(mode=0o700)
                    (self.root / "temp").mkdir()
                    if toggled:
                        made = run_ctl(["on"], dict(env, CLAUDE_CODE_SESSION_ID=toggled))
                        self.assertEqual(made.returncode, 0, made.stderr)
                    case_env = dict(env, **extra)
                    stdin = json.dumps({"session_id": "this"})
                    hook = run_hook(case_env, stdin, self.payload)
                    python = run_ctl(["gate", str(self.payload)], case_env, stdin)
                    self.assertEqual((hook.returncode, hook.stdout),
                                     (python.returncode, python.stdout))
                    expected = "payload\n" if toggled == "this" else ""
                    self.assertEqual(hook.stdout, expected)

    def test_inputs_the_shell_cannot_read_are_left_to_python(self):
        env = hermetic_env(self.root)
        stdin = json.dumps({"session_id": "this"})
        state = self.root / "state"
        state.mkdir(mode=0o700)
        # A toggle named by an earlier release (the raw session id).
        (state / "this.on").write_text("", encoding="utf-8")
        cases = [("legacy toggle name", None),
                 ("escaped on", '{"default": "\\u006fn"}'),
                 ("on under another key", '{"codex": "on"}')]
        for name, config in cases:
            with self.subTest(case=name):
                if config is not None:
                    (self.root / "format.json").write_text(config, encoding="utf-8")
                hook = run_hook(env, stdin, self.payload)
                python = run_ctl(["gate", str(self.payload)], env, stdin)
                self.assertEqual((hook.returncode, hook.stdout),
                                 (python.returncode, python.stdout))
        self.assertEqual(hook.stdout, "")
        (self.root / "format.json").write_text('{"default": "\\u006fn"}', encoding="utf-8")
        self.assertEqual(run_hook(env, stdin, self.payload).stdout, "payload\n")

    def interpreter_dirs(self, names):
        """One PATH directory per candidate, each holding a python3 that logs
        its name and then behaves as described."""
        real = sys.executable.replace("\\", "/")
        log = self.root / "attempts.log"
        old = ("import sys, runpy; sys.version_info = (3, 8, 18, 'final', 0); "
               "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")
        bodies = {
            # Not Python: swallows stdin and prints to stdout.
            "not-python": 'cat >/dev/null; echo garbage; exit 0',
            # Python that reports 3.8: the gate itself must refuse to run.
            "too-old": "exec \"%s\" -c \"%s\" \"$@\"" % (real, old),
            # Fails before reading stdin.
            "crash": 'echo "fatal: cannot start" >&2; exit 1',
            "good": 'exec "%s" "$@"' % real,
        }
        dirs = []
        for name in names:
            directory = self.root / ("bin-" + name)
            directory.mkdir()
            script = directory / "python3"
            script.write_bytes(("#!/bin/sh\necho %s >> \"%s\"\n%s\n"
                                % (name, str(log).replace("\\", "/"), bodies[name]))
                               .encode("utf-8"))
            script.chmod(0o755)
            dirs.append(str(directory))
        sh_dir = self.root / "bin-sh"
        sh_dir.mkdir()
        (sh_dir / "sh").write_bytes(
            ('#!/bin/sh\nexec "%s" "$@"\n' % SH.replace("\\", "/")).encode("utf-8"))
        (sh_dir / "sh").chmod(0o755)
        return os.pathsep.join(dirs + [str(sh_dir)]), log

    def test_each_interpreter_starts_once_and_bad_ones_fall_through(self):
        env = hermetic_env(self.root)
        self.assertEqual(run_ctl(["on"], dict(env, CLAUDE_CODE_SESSION_ID="this")).returncode, 0)
        path, log = self.interpreter_dirs(["not-python", "too-old", "crash", "good"])
        hook = run_hook(dict(env, PATH=path, POLSTOOLS_PYTHON=""),
                        json.dumps({"session_id": "this"}), self.payload)
        # The JSON reached the last interpreter intact (the toggle matched),
        # the payload appears exactly once, and nothing a rejected attempt
        # printed leaks through.
        self.assertEqual((hook.returncode, hook.stdout), (0, "payload\n"), hook.stderr)
        self.assertEqual(log.read_text("utf-8").split(),
                         ["not-python", "too-old", "crash", "good"])

    def test_no_adequate_interpreter_exits_1_with_no_output(self):
        env = dict(hermetic_env(self.root), P_FORMAT_DEFAULT="on", POLSTOOLS_PYTHON="")
        path, log = self.interpreter_dirs(["not-python", "too-old", "crash"])
        hook = run_hook(dict(env, PATH=path), '{"session_id": "s"}', self.payload)
        self.assertEqual((hook.returncode, hook.stdout), (1, ""))
        self.assertEqual(log.read_text("utf-8").split(), ["not-python", "too-old", "crash"])

    def test_off_needs_no_python_and_on_without_python_never_exits_2(self):
        only_sh = self.root / "only-sh"
        only_sh.mkdir()
        wrapper = only_sh / "sh"
        wrapper.write_bytes(
            ('#!/bin/sh\nexec "%s" "$@"\n' % SH.replace("\\", "/")).encode("utf-8"))
        wrapper.chmod(0o755)
        env = dict(hermetic_env(self.root), PATH=str(only_sh))
        off = run_hook(env, '{"session_id": "s"}', self.payload)
        self.assertEqual((off.returncode, off.stdout), (0, ""))
        on = run_hook(dict(env, P_FORMAT_DEFAULT="on"), '{"session_id": "s"}', self.payload)
        self.assertEqual((on.returncode, on.stdout), (1, ""))

    def test_bad_arguments_never_exit_2(self):
        env = hermetic_env(self.root, P_FORMAT_DEFAULT="on")
        result = run_hook(env, "{}", self.payload, "--hook-id")
        self.assertEqual(result.returncode, 1)


class FormatE2eHermeticTests(unittest.TestCase):
    """format-e2e must give the same result whatever the caller's shell holds,
    and must never write into a RETRO_HOME it did not create."""

    def run_e2e(self, **extra):
        with tempfile.TemporaryDirectory() as retro_home:
            env = dict(os.environ, RETRO_HOME=retro_home, **extra)
            result = subprocess.run(
                [sys.executable, "-B", str(PLUGIN_ROOT / "bin" / "format-e2e")],
                text=True, encoding="utf-8", capture_output=True, env=env)
            leftover = os.listdir(retro_home)
        last = result.stdout.strip().splitlines()[-1]
        passed, total = last.split()[0].split("/")
        self.assertEqual((result.returncode, passed), (0, total), result.stdout[-600:])
        self.assertEqual(leftover, [])

    def test_passes_with_retro_home_set_and_leaves_it_empty(self):
        self.run_e2e()

    def test_passes_with_a_forced_default_and_a_stray_session(self):
        self.run_e2e(P_FORMAT_DEFAULT="on", CLAUDE_CODE_SESSION_ID="stray-session")


if __name__ == "__main__":
    unittest.main()
