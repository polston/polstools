import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
RENDERER_DIR = PLUGIN_ROOT / "renderer"
CONTRACT = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "statusline-contract.json").read_text("utf-8")
)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
SESSION_VARS = ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID")


def load_renderer():
    path = RENDERER_DIR / "claude-statusline.py"
    loader = importlib.machinery.SourceFileLoader("claude_statusline_renderer", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def load_activation():
    lib = str(PLUGIN_ROOT / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import skill_activation

    return skill_activation


class FakeHome:
    """A throwaway home, cache, activation state and a keychain stub on PATH."""

    def __init__(self, root):
        self.root = Path(root)
        self.home = self.root / "home" / "user"
        self.home.mkdir(parents=True)
        self.cache = self.root / "cache" / "claude-statusline"
        self.stub = self.root / "stub"
        self.stub.mkdir()
        self.keychain_log = self.root / "keychain.log"
        security = self.stub / "security"
        security.write_text(
            "#!/bin/sh\necho called >> \"" + self.keychain_log.as_posix() + "\"\nexit 44\n",
            encoding="utf-8",
        )
        security.chmod(0o755)

    def env(self, columns=None):
        env = dict(os.environ)
        for name in SESSION_VARS + ("COLUMNS", "USERPROFILE", "CLAUDE_CONFIG_DIR"):
            env.pop(name, None)
        env.update(
            {
                "HOME": str(self.home),
                "LOCALAPPDATA": str(self.root / "cache"),
                "XDG_CACHE_HOME": str(self.root / "cache"),
                "P_SKILL_CONFIG_FILE": str(self.root / "skill-global.json"),
                "P_SKILL_STATE_DIR": str(self.root / "skill-sessions"),
                "PATH": str(self.stub) + os.pathsep + env.get("PATH", ""),
            }
        )
        if os.name == "nt":
            env["USERPROFILE"] = str(self.home)
        if columns is not None:
            env["COLUMNS"] = str(columns)
        return env

    def write_cache(self, cache):
        """Install the case's cache and mark a refresh as just attempted."""
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        now = int(time.time() * 1000)
        (self.cache / "usage-attempt.txt").write_text(str(now), encoding="utf-8")
        target = self.cache / "usage-cache.json"
        if cache is None:
            if target.exists():
                target.unlink()
            return
        body = {"at": now - cache["age_ms"], "label": cache["label"], "percent": cache["percent"]}
        target.write_text(json.dumps(body), encoding="utf-8")


def case_input(case, home):
    raw = case["raw"] if "raw" in case else json.dumps(case["stdin"])
    return raw.replace("{home}", home.as_posix())


def run_python(stdin, env):
    return subprocess.run(
        [sys.executable, str(RENDERER_DIR / "claude-statusline.py")],
        input=stdin, text=True, encoding="utf-8", capture_output=True, env=env, timeout=30,
    )


class RendererContractTests(unittest.TestCase):
    def check_contract(self, runner, fake):
        outputs = {}
        for case in CONTRACT["cases"]:
            with self.subTest(case=case["name"]):
                fake.write_cache(case.get("cache"))
                result = runner(case_input(case, fake.home), fake.env(case.get("columns")))
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = [line.replace("{home}", fake.home.as_posix()) for line in case["expect"]]
                self.assertEqual(ANSI.sub("", result.stdout).splitlines(), expected)
                if not case.get("columns") or case["columns"] >= 20:
                    self.assertIn("\x1b[", result.stdout, "colour must survive a piped stdout")
                outputs[case["name"]] = result.stdout
        self.assertFalse(fake.keychain_log.exists(), "the render path read the keychain")
        return outputs

    def test_python_renderer_meets_the_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.check_contract(run_python, FakeHome(tmp))

class RendererProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = load_renderer()

    def render_in_process(self, fake, stdin):
        with mock.patch.dict(os.environ, fake.env(), clear=True), mock.patch.object(
            self.renderer.subprocess, "Popen"
        ) as popen, mock.patch.object(
            self.renderer, "read_credential", side_effect=AssertionError("credential read")
        ), mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            lines = self.renderer.render(stdin)
        return lines, popen

    def test_cold_cache_renders_marker_and_schedules_one_detached_refresh(self):
        sample = json.dumps({"rate_limits": {"five_hour": {"used_percentage": 10}}})
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHome(tmp)
            lines, popen = self.render_in_process(fake, sample)
            self.assertEqual(ANSI.sub("", lines[1]), "5h █████████░ 90% left | scoped --")
            self.assertEqual(popen.call_count, 1)
            argv = popen.call_args.args[0]
            self.assertEqual(argv[-1], "--update-cache")
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
            _, again = self.render_in_process(fake, sample)
            self.assertEqual(again.call_count, 0, "a second render inside 30 s must not respawn")

    def test_fresh_cache_schedules_nothing_and_no_rate_limits_reads_no_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHome(tmp)
            fake.cache.mkdir(parents=True, mode=0o700)
            now = int(time.time() * 1000)
            (fake.cache / "usage-cache.json").write_text(
                json.dumps({"at": now, "label": "model-week", "percent": 30}), "utf-8"
            )
            sample = json.dumps({"rate_limits": {"seven_day": {"used_percentage": 50}}})
            lines, popen = self.render_in_process(fake, sample)
            self.assertEqual(ANSI.sub("", lines[1]), "wk █████░░░░░ 50% left | model-week ███████░░░ 70% left")
            self.assertEqual(popen.call_count, 0)
            (fake.cache / "usage-cache.json").unlink()
            lines, popen = self.render_in_process(fake, json.dumps({"model": {"display_name": "M"}}))
            self.assertEqual(len(lines), 1)
            self.assertEqual(popen.call_count, 0)

    def test_unusable_home_attempt_and_cache_files_degrade_without_failing(self):
        sample = json.dumps(
            {"workspace": {"current_dir": "/srv/x/proj"}, "rate_limits": {"five_hour": {"used_percentage": 10}}}
        )
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHome(tmp)
            fake.cache.mkdir(parents=True, mode=0o700)
            (fake.cache / "usage-cache.json").write_text("{not json", "utf-8")
            (fake.cache / "usage-attempt.txt").write_text("yesterday", "utf-8")
            env = dict(fake.env(), HOME="")
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                self.renderer.subprocess, "Popen"
            ) as popen:
                lines = self.renderer.render(sample)
            self.assertEqual(
                [ANSI.sub("", line) for line in lines],
                ["/srv/x/proj | p:h", "5h █████████░ 90% left | scoped --"],
            )
            self.assertEqual(popen.call_count, 1)

    def test_activation_policy_error_shows_the_unknown_profile_label(self):
        activation = load_activation()
        error = activation.PolicyError("session-state directory is not trusted")
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHome(tmp)
            with mock.patch.object(activation, "resolve", side_effect=error):
                lines, _ = self.render_in_process(fake, json.dumps({"model": {"display_name": "M"}}))
        self.assertEqual([ANSI.sub("", line) for line in lines], ["M | p:?"])

    def test_an_internal_failure_still_prints_a_line_and_exits_zero(self):
        out = io.StringIO()
        with mock.patch.object(self.renderer, "render", side_effect=RuntimeError("boom")), mock.patch.object(
            self.renderer.sys, "stdin", io.StringIO("{}")
        ), contextlib.redirect_stdout(out):
            self.assertEqual(self.renderer.main([]), 0)
        self.assertEqual(out.getvalue(), "p:?\n")


class UsageRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = load_renderer()

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def opener_returning(self, payload, calls):
        def opener(request, timeout):
            calls.append((request.full_url, request.get_header("Authorization"), timeout))
            return self.Response(json.dumps(payload).encode("utf-8"))

        return opener

    def test_refresh_caches_only_label_percent_and_time(self):
        token = "sentinel-" + "token"
        credential = {"claudeAiOauth": {"accessToken": token, "expiresAt": 2 * 10 ** 13}}
        usage = {"limits": [
            {"kind": "weekly", "percent": 5},
            {"kind": "weekly_scoped", "percent": 42.5, "scope": {"model": {"display_name": "Model-Week"}}},
        ]}
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHome(tmp)
            with mock.patch.dict(os.environ, fake.env(), clear=True), mock.patch.object(
                self.renderer, "read_credential", return_value=credential
            ):
                rc = self.renderer.update_cache(now_ms=1000, opener=self.opener_returning(usage, calls))
            self.assertEqual(rc, 0)
            stored = (fake.cache / "usage-cache.json").read_text("utf-8")
            self.assertEqual(json.loads(stored), {"at": 1000, "label": "model-week", "percent": 42.5})
            self.assertNotIn(token, stored)
            self.assertEqual(calls, [(self.renderer.USAGE_URL, "Bearer " + token, 3)])
            self.assertFalse((fake.cache / "refresh.lock").exists())

    def test_refresh_is_single_flight_and_skips_expired_tokens(self):
        expired = {"claudeAiOauth": {"accessToken": "t", "expiresAt": 999}}
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHome(tmp)
            fake.cache.mkdir(parents=True, mode=0o700)
            with mock.patch.dict(os.environ, fake.env(), clear=True):
                (fake.cache / "refresh.lock").write_text("", "utf-8")
                with mock.patch.object(self.renderer, "read_credential") as read:
                    self.assertEqual(self.renderer.update_cache(now_ms=1000), 0)
                    read.assert_not_called()
                (fake.cache / "refresh.lock").unlink()
                with mock.patch.object(self.renderer, "read_credential", return_value=expired):
                    rc = self.renderer.update_cache(now_ms=1000, opener=self.opener_returning({}, calls))
            self.assertEqual(rc, 1)
            self.assertEqual(calls, [])
            self.assertFalse((fake.cache / "usage-cache.json").exists())

    @unittest.skipIf(os.name == "nt", "the keychain stub is a POSIX shell script")
    def test_keychain_is_read_only_on_macos_and_only_when_no_credential_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHome(tmp)
            security = fake.stub / "security"
            security.write_text(
                "#!/bin/sh\necho \"$*\" >> \"" + fake.keychain_log.as_posix() + "\"\n"
                "printf '%s' '{\"claudeAiOauth\": {\"accessToken\": \"k\", \"expiresAt\": 5}}'\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, fake.env(), clear=True):
                self.assertIsNone(self.renderer.read_credential(platform="linux"))
                self.assertFalse(fake.keychain_log.exists())
                found = self.renderer.read_credential(platform="darwin")
                self.assertEqual(found["claudeAiOauth"]["accessToken"], "k")
                self.assertEqual(
                    fake.keychain_log.read_text("utf-8").split(),
                    ["find-generic-password", "-s", "Claude", "Code-credentials", "-w"],
                )
                (fake.home / ".claude").mkdir()
                (fake.home / ".claude" / ".credentials.json").write_text('{"claudeAiOauth": {}}', "utf-8")
                fake.keychain_log.unlink()
                self.assertEqual(self.renderer.read_credential(platform="darwin"), {"claudeAiOauth": {}})
                self.assertFalse(fake.keychain_log.exists())


class WindowsPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = load_renderer()

    def test_home_shortening_follows_platform_path_rules(self):
        shorten = self.renderer.shorten_home
        self.assertEqual(shorten("D:\\Accounts\\Ann\\src", "D:\\Accounts\\Ann", windows=True), "~\\src")
        self.assertEqual(shorten("d:\\accounts\\ann", "D:\\Accounts\\Ann\\", windows=True), "~")
        self.assertEqual(shorten("D:\\Accounts\\AnnX\\src", "D:\\Accounts\\Ann", windows=True), "D:\\Accounts\\AnnX\\src")
        self.assertEqual(shorten("/srv/Ann/src", "/srv/ann", windows=False), "/srv/Ann/src")
        self.assertEqual(shorten("/srv/ann/src", "/srv/ann/", windows=False), "~/src")


RATE_LIMITS = {"rate_limits": {"five_hour": {"used_percentage": 10}}}
FRESH_SCOPED = {"label": "model-week", "percent": 30}


def snapshot(root):
    """Every file under root as {relative path: bytes}, links not followed."""
    found = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            found[path.relative_to(root).as_posix()] = "-> " + os.readlink(path)
        elif path.is_file():
            found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def fresh_cache_bytes():
    body = {"at": int(time.time() * 1000), **FRESH_SCOPED}
    return json.dumps(body).encode("utf-8")


class CacheDirectoryTrustTests(unittest.TestCase):
    def setUp(self):
        if os.name == "nt":
            self.skipTest("POSIX ownership and mode semantics")

    def render(self, fake):
        result = run_python(json.dumps(RATE_LIMITS), fake.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        plain = ANSI.sub("", result.stdout)
        self.assertTrue(plain.strip(), "a line must still be printed")
        self.assertNotIn("model-week", plain, "the foreign cache must not be read")
        self.assertNotIn("Traceback", result.stderr)
        return plain

    def settle(self):
        # A refresh child, if one were wrongly started, would act within this.
        time.sleep(0.5)

    def test_cache_directory_is_created_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHome(tmp)
            self.render(fake)
            self.assertEqual(os.stat(fake.cache).st_mode & 0o777, 0o700)

    def test_world_writable_cache_directory_is_not_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHome(tmp)
            fake.cache.mkdir(parents=True, mode=0o700)
            os.chmod(fake.cache, 0o777)
            planted = fake.cache / "usage-cache.json"
            planted.write_bytes(fresh_cache_bytes())
            before = snapshot(fake.cache)
            plain = self.render(fake)
            self.settle()
            self.assertEqual(plain.splitlines()[1], "5h █████████░ 90% left")
            self.assertEqual(snapshot(fake.cache), before)
            self.assertEqual(os.stat(fake.cache).st_mode & 0o777, 0o777)

    def test_group_accessible_cache_directory_is_not_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHome(tmp)
            fake.cache.mkdir(parents=True, mode=0o700)
            os.chmod(fake.cache, 0o750)
            before = snapshot(fake.cache)
            self.render(fake)
            self.settle()
            self.assertEqual(snapshot(fake.cache), before)

    def test_symlinked_cache_directory_is_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHome(tmp)
            victim = Path(tmp) / "victim-dir"
            victim.mkdir(mode=0o700)
            (victim / "usage-cache.json").write_bytes(fresh_cache_bytes())
            fake.cache.parent.mkdir(parents=True)
            fake.cache.symlink_to(victim)
            before = snapshot(victim)
            self.render(fake)
            self.settle()
            self.assertEqual(snapshot(victim), before)
            self.assertTrue(fake.cache.is_symlink())

    def test_symlinks_at_cache_file_paths_are_not_followed(self):
        for name in ("usage-cache.json", "usage-attempt.txt", "refresh.lock", "usage-cache.json.tmp"):
            with self.subTest(link=name), tempfile.TemporaryDirectory() as tmp:
                fake = FakeHome(tmp)
                fake.cache.mkdir(parents=True, mode=0o700)
                victim = Path(tmp) / "victim.txt"
                victim.write_bytes(fresh_cache_bytes())
                (fake.cache / name).symlink_to(victim)
                before = victim.read_bytes()
                self.render(fake)
                self.settle()
                self.assertEqual(victim.read_bytes(), before)

    def test_refresh_does_not_write_through_a_planted_temporary_link(self):
        renderer = load_renderer()
        token = {"claudeAiOauth": {"accessToken": "t", "expiresAt": 2 * 10 ** 13}}
        usage = {"limits": [{"kind": "weekly_scoped", "percent": 5, "scope": {"model": {"display_name": "M"}}}]}
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHome(tmp)
            fake.cache.mkdir(parents=True, mode=0o700)
            victim = Path(tmp) / "victim.txt"
            victim.write_bytes(b"precious")
            (fake.cache / "usage-cache.json.tmp").symlink_to(victim)
            response = UsageRefreshTests.Response(json.dumps(usage).encode("utf-8"))
            with mock.patch.dict(os.environ, fake.env(), clear=True), mock.patch.object(
                renderer, "read_credential", return_value=token
            ):
                renderer.update_cache(now_ms=1000, opener=lambda request, timeout: response)
            self.assertEqual(victim.read_bytes(), b"precious")
            self.assertFalse((fake.cache / "usage-cache.json").is_symlink())


class ControlCharacterTests(unittest.TestCase):
    ESC = chr(27)
    BEL = chr(7)
    C1 = chr(0x9B)
    DEL = chr(0x7F)

    def hostile(self, word):
        e = self.ESC
        return (
            e + "[31m" + word + e + "]0;owned" + self.BEL + self.C1 + "31m" + self.DEL + "\r\t"
        )

    def assert_only_own_escapes(self, text):
        own = ANSI.findall(text)
        self.assertEqual(text.count(self.ESC), len(own), "an ESC byte is not part of a colour code")
        bare = ANSI.sub("", text)
        for char in bare:
            if char == "\n":
                continue
            self.assertFalse(
                ord(char) < 32 or 0x7F <= ord(char) <= 0x9F, "control character %r in output" % char
            )

    def test_stdin_values_are_stripped_in_a_real_run(self):
        sample = {
            "model": {"display_name": self.hostile("Mod")},
            "effort": {"level": self.hostile("hi")},
            "workspace": {"current_dir": self.hostile("dir"), "git_branch": self.hostile("br")},
        }
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeHome(tmp)
            result = run_python(json.dumps(sample), fake.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_only_own_escapes(result.stdout)
        plain = ANSI.sub("", result.stdout)
        for word in ("Mod", "hi", "dir", "br"):
            self.assertIn(word, plain)

    def test_git_branch_home_and_labels_are_stripped(self):
        renderer = load_renderer()
        branch = self.hostile("feature")
        completed = subprocess.CompletedProcess([], 0, stdout=branch + "\n", stderr="")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            renderer.subprocess, "run", return_value=completed
        ):
            self.assertEqual(
                renderer._git_branch(tmp), renderer.strip_controls(branch).strip()
            )
        lines = renderer.render_lines(
            {"workspace": {"current_dir": self.hostile("/h") + "/proj"}, **RATE_LIMITS},
            profile_label=self.hostile("p:h"),
            scoped=("gauge", self.hostile("scope"), 20),
            home=self.hostile("/h"),
            branch=branch,
        )
        for line in lines:
            self.assert_only_own_escapes(line)
        plain = ANSI.sub("", "\n".join(lines))
        for word in ("feature", "p:h", "scope", "~/proj"):
            self.assertIn(word, plain)

    def test_cache_label_is_stripped(self):
        renderer = load_renderer()
        state, _ = renderer.scoped_state(
            {"at": 1000, "label": self.hostile("model"), "percent": 5}, 1000
        )
        line = renderer.render_lines({**RATE_LIMITS}, profile_label="p:h", scoped=state)[1]
        self.assert_only_own_escapes(line)
        self.assertIn("model", ANSI.sub("", line))


if __name__ == "__main__":
    unittest.main()
