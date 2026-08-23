import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
CTL_PATH = PLUGIN_ROOT / "bin" / "statusline-ctl"


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

    def test_render_tolerates_missing_optional_fields(self):
        lines = self.ctl.render_claude({}, color=False)
        self.assertEqual(lines, [])

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

    @unittest.skipUnless(os.name == "nt", "PowerShell renderer is Windows-specific")
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
            env["USERPROFILE"] = str(Path(tmp) / "no-credentials")
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
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


class StatuslineCliTests(unittest.TestCase):
    def make_env(self, root):
        env = dict(os.environ)
        env.update(
            {
                "STATUSLINE_CLAUDE_SETTINGS": str(root / "claude-settings.json"),
                "STATUSLINE_CODEX_CONFIG": str(root / "codex-config.toml"),
                "STATUSLINE_STATE_DIR": str(root / "state"),
                "STATUSLINE_INSTALL_DIR": str(root / "install"),
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
                "statusLine": {"type": "command", "command": "old-renderer"},
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
            codex_now = tomllib.loads(first_codex.decode("utf-8"))
            self.assertTrue(codex_now["plugins"]["keep"]["enabled"])
            self.assertEqual(codex_now["tui"]["theme"], "ansi")
            self.assertEqual(
                codex_now["tui"]["status_line"],
                [
                    "model-with-reasoning",
                    "current-dir",
                    "git-branch",
                    "context-remaining",
                    "five-hour-limit",
                    "weekly-limit",
                ],
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
                tomllib.loads(codex_path.read_text("utf-8"))["tui"]["status_line"],
                ["model"],
            )

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
        for keyword in ("statusline", "claude-code", "codex"):
            self.assertIn(keyword, entry["keywords"])
            self.assertIn(keyword, manifest["keywords"])

    def test_statusline_plugin_contains_no_machine_specific_home_path(self):
        pattern = re.compile(r"[A-Za-z]:[\\/](?:Users|home)[\\/][A-Za-z0-9_.-]+")
        for path in PLUGIN_ROOT.rglob("*"):
            is_text = path.suffix in {".json", ".md", ".ps1"} or path.name == "statusline-ctl"
            if path.is_file() and "tests" not in path.parts and is_text:
                self.assertIsNone(pattern.search(path.read_text("utf-8")), str(path))


if __name__ == "__main__":
    unittest.main()
