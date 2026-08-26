import json
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "p"
COMMAND_NAMES = (
    "adequacy-review",
    "statusline-apply",
    "statusline-check",
    "statusline-preview",
    "statusline-restore",
)
VALIDATOR_PATH = PLUGIN_ROOT / "bin" / "p_validate.py"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_validator():
    spec = importlib.util.spec_from_file_location("p_validate_test", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UniversalMetadataTests(unittest.TestCase):
    def test_codex_manifest_uses_the_accepted_skill_only_surface(self):
        manifest = load_json(PLUGIN_ROOT / ".codex-plugin" / "plugin.json")
        self.assertEqual("p", manifest["name"])
        self.assertEqual("1.8.1", manifest["version"])
        self.assertEqual("./skills/", manifest["skills"])
        self.assertEqual("polston", manifest["author"]["name"])
        self.assertNotIn("hooks", manifest)
        self.assertNotIn("apps", manifest)
        self.assertNotIn("mcpServers", manifest)
        required_interface = {
            "displayName",
            "shortDescription",
            "longDescription",
            "developerName",
            "category",
            "capabilities",
            "defaultPrompt",
        }
        self.assertTrue(required_interface <= set(manifest["interface"]))

    def test_repo_marketplace_has_one_available_local_plugin(self):
        marketplace = load_json(REPO_ROOT / ".agents" / "plugins" / "marketplace.json")
        self.assertEqual("polstools", marketplace["name"])
        self.assertEqual("polstools", marketplace["interface"]["displayName"])
        self.assertEqual(
            [{
                "name": "p",
                "source": {"source": "local", "path": "./plugins/p"},
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Productivity",
            }],
            marketplace["plugins"],
        )

    def test_release_metadata_is_synchronized_at_1_8_1(self):
        claude_manifest = load_json(PLUGIN_ROOT / ".claude-plugin" / "plugin.json")
        codex_manifest = load_json(PLUGIN_ROOT / ".codex-plugin" / "plugin.json")
        claude_marketplace = load_json(REPO_ROOT / ".claude-plugin" / "marketplace.json")
        claude_entry = next(
            item for item in claude_marketplace["plugins"] if item["name"] == "p"
        )
        self.assertEqual(
            {"1.8.1"},
            {
                claude_manifest["version"],
                codex_manifest["version"],
                claude_entry["version"],
            },
        )
        self.assertEqual(claude_manifest["description"], codex_manifest["description"])
        self.assertEqual(claude_manifest["description"], claude_entry["description"])


class CanonicalSkillAdapterTests(unittest.TestCase):
    def test_every_legacy_command_is_a_thin_adapter_to_a_canonical_skill(self):
        activation = load_json(PLUGIN_ROOT / "profiles" / "skill-activation-v1.json")
        for name in COMMAND_NAMES:
            with self.subTest(name=name):
                skill = PLUGIN_ROOT / "skills" / name / "SKILL.md"
                command = PLUGIN_ROOT / "commands" / (name + ".md")
                self.assertTrue(skill.is_file())
                skill_text = skill.read_text(encoding="utf-8")
                command_text = command.read_text(encoding="utf-8")
                self.assertIn("name: " + name, skill_text)
                self.assertIn("skill-profile-ctl check " + name, skill_text)
                self.assertIn(
                    "${CLAUDE_PLUGIN_ROOT}/skills/" + name + "/SKILL.md",
                    command_text,
                )
                self.assertIn("$ARGUMENTS", command_text)
                self.assertLessEqual(len(command_text.splitlines()), 12)
                self.assertEqual("skill", activation["components"][name]["source"])


class ValidationEntrypointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_validator()

    def test_python_launcher_normalizes_windows_environment_paths(self):
        launcher = (PLUGIN_ROOT / "bin" / "python-launcher").read_text(encoding="utf-8")
        self.assertIn("cygpath -u", launcher)
        self.assertIn("POLSTOOLS_UV_CACHE", launcher)

    def test_validator_is_launcher_backed(self):
        launcher = (PLUGIN_ROOT / "bin" / "p-validate").read_text(encoding="utf-8")
        self.assertIn("python-launcher", launcher)
        self.assertIn("p_validate.py", launcher)

    def test_validator_passes_source_and_both_installed_copy_smokes(self):
        result = subprocess.run(
            ["sh", "plugins/p/bin/p-validate"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("PASS source package", result.stdout)
        self.assertIn("PASS Claude installed copy", result.stdout)
        self.assertIn("PASS Codex installed copy", result.stdout)

    def test_validator_flags_a_copy_missing_its_universal_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "p"
            shutil.copytree(PLUGIN_ROOT, plugin)
            (plugin / ".codex-plugin" / "plugin.json").unlink()
            errors = self.validator.validate_package(plugin)
        self.assertIn("universal plugin manifest is missing or malformed", errors)


if __name__ == "__main__":
    unittest.main()
