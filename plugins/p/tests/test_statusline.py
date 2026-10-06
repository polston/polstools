import importlib.machinery
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
CTL_PATH = PLUGIN_ROOT / "bin" / "statusline-ctl"
PROFILE_CTL_PATH = PLUGIN_ROOT / "bin" / "skill-profile-ctl"


def load_ctl():
    loader = importlib.machinery.SourceFileLoader("statusline_ctl", str(CTL_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class StatuslineUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctl = load_ctl()

    def test_claude_provider_recognizes_earlier_p_renderer_commands_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            installed = Path(tmp) / "installed-v1"
            installed.mkdir()
            owned = (installed / "claude-statusline.py", installed / "claude-statusline.ps1")
            for path in owned:
                path.write_text("", "utf-8")
            desired = {"type": "command", "command": "python3 \"" + owned[0].as_posix() + "\""}
            ps1 = owned[1].as_posix()
            cases = {
                "pwsh -NoProfile -File \"" + ps1 + "\"": "legacy",
                "powershell -NoProfile -ExecutionPolicy Bypass -File \"" + ps1 + "\"": "legacy",
                "/usr/bin/python3 " + owned[0].as_posix(): "legacy",
                "pwsh -NoProfile -File \"" + ps1 + "\" --extra": "external",
                "bash -c \"pwsh -File " + ps1 + "\"": "external",
                "pwsh -NoProfile -File \"" + str(Path(tmp) / "elsewhere.ps1") + "\"": "external",
                "my-renderer \"" + ps1 + "\"": "external",
            }
            for command, expected in cases.items():
                with self.subTest(command=command):
                    self.assertEqual(
                        self.ctl.claude_provider({"type": "command", "command": command}, desired, owned),
                        expected,
                    )

    def test_profile_rejects_renderer_paths_outside_the_renderer_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = json.loads((PLUGIN_ROOT / "profiles" / "aligned-v1.json").read_text("utf-8"))
            for key, value in (
                ("claudeRenderer", "../bin/statusline-ctl"),
                ("claudeRendererWindows", None),
                ("claudeRenderer", "renderer/../../outside.py"),
            ):
                with self.subTest(key=key, value=value):
                    broken = dict(profile, **{key: value})
                    path = Path(tmp) / "profile.json"
                    path.write_text(json.dumps(broken), "utf-8")
                    with self.assertRaises(ValueError):
                        self.ctl.load_profile(path)

    def test_claude_provider_recognizes_only_direct_ccstatusline(self):
        desired = {"type": "command", "command": "bundled"}
        self.assertEqual(self.ctl.claude_provider(desired, desired), "bundled")
        self.assertEqual(self.ctl.claude_provider(None, desired), "missing")
        self.assertEqual(
            self.ctl.claude_provider(
                {"type": "command", "command": "/usr/local/bin/ccstatusline"},
                desired,
            ),
            "ccstatusline",
        )
        self.assertEqual(
            self.ctl.claude_provider(
                {"type": "command", "command": "npx ccstatusline"}, desired
            ),
            "external",
        )


class StatuslineCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctl = load_ctl()

    def make_env(self, root):
        env = dict(os.environ)
        env.update(
            {
                "STATUSLINE_CLAUDE_SETTINGS": str(root / "claude-settings.json"),
                "STATUSLINE_CODEX_CONFIG": str(root / "codex-config.toml"),
                "STATUSLINE_STATE_DIR": str(root / "state"),
                "STATUSLINE_INSTALL_DIR": str(root / "install"),
                "STATUSLINE_CCSTATUSLINE_CONFIG": str(root / "ccstatusline.json"),
                "USERPROFILE": str(root / "profile-marker"),
                "HOME": str(root / "profile-marker"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        return env

    def run_ctl(self, command, env):
        return subprocess.run(
            [sys.executable, str(CTL_PATH), command],
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=env,
        )

    def test_apply_is_atomic_idempotent_and_preserves_unrelated_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            private_key = "api" + "_key"
            claude_original = {
                "theme": "dark",
                private_key: "sentinel-private-value",
            }
            codex_original = (
                'model = "example"\n\n'
                '[plugins."keep"]\n'
                'enabled = true\n\n'
                '[tui]\n'
                'theme = "ansi"\n'
                'status_line = ["model", "context-used"]\n'
            )
            Path(env["STATUSLINE_CLAUDE_SETTINGS"]).write_text(
                json.dumps(claude_original, indent=2) + "\n", encoding="utf-8"
            )
            Path(env["STATUSLINE_CODEX_CONFIG"]).write_text(
                codex_original, encoding="utf-8"
            )

            first = self.run_ctl("apply", env)
            self.assertEqual(first.returncode, 0, first.stderr)
            first_claude = Path(env["STATUSLINE_CLAUDE_SETTINGS"]).read_bytes()
            first_codex = Path(env["STATUSLINE_CODEX_CONFIG"]).read_bytes()
            second = self.run_ctl("apply", env)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                Path(env["STATUSLINE_CLAUDE_SETTINGS"]).read_bytes(), first_claude
            )
            self.assertEqual(
                Path(env["STATUSLINE_CODEX_CONFIG"]).read_bytes(), first_codex
            )

            claude_now = json.loads(first_claude)
            self.assertEqual(claude_now["theme"], "dark")
            self.assertEqual(claude_now[private_key], "sentinel-private-value")
            codex_now = first_codex.decode("utf-8")
            self.assertIn('[plugins."keep"]\nenabled = true', codex_now)
            self.assertIn('[tui]\ntheme = "ansi"', codex_now)
            self.assertEqual(
                self.ctl.read_codex_status(codex_now),
                (
                    "model-with-reasoning",
                    "current-dir",
                    "git-branch",
                    "context-remaining",
                    "five-hour-limit",
                    "weekly-limit",
                ),
            )
            rollback = (root / "state" / "rollback-v1.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("sentinel-private-value", rollback)
            self.assertFalse(list(root.rglob("*.tmp")))

            checked = self.run_ctl("check", env)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

            restored = self.run_ctl("restore", env)
            self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
            self.assertEqual(
                json.loads(Path(env["STATUSLINE_CLAUDE_SETTINGS"]).read_text("utf-8")),
                claude_original,
            )
            self.assertEqual(
                Path(env["STATUSLINE_CODEX_CONFIG"]).read_text("utf-8"),
                codex_original,
            )

    def test_apply_restores_both_configs_after_each_simulated_write_failure(self):
        for fail_on in range(1, 8):
            with self.subTest(fail_on=fail_on), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                env = self.make_env(root)
                claude_path = Path(env["STATUSLINE_CLAUDE_SETTINGS"])
                codex_path = Path(env["STATUSLINE_CODEX_CONFIG"])
                claude_original = b'{"theme": "dark"}\n'
                codex_original = b'[tui]\nstatus_line = ["model"]\n'
                claude_path.write_bytes(claude_original)
                codex_path.write_bytes(codex_original)
                real_replace = self.ctl.os.replace
                calls = 0

                def fail_once(source, destination):
                    nonlocal calls
                    calls += 1
                    if calls == fail_on:
                        raise OSError("simulated replace failure")
                    return real_replace(source, destination)

                with mock.patch.dict(os.environ, env), mock.patch.object(
                    self.ctl.os, "replace", side_effect=fail_once
                ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                    io.StringIO()
                ):
                    self.assertEqual(self.ctl.main(["apply"]), 2)

                self.assertEqual(claude_path.read_bytes(), claude_original)
                self.assertEqual(codex_path.read_bytes(), codex_original)
                self.assertFalse((root / "state" / "rollback-v1.json").exists())
                self.assertFalse((root / "install" / "claude-statusline.ps1").exists())
                self.assertFalse((root / "install" / "claude-statusline.py").exists())
                self.assertFalse(list(root.rglob("*.tmp")))

    def test_sync_repairs_safe_drift_then_becomes_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            Path(env["STATUSLINE_CLAUDE_SETTINGS"]).write_text("{}\n", "utf-8")
            Path(env["STATUSLINE_CODEX_CONFIG"]).write_text("[tui]\n", "utf-8")

            first = self.run_ctl("sync", env)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertIn("applied:", first.stdout)
            self.assertIn("aligned:", first.stdout)
            first_claude = Path(env["STATUSLINE_CLAUDE_SETTINGS"]).read_bytes()
            first_codex = Path(env["STATUSLINE_CODEX_CONFIG"]).read_bytes()

            second = self.run_ctl("sync", env)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertNotIn("applied:", second.stdout)
            self.assertEqual(
                Path(env["STATUSLINE_CLAUDE_SETTINGS"]).read_bytes(), first_claude
            )
            self.assertEqual(
                Path(env["STATUSLINE_CODEX_CONFIG"]).read_bytes(), first_codex
            )

    def test_apply_preserves_ccstatusline_layout_and_adds_only_owned_widget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            claude_path = Path(env["STATUSLINE_CLAUDE_SETTINGS"])
            codex_path = Path(env["STATUSLINE_CODEX_CONFIG"])
            claude_original = {
                "theme": "dark",
                "statusLine": {"type": "command", "command": "/usr/local/bin/ccstatusline"},
            }
            codex_original = '[tui]\nstatus_line = ["model"]\n'
            cc_original = {
                "version": 3,
                "lines": [
                    [
                        {"id": "model", "type": "model", "color": "cyan"},
                        {"id": "branch", "type": "git-branch", "color": "magenta"},
                    ],
                    [{"id": "clock", "type": "clock", "metadata": {"timezone": "UTC"}}],
                    [],
                ],
                "powerline": {"enabled": True, "separators": [">"], "separatorInvertBackground": [False], "startCaps": [], "endCaps": [], "autoAlign": False, "continueThemeAcrossLines": False},
            }
            claude_path.write_text(json.dumps(claude_original, indent=2) + "\n", "utf-8")
            codex_path.write_text(codex_original, "utf-8")
            cc_path = Path(env["STATUSLINE_CCSTATUSLINE_CONFIG"])
            cc_path.write_text(json.dumps(cc_original, indent=2) + "\n", "utf-8")
            claude_bytes = claude_path.read_bytes()

            applied = self.run_ctl("apply", env)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertIn("ccstatusline preserved", applied.stdout)
            self.assertEqual(claude_path.read_bytes(), claude_bytes)
            self.assertTrue((Path(env["STATUSLINE_INSTALL_DIR"]) / "skill-profile-label.py").is_file())
            cc_now = json.loads(cc_path.read_text("utf-8"))
            owned = [
                widget
                for line in cc_now["lines"]
                for widget in line
                if (widget.get("metadata") or {}).get("p.owner") == "skill-activation-v1"
            ]
            self.assertEqual(len(owned), 1)
            self.assertEqual(cc_now["lines"][0][:-1], cc_original["lines"][0])
            self.assertEqual(cc_now["lines"][1:], cc_original["lines"][1:])
            self.assertEqual(cc_now["powerline"], cc_original["powerline"])
            rollback = json.loads(
                (root / "state" / "rollback-v1.json").read_text("utf-8")
            )
            self.assertFalse(rollback["managed"]["claude"])
            self.assertIsNone(rollback["applied"]["claude"])
            self.assertIsNone(rollback["previous"]["claude"])
            self.assertTrue(rollback["managed"]["ccstatusline"])

            checked = self.run_ctl("check", env)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn("compatible: Claude ccstatusline", checked.stdout)

            restored = self.run_ctl("restore", env)
            self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
            self.assertEqual(claude_path.read_bytes(), claude_bytes)
            self.assertEqual(codex_path.read_text("utf-8"), codex_original)
            self.assertEqual(json.loads(cc_path.read_text("utf-8")), cc_original)

    def test_profile_sync_is_idempotent_and_changes_no_codex_or_claude_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            claude_path = Path(env["STATUSLINE_CLAUDE_SETTINGS"])
            codex_path = Path(env["STATUSLINE_CODEX_CONFIG"])
            cc_path = Path(env["STATUSLINE_CCSTATUSLINE_CONFIG"])
            claude_path.write_text(
                json.dumps({"statusLine": {"type": "command", "command": "/usr/local/bin/ccstatusline"}}, indent=2) + "\n",
                "utf-8",
            )
            codex_path.write_text('[tui]\nstatus_line = ["model"]\n', "utf-8")
            cc_path.write_text(
                json.dumps({"version": 3, "lines": [[{"id": "model", "type": "model"}], [], []]}, indent=2) + "\n",
                "utf-8",
            )
            before = (claude_path.read_bytes(), codex_path.read_bytes())
            first = self.run_ctl("profile-sync", env)
            self.assertEqual(first.returncode, 0, first.stderr)
            first_cc = cc_path.read_bytes()
            second = self.run_ctl("profile-sync", env)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(cc_path.read_bytes(), first_cc)
            self.assertEqual(before, (claude_path.read_bytes(), codex_path.read_bytes()))

    def test_profile_sync_refuses_invalid_ccstatusline_without_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            claude_path = Path(env["STATUSLINE_CLAUDE_SETTINGS"])
            cc_path = Path(env["STATUSLINE_CCSTATUSLINE_CONFIG"])
            claude_path.write_text(
                json.dumps({"statusLine": {"type": "command", "command": "/usr/local/bin/ccstatusline"}}),
                "utf-8",
            )
            cc_path.write_text("{broken", "utf-8")
            before = (claude_path.read_bytes(), cc_path.read_bytes())
            result = self.run_ctl("profile-sync", env)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(before, (claude_path.read_bytes(), cc_path.read_bytes()))
            self.assertFalse(Path(env["STATUSLINE_INSTALL_DIR"]).exists())
            applied = self.run_ctl("apply", env)
            self.assertEqual(applied.returncode, 2)
            self.assertEqual(before, (claude_path.read_bytes(), cc_path.read_bytes()))
            self.assertFalse(Path(env["STATUSLINE_CODEX_CONFIG"]).exists())
            self.assertFalse((root / "state").exists())

    def test_home_work_toggle_refreshes_status_bundle_and_label_in_same_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            for name in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID"):
                env.pop(name, None)
            env.update(
                {
                    "CODEX_THREAD_ID": "test-session",
                    "P_SKILL_CONFIG_FILE": str(root / "skill-global.json"),
                    "P_SKILL_STATE_DIR": str(root / "skill-sessions"),
                    "P_CODEX_CONFIG_FILE": str(root / "skill-codex.toml"),
                }
            )
            Path(env["STATUSLINE_CLAUDE_SETTINGS"]).write_text(
                json.dumps({"statusLine": {"type": "command", "command": "/usr/local/bin/ccstatusline"}}),
                "utf-8",
            )
            Path(env["STATUSLINE_CCSTATUSLINE_CONFIG"]).write_text(
                json.dumps({"version": 3, "lines": [[{"id": "model", "type": "model"}], [], []]}),
                "utf-8",
            )

            def toggle(profile):
                return subprocess.run(
                    [sys.executable, str(PROFILE_CTL_PATH), profile],
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    env=env,
                )

            def installed_label():
                return subprocess.run(
                    [sys.executable, str(Path(env["STATUSLINE_INSTALL_DIR"]) / "skill-profile-label.py")],
                    input=json.dumps({"session_id": "test-session"}),
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    env=env,
                )

            work = toggle("work")
            self.assertEqual(work.returncode, 0, work.stderr)
            self.assertEqual(installed_label().stdout.strip(), "p:w")
            state_files = list((root / "skill-sessions").glob("*.json"))
            self.assertEqual(len(state_files), 1)
            state_files[0].write_text("{broken", encoding="utf-8")
            self.assertEqual(installed_label().stdout.strip(), "p:?")
            home = toggle("home")
            self.assertEqual(home.returncode, 0, home.stderr)
            self.assertEqual(installed_label().stdout.strip(), "p:h")

    def test_apply_refuses_unknown_external_claude_renderer_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            claude_path = Path(env["STATUSLINE_CLAUDE_SETTINGS"])
            codex_path = Path(env["STATUSLINE_CODEX_CONFIG"])
            claude_path.write_text(
                json.dumps({"statusLine": {"type": "command", "command": "custom-renderer"}}),
                "utf-8",
            )
            codex_path.write_text('[tui]\nstatus_line = ["model"]\n', "utf-8")
            before = (claude_path.read_bytes(), codex_path.read_bytes())

            applied = self.run_ctl("apply", env)
            self.assertEqual(applied.returncode, 1)
            self.assertIn("externally managed", applied.stdout)
            self.assertEqual(before, (claude_path.read_bytes(), codex_path.read_bytes()))
            self.assertFalse((root / "state").exists())

            synced = self.run_ctl("sync", env)
            self.assertEqual(synced.returncode, 1)
            self.assertIn("externally managed", synced.stdout)
            self.assertEqual(before, (claude_path.read_bytes(), codex_path.read_bytes()))
            self.assertFalse((root / "state").exists())

    def test_apply_and_restore_write_through_a_symlinked_settings_file(self):
        if os.name == "nt":
            self.skipTest("symbolic links need privileges on Windows")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            dotfiles = root / "dot"
            dotfiles.mkdir()
            links = {}
            for key, name, body in (
                ("STATUSLINE_CLAUDE_SETTINGS", "claude.json", '{"theme": "dark"}\n'),
                ("STATUSLINE_CODEX_CONFIG", "codex.toml", "[tui]\n"),
            ):
                target = dotfiles / name
                target.write_text(body, "utf-8")
                link = Path(env[key])
                link.symlink_to(target)
                links[key] = (link, target)
            self.assertEqual(self.run_ctl("apply", env).returncode, 0)
            claude_target = links["STATUSLINE_CLAUDE_SETTINGS"][1]
            codex_target = links["STATUSLINE_CODEX_CONFIG"][1]
            for link, target in links.values():
                self.assertTrue(link.is_symlink())
                self.assertEqual(link.read_text("utf-8"), target.read_text("utf-8"))
            applied = json.loads(claude_target.read_text("utf-8"))
            self.assertIn("statusLine", applied)
            self.assertEqual(applied["theme"], "dark")
            self.assertIn("status_line", codex_target.read_text("utf-8"))
            restored = self.run_ctl("restore", env)
            self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
            for key, (link, target) in links.items():
                self.assertTrue(link.is_symlink(), key)
            self.assertEqual(json.loads(claude_target.read_text("utf-8")), {"theme": "dark"})
            self.assertNotIn("status_line", codex_target.read_text("utf-8"))

    def test_restore_does_not_overwrite_later_owned_setting_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            claude_path = Path(env["STATUSLINE_CLAUDE_SETTINGS"])
            codex_path = Path(env["STATUSLINE_CODEX_CONFIG"])
            claude_path.write_text("{}\n", encoding="utf-8")
            codex_path.write_text("[tui]\n", encoding="utf-8")
            self.assertEqual(self.run_ctl("apply", env).returncode, 0)

            claude = json.loads(claude_path.read_text("utf-8"))
            claude["statusLine"] = {"type": "command", "command": "later-edit"}
            claude_path.write_text(json.dumps(claude, indent=2) + "\n", "utf-8")
            codex_path.write_text('[tui]\nstatus_line = ["model"]\n', "utf-8")

            restored = self.run_ctl("restore", env)
            self.assertEqual(restored.returncode, 1)
            self.assertEqual(
                json.loads(claude_path.read_text("utf-8"))["statusLine"]["command"],
                "later-edit",
            )
            self.assertEqual(
                self.ctl.read_codex_status(codex_path.read_text("utf-8")),
                ("model",),
            )

    def test_status_parser_accepts_toml_string_array_syntax(self):
        value = self.ctl.read_codex_status(
            "[tui]\n"
            "status_line = [\n"
            "  'model', # literal string\n"
            '  "context\\u002dremaining",\n'
            "]\n"
        )
        self.assertEqual(value, ("model", "context-remaining"))

    def test_status_parser_rejects_unsafe_target_shapes(self):
        samples = (
            '[tui]\nstatus_line = ["model", 1]\n',
            '[tui]\nstatus_line = ["model"]\nstatus_line = ["branch"]\n',
            '[tui]\nstatus_line = ["model"]\n[tui]\n',
        )
        for sample in samples:
            with self.subTest(sample=sample):
                with self.assertRaises(ValueError):
                    self.ctl.read_codex_status(sample)

    def test_preview_is_representative_and_privacy_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            result = self.run_ctl("preview", env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Claude (two lines)", result.stdout)
            self.assertIn("Codex (native footer)", result.stdout)
            self.assertIn("% left", result.stdout)
            self.assertNotIn("profile-marker", result.stdout)

    def write_legacy_install(self, root, env):
        """The state an earlier release left: a PowerShell command and its rollback."""
        legacy = {
            "type": "command",
            "command": 'pwsh -NoProfile -File "'
            + (Path(env["STATUSLINE_INSTALL_DIR"]) / "claude-statusline.ps1").resolve().as_posix()
            + '"',
        }
        claude_path = Path(env["STATUSLINE_CLAUDE_SETTINGS"])
        claude_path.write_text(json.dumps({"theme": "dark", "statusLine": legacy}, indent=2) + "\n", "utf-8")
        Path(env["STATUSLINE_CODEX_CONFIG"]).write_text(
            "[tui]\nstatus_line = " + json.dumps(list(self.ctl.CODEX_STATUS_LINE)) + "\n", "utf-8"
        )
        state = Path(env["STATUSLINE_STATE_DIR"])
        state.mkdir(parents=True)
        (state / "rollback-v1.json").write_text(
            json.dumps(
                {
                    "schema": 2,
                    "managed": {"claude": True, "codex": True, "ccstatusline": False},
                    "applied": {"claude": legacy, "codex": list(self.ctl.CODEX_STATUS_LINE), "cc_widget": None},
                    "previous": {
                        "claude": {"present": False, "value": None},
                        "codex": {"present": False, "raw": None, "sectionPresent": True},
                        "cc_widgets": None,
                    },
                }
            ),
            "utf-8",
        )
        return claude_path, legacy

    def test_earlier_p_renderer_command_is_reported_refreshed_and_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            claude_path, legacy = self.write_legacy_install(root, env)

            checked = self.run_ctl("check", env)
            self.assertEqual(checked.returncode, 1, checked.stderr)
            self.assertIn("earlier p release", checked.stdout)
            self.assertIn("repair with: statusline-ctl sync", checked.stdout)

            before = claude_path.read_bytes()
            refreshed = self.run_ctl("profile-sync", env)
            self.assertEqual(refreshed.returncode, 0, refreshed.stdout + refreshed.stderr)
            self.assertEqual(claude_path.read_bytes(), before)
            installed = Path(env["STATUSLINE_INSTALL_DIR"])
            self.assertTrue((installed / "claude-statusline.ps1").is_file())
            self.assertTrue((installed / "claude-statusline.py").is_file())

            synced = self.run_ctl("sync", env)
            self.assertEqual(synced.returncode, 0, synced.stdout + synced.stderr)
            self.assertIn("aligned:", synced.stdout)
            now = json.loads(claude_path.read_text("utf-8"))
            self.assertEqual(now["statusLine"], self.ctl.desired_claude(installed / "claude-statusline.py"))
            self.assertEqual(now["theme"], "dark")

            restored = self.run_ctl("restore", env)
            self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
            self.assertNotIn("statusLine", json.loads(claude_path.read_text("utf-8")))

    def test_apply_after_a_ccstatusline_rollback_records_the_missing_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            claude_path = Path(env["STATUSLINE_CLAUDE_SETTINGS"])
            claude_path.write_text(
                json.dumps({"statusLine": {"type": "command", "command": "/usr/local/bin/ccstatusline"}}), "utf-8"
            )
            Path(env["STATUSLINE_CCSTATUSLINE_CONFIG"]).write_text(
                json.dumps({"version": 3, "lines": [[{"id": "model", "type": "model"}]]}), "utf-8"
            )
            self.assertEqual(self.run_ctl("apply", env).returncode, 0)
            claude_path.write_text("{}\n", "utf-8")
            applied = self.run_ctl("apply", env)
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            restored = self.run_ctl("restore", env)
            self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
            self.assertNotIn("statusLine", json.loads(claude_path.read_text("utf-8")))

    def test_check_names_stale_bundle_files_and_sync_repairs_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            Path(env["STATUSLINE_CLAUDE_SETTINGS"]).write_text(
                json.dumps({"statusLine": {"type": "command", "command": "/usr/local/bin/ccstatusline"}}), "utf-8"
            )
            Path(env["STATUSLINE_CCSTATUSLINE_CONFIG"]).write_text(
                json.dumps({"version": 3, "lines": [[{"id": "model", "type": "model"}], []]}), "utf-8"
            )
            self.assertEqual(self.run_ctl("apply", env).returncode, 0)
            installed = Path(env["STATUSLINE_INSTALL_DIR"])
            (installed / "skill_activation.py").write_text("# an older copy\n", "utf-8")
            (installed / "skill-activation-v1.json").unlink()

            checked = self.run_ctl("check", env)
            self.assertEqual(checked.returncode, 1, checked.stderr)
            self.assertIn("stale: skill_activation.py, skill-activation-v1.json", checked.stdout)
            self.assertNotIn("skill-profile-label.py", checked.stdout)
            self.assertIn("repair with: statusline-ctl sync", checked.stdout)

            self.assertEqual(self.run_ctl("sync", env).returncode, 0)
            self.assertEqual(self.run_ctl("check", env).returncode, 0)

    def test_check_exits_zero_until_p_has_managed_a_statusline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            claude_path = Path(env["STATUSLINE_CLAUDE_SETTINGS"])
            for settings in ({}, {"statusLine": {"type": "command", "command": "custom-renderer"}}):
                with self.subTest(settings=settings):
                    claude_path.write_text(json.dumps(settings), "utf-8")
                    checked = self.run_ctl("check", env)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("not managed", checked.stdout)
            self.assertFalse((root / "state").exists())
            self.assertFalse((root / "install").exists())

            claude_path.write_text("{}\n", "utf-8")
            synced = self.run_ctl("sync", env)
            self.assertEqual(synced.returncode, 0, synced.stdout + synced.stderr)
            self.assertIn("applied:", synced.stdout)
            self.assertEqual(self.run_ctl("check", env).returncode, 0)
            (Path(env["STATUSLINE_INSTALL_DIR"]) / "skill_activation.py").write_text("# older\n", "utf-8")
            drifted = self.run_ctl("check", env)
            self.assertEqual(drifted.returncode, 1, drifted.stderr)
            self.assertIn("stale: skill_activation.py", drifted.stdout)

    def test_profile_sync_exit_codes_and_library_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            claude_path = Path(env["STATUSLINE_CLAUDE_SETTINGS"])
            for settings in ({}, {"statusLine": {"type": "command", "command": "custom-renderer"}}):
                with self.subTest(settings=settings):
                    claude_path.write_text(json.dumps(settings), "utf-8")
                    before = claude_path.read_bytes()
                    result = self.run_ctl("profile-sync", env)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("no supported active renderer", result.stdout)
                    self.assertEqual(claude_path.read_bytes(), before)
                    self.assertFalse((root / "install").exists())

            claude_path.write_text("{}\n", "utf-8")
            self.assertEqual(self.run_ctl("apply", env).returncode, 0)
            installed = Path(env["STATUSLINE_INSTALL_DIR"]) / "skill_activation.py"
            installed.write_text("# an older activation library\n", "utf-8")
            synced = self.run_ctl("profile-sync", env)
            self.assertEqual(synced.returncode, 0, synced.stdout + synced.stderr)
            self.assertEqual(installed.read_bytes(), (PLUGIN_ROOT / "lib" / "skill_activation.py").read_bytes())

    def test_check_offers_no_repair_for_an_external_renderer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            Path(env["STATUSLINE_CLAUDE_SETTINGS"]).write_text("{}\n", "utf-8")
            self.assertEqual(self.run_ctl("apply", env).returncode, 0)
            Path(env["STATUSLINE_CLAUDE_SETTINGS"]).write_text(
                json.dumps({"statusLine": {"type": "command", "command": "custom-renderer"}}), "utf-8"
            )
            checked = self.run_ctl("check", env)
            self.assertEqual(checked.returncode, 1, checked.stderr)
            self.assertIn("external renderer, left unmanaged", checked.stdout)
            self.assertNotIn("repair with", checked.stdout)

    def copy_plugin(self, root):
        copy = root / "plugin"
        for part in ("bin", "lib", "profiles", "renderer"):
            shutil.copytree(PLUGIN_ROOT / part, copy / part)
        return copy

    def preview_env(self, root):
        env = self.make_env(root)
        stub = root / "stub"
        stub.mkdir()
        (root / "profile-marker").mkdir()
        security = stub / "security"
        security.write_text(
            "#!/bin/sh\necho called >> \"" + (root / "keychain.log").as_posix() + "\"\nexit 44\n",
            encoding="utf-8",
        )
        security.chmod(0o755)
        env["PATH"] = str(stub) + os.pathsep + env.get("PATH", "")
        env["TMPDIR"] = str(root / "tmp")
        (root / "tmp").mkdir()
        env["XDG_CACHE_HOME"] = str(root / "cache")
        env["LOCALAPPDATA"] = str(root / "cache")
        env["P_STATUSLINE_NOW_MS"] = "1800000000000"
        env.pop("P_STATUSLINE_NO_REFRESH", None)
        return env

    def test_preview_renders_through_the_shipped_renderer_and_profile_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.preview_env(root)
            copy = self.copy_plugin(root)
            renderer = copy / "renderer" / "claude-statusline.py"
            renderer.write_text(
                renderer.read_text("utf-8").replace('+ "% left"', '+ "% spare"'), "utf-8"
            )
            profile_path = copy / "profiles" / "aligned-v1.json"
            profile = json.loads(profile_path.read_text("utf-8"))
            profile["codexStatusLine"] = ["git-branch", "model-with-reasoning"]
            profile_path.write_text(json.dumps(profile), "utf-8")
            result = subprocess.run(
                [sys.executable, str(copy / "bin" / "statusline-ctl"), "preview"],
                text=True, encoding="utf-8", capture_output=True, env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.splitlines()
            claude = lines[1:lines.index("Codex (native footer)")]
            self.assertEqual(len(claude), 2)
            self.assertIn("~/project", claude[0])
            self.assertIn("56k/200k", claude[0])
            self.assertIn("72% spare", claude[0])
            self.assertIn("model-week", claude[1])
            codex = lines[lines.index("Codex (native footer)") + 1]
            self.assertEqual(codex, "main | model high")

    def test_preview_starts_no_usage_refresh_and_never_reaches_the_keychain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.preview_env(root)
            before = {p for p in root.rglob("*") if p.name != "stub" and p.parent.name != "stub"}
            result = subprocess.run(
                [sys.executable, str(CTL_PATH), "preview"],
                text=True, encoding="utf-8", capture_output=True, env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / "keychain.log").exists())
            after = {p for p in root.rglob("*") if p.name != "stub" and p.parent.name != "stub"}
            self.assertEqual(after, before)
            for name in ("usage-cache.json", "usage-attempt.txt", "usage-refresh.lock"):
                self.assertEqual(list(root.rglob(name)), [])

    def test_apply_installs_the_renderer_the_profile_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            copy = self.copy_plugin(root)
            (copy / "renderer" / "claude-statusline.py").rename(copy / "renderer" / "status-v2.py")
            profile_path = copy / "profiles" / "aligned-v1.json"
            profile = json.loads(profile_path.read_text("utf-8"))
            profile["claudeRenderer"] = "renderer/status-v2.py"
            profile_path.write_text(json.dumps(profile), "utf-8")
            Path(env["STATUSLINE_CLAUDE_SETTINGS"]).write_text("{}\n", "utf-8")
            result = subprocess.run(
                [sys.executable, str(copy / "bin" / "statusline-ctl"), "apply"],
                text=True, encoding="utf-8", capture_output=True, env=env,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            installed = Path(env["STATUSLINE_INSTALL_DIR"])
            self.assertTrue((installed / "status-v2.py").is_file())
            command = json.loads(Path(env["STATUSLINE_CLAUDE_SETTINGS"]).read_text("utf-8"))["statusLine"]["command"]
            if os.name != "nt":
                self.assertIn((installed / "status-v2.py").resolve().as_posix(), command)

    def test_windows_target_installs_powershell_command_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_env(root)
            claude_path = Path(env["STATUSLINE_CLAUDE_SETTINGS"])
            claude_path.write_text(json.dumps({"theme": "dark"}, indent=2) + "\n", "utf-8")
            original = claude_path.read_bytes()
            out = io.StringIO()
            with mock.patch.dict(os.environ, env), mock.patch.object(
                self.ctl, "IS_WINDOWS", True
            ), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                self.assertEqual(self.ctl.main(["apply"]), 0)
                command = json.loads(claude_path.read_text("utf-8"))["statusLine"]["command"]
                installed = Path(env["STATUSLINE_INSTALL_DIR"])
                self.assertEqual(
                    command,
                    'powershell -NoProfile -ExecutionPolicy Bypass -File "'
                    + (installed / "claude-statusline.ps1").resolve().as_posix() + '"',
                )
                self.assertTrue((installed / "claude-statusline.py").is_file())
                self.assertEqual(self.ctl.main(["check"]), 0)
                self.assertEqual(self.ctl.main(["restore"]), 0)
            self.assertEqual(claude_path.read_bytes(), original)


class PackagingTests(unittest.TestCase):
    def test_consolidated_marketplace_and_manifest_publish_statusline(self):
        claude_market = json.loads(
            (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text("utf-8")
        )
        entry = claude_market["plugins"][0]
        manifest = json.loads(
            (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text("utf-8")
        )
        self.assertEqual("p", entry["name"])
        self.assertEqual("p", manifest["name"])
        self.assertEqual(entry["version"], manifest["version"])
        self.assertEqual(entry["description"], manifest["description"])
        for keyword in ("profiles", "statusline", "claude-code", "codex"):
            self.assertIn(keyword, entry["keywords"])
            self.assertIn(keyword, manifest["keywords"])

    def test_statusline_plugin_contains_no_machine_specific_home_path(self):
        pattern = re.compile(r"[A-Za-z]:[\\/](?:Users|home)[\\/][A-Za-z0-9_.-]+")
        for path in PLUGIN_ROOT.rglob("*"):
            is_text = path.suffix in {".json", ".md", ".ps1", ".py"} or path.name in {
                "skill-profile-ctl",
                "statusline-ctl",
            }
            if path.is_file() and "tests" not in path.parts and is_text:
                self.assertIsNone(pattern.search(path.read_text("utf-8")), str(path))


if __name__ == "__main__":
    unittest.main()
