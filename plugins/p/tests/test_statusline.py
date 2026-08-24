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
import time
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

    def test_percent_left_clamps_and_handles_missing_values(self):
        self.assertEqual(self.ctl.percent_left(23.5), 76.5)
        self.assertEqual(self.ctl.percent_left(-4), 100)
        self.assertEqual(self.ctl.percent_left(140), 0)
        self.assertIsNone(self.ctl.percent_left(None))

    def test_render_uses_percent_left_for_context_and_all_quotas(self):
        lines = self.ctl.render_claude(
            {
                "model": {"display_name": "Example Model"},
                "effort": {"level": "high"},
                "workspace": {"current_dir": "project", "git_branch": "main"},
                "context_window": {"remaining_percentage": 72},
                "rate_limits": {
                    "five_hour": {"used_percentage": 36},
                    "seven_day": {"used_percentage": 19},
                },
                "model_weekly": {"label": "model", "used_percentage": 12},
            },
            color=False,
        )
        self.assertEqual(len(lines), 2)
        self.assertIn("Example Model | eff high | project | main |", lines[0])
        self.assertIn("72% left", lines[0])
        self.assertIn("5h", lines[1])
        self.assertIn("64% left", lines[1])
        self.assertIn("wk", lines[1])
        self.assertIn("81% left", lines[1])
        self.assertIn("model", lines[1])
        self.assertIn("88% left", lines[1])
        self.assertIn("p:h", lines[0])

    def test_render_tolerates_missing_optional_fields(self):
        lines = self.ctl.render_claude({}, color=False)
        self.assertEqual(lines, ["p:h"])

    def test_codex_footer_order_matches_the_contract(self):
        self.assertEqual(
            list(self.ctl.CODEX_STATUS_LINE),
            [
                "model-with-reasoning",
                "current-dir",
                "git-branch",
                "context-remaining",
                "five-hour-limit",
                "weekly-limit",
            ],
        )

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

    @unittest.skipUnless(
        shutil.which("powershell" if os.name == "nt" else "pwsh"),
        "PowerShell is unavailable",
    )
    def test_powershell_renderer_matches_percent_left_semantics(self):
        sample = {
            "model": {"display_name": "Example Model"},
            "effort": {"level": "high"},
            "workspace": {"current_dir": "project", "git_branch": "main"},
            "context_window": {"remaining_percentage": 72},
            "rate_limits": {
                "five_hour": {"used_percentage": 36},
                "seven_day": {"used_percentage": 19},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["LOCALAPPDATA"] = tmp
            env["HOME"] = str(Path(tmp) / "no-credentials")
            if os.name == "nt":
                env["USERPROFILE"] = env["HOME"]
            else:
                env.pop("USERPROFILE", None)
            cache_dir = Path(tmp) / "claude-statusline"
            cache_dir.mkdir()
            (cache_dir / "usage-cache.json").write_text(
                json.dumps(
                    {
                        "at": int(time.time() * 1000),
                        "label": "model-week",
                        "percent": 12,
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "powershell" if os.name == "nt" else "pwsh",
                    "-NoProfile",
                    *(["-ExecutionPolicy", "Bypass"] if os.name == "nt" else []),
                    "-File",
                    str(PLUGIN_ROOT / "renderer" / "claude-statusline.ps1"),
                ],
                input=json.dumps(sample),
                text=True,
                encoding="utf-8",
                capture_output=True,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        self.assertIn("Example Model | eff high | project | main |", plain)
        self.assertIn("72% left", plain)
        self.assertIn("5h", plain)
        self.assertIn("64% left", plain)
        self.assertIn("wk", plain)
        self.assertIn("81% left", plain)
        self.assertIn("model-week", plain)
        self.assertIn("88% left", plain)
        self.assertIn("p:h", plain)


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

    def test_skill_defaults_to_sync_and_documents_transactional_rollback(self):
        skill = (PLUGIN_ROOT / "skills" / "aligning-statuslines" / "SKILL.md").read_text(
            "utf-8"
        )
        self.assertIn("run `statusline-ctl sync`", skill)
        self.assertIn("restores all earlier targets", skill)
        self.assertIn("without changing\neither settings file", skill)


if __name__ == "__main__":
    unittest.main()
