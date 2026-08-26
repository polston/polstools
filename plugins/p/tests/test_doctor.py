import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
DOCTOR_PATH = PLUGIN_ROOT / "bin" / "p-doctor"
SKILL_PATH = PLUGIN_ROOT / "skills" / "doctor" / "SKILL.md"


def load_doctor():
    loader = importlib.machinery.SourceFileLoader("p_doctor", str(DOCTOR_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class HarnessEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doctor = load_doctor()

    @staticmethod
    def _by_key(checks):
        return {check.key: check for check in checks}

    def test_healthy_install_matches_expected_version(self):
        checks = self.doctor.evaluate_harness(
            "claude",
            [{
                "id": "p@polstools",
                "version": "1.5.14",
                "enabled": True,
                "root": "fixture-plugin-root",
                "scope": "user",
            }],
            "1.5.14",
        )
        found = self._by_key(checks)
        self.assertEqual("PASS", found["claude.install"].status)
        self.assertEqual("PASS", found["claude.version"].status)
        self.assertEqual("PASS", found["claude.obsolete"].status)
        self.assertEqual(0, self.doctor.exit_code(checks))

    def test_stale_install_is_flagged_with_guarded_cross_harness_repair(self):
        checks = self.doctor.evaluate_harness(
            "codex",
            [{
                "id": "p@polstools",
                "version": "1.5.13",
                "enabled": True,
                "root": "fixture-plugin-root",
                "scope": "user",
            }],
            "1.5.14",
        )
        version = self._by_key(checks)["codex.version"]
        self.assertEqual("FAIL", version.status)
        self.assertIn("1.5.13", version.summary)
        self.assertIn("p-update", version.fix)
        self.assertEqual(1, self.doctor.exit_code(checks))

    def test_obsolete_polstools_ids_are_flagged(self):
        checks = self.doctor.evaluate_harness(
            "claude",
            [
                {
                    "id": "p@polstools",
                    "version": "1.5.14",
                    "enabled": True,
                    "root": "fixture-plugin-root",
                    "scope": "user",
                },
                {
                    "id": "statusline@polstools",
                    "version": "0.1.0",
                    "enabled": True,
                    "root": "old-plugin-root",
                    "scope": "user",
                },
            ],
            "1.5.14",
        )
        obsolete = self._by_key(checks)["claude.obsolete"]
        self.assertEqual("FAIL", obsolete.status)
        self.assertIn("statusline@polstools", obsolete.summary)
        self.assertIn("claude plugin uninstall statusline@polstools", obsolete.fix)

    def test_missing_install_is_skipped_not_failed(self):
        checks = self.doctor.evaluate_harness("codex", [], "1.5.14")
        found = self._by_key(checks)
        self.assertEqual("SKIP", found["codex.install"].status)
        self.assertEqual(0, self.doctor.exit_code(checks))


class HookProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doctor = load_doctor()

    def _plugin(self, root):
        plugin = root / "plugin"
        (plugin / "hooks").mkdir(parents=True)
        (plugin / "style").mkdir()
        (plugin / "style" / "full.md").write_text("full payload\n", "utf-8")
        (plugin / "style" / "turn.md").write_text("turn payload\n", "utf-8")
        hooks = {
            "hooks": {
                "SessionStart": [{"hooks": [{
                    "type": "command",
                    "command": "sh \"${CLAUDE_PLUGIN_ROOT}/bin/python-launcher\" "
                    "\"${CLAUDE_PLUGIN_ROOT}/bin/format-ctl\" gate "
                    "\"${CLAUDE_PLUGIN_ROOT}/style/full.md\"",
                }]}],
                "UserPromptSubmit": [{"hooks": [{
                    "type": "command",
                    "command": "sh \"${CLAUDE_PLUGIN_ROOT}/bin/python-launcher\" "
                    "\"${CLAUDE_PLUGIN_ROOT}/bin/format-ctl\" gate "
                    "\"${CLAUDE_PLUGIN_ROOT}/style/turn.md\"",
                }]}],
            }
        }
        (plugin / "hooks" / "hooks.json").write_text(
            json.dumps(hooks), encoding="utf-8"
        )
        return plugin

    def test_missing_python_is_reported_without_raw_stderr(self):
        class Result:
            returncode = 2
            stdout = ""
            stderr = "python-launcher: Python 3 not found at private-path"

        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            checks = self.doctor.probe_plugin_hooks(
                "codex", plugin, runner=lambda *args, **kwargs: Result()
            )
        self.assertTrue(all(check.status == "FAIL" for check in checks))
        self.assertTrue(all("Python 3 is unavailable" in check.summary for check in checks))
        rendered = self.doctor.render(checks, expected_version="1.5.14")
        self.assertNotIn("private-path", rendered)

    def test_generic_hook_failure_is_distinct_from_missing_python(self):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "internal failure at private-path"

        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            checks = self.doctor.probe_plugin_hooks(
                "claude", plugin, runner=lambda *args, **kwargs: Result()
            )
        self.assertTrue(all(check.status == "FAIL" for check in checks))
        self.assertTrue(all("exited 1" in check.summary for check in checks))
        rendered = self.doctor.render(checks, expected_version="1.5.14")
        self.assertNotIn("private-path", rendered)

    def test_success_requires_byte_exact_payload(self):
        class Result:
            returncode = 0
            stderr = ""

            def __init__(self, stdout):
                self.stdout = stdout

        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))

            def runner(argv, **kwargs):
                payload = Path(argv[argv.index("gate") + 1]).read_text("utf-8")
                return Result(payload)

            checks = self.doctor.probe_plugin_hooks("claude", plugin, runner=runner)
        self.assertEqual(["PASS", "PASS"], [check.status for check in checks])


class SchemaAndPackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doctor = load_doctor()

    def test_normalizes_current_claude_and_codex_json_shapes(self):
        claude = self.doctor.normalize_claude_plugins([{
            "id": "p@polstools",
            "version": "1.5.14",
            "scope": "user",
            "enabled": True,
            "installPath": "fixture-claude-root",
        }])
        codex = self.doctor.normalize_codex_plugins({"installed": [{
            "pluginId": "p@polstools",
            "version": "1.5.14",
            "enabled": True,
            "source": {"source": "local", "path": "fixture-codex-root"},
        }]})
        self.assertEqual("fixture-claude-root", claude[0]["root"])
        self.assertEqual("fixture-codex-root", codex[0]["root"])

    def test_local_marketplace_version_is_read_without_exposing_its_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin" / "marketplace.json").write_text(
                json.dumps({"plugins": [{"name": "p", "version": "1.5.14"}]}),
                encoding="utf-8",
            )
            self.assertEqual("1.5.14", self.doctor.marketplace_version(root))

    def test_package_metadata_check_requires_synchronized_universal_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "p"
            (plugin / ".claude-plugin").mkdir(parents=True)
            (plugin / ".codex-plugin").mkdir()
            base = {"name": "p", "version": "1.8.0", "description": "fixture"}
            (plugin / ".claude-plugin" / "plugin.json").write_text(
                json.dumps(base), encoding="utf-8"
            )
            (plugin / ".codex-plugin" / "plugin.json").write_text(
                json.dumps(base), encoding="utf-8"
            )
            checks = self.doctor.package_metadata_checks(plugin)
            self.assertEqual(["PASS", "PASS"], [check.status for check in checks])
            drifted = dict(base, version="1.7.0")
            (plugin / ".codex-plugin" / "plugin.json").write_text(
                json.dumps(drifted), encoding="utf-8"
            )
            checks = self.doctor.package_metadata_checks(plugin)
            self.assertEqual("FAIL", checks[1].status)
            self.assertIn("version or description differs", checks[1].summary)

    def test_error_dominates_fail_and_fail_dominates_skip(self):
        Check = self.doctor.Check
        self.assertEqual(2, self.doctor.exit_code([Check("a", "ERROR", "x")]))
        self.assertEqual(
            1,
            self.doctor.exit_code([
                Check("a", "SKIP", "x"), Check("b", "FAIL", "x")
            ]),
        )

    def test_native_skill_runs_doctor_through_python_launcher(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("name: doctor", skill)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/bin/python-launcher", skill)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/bin/p-doctor", skill)
        self.assertIn("exit code", skill)

    def test_release_metadata_uses_feature_version(self):
        marketplace = json.loads(
            (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        manifest = json.loads(
            (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        codex_manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        entry = next(item for item in marketplace["plugins"] if item["name"] == "p")
        self.assertEqual("1.9.0", entry["version"])
        self.assertEqual("1.9.0", manifest["version"])
        self.assertEqual("1.9.0", codex_manifest["version"])
        self.assertEqual(manifest["description"], codex_manifest["description"])


if __name__ == "__main__":
    unittest.main()
