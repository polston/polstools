import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DOCTOR_PATH = PLUGIN_ROOT / "bin" / "p-doctor"
COMMAND_PATH = PLUGIN_ROOT / "commands" / "doctor.md"


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

    def test_stale_install_is_flagged_with_harness_specific_repair(self):
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
        self.assertIn("codex plugin remove p@polstools", version.fix)
        self.assertIn("codex plugin add p@polstools", version.fix)
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

    def test_error_dominates_fail_and_fail_dominates_skip(self):
        Check = self.doctor.Check
        self.assertEqual(2, self.doctor.exit_code([Check("a", "ERROR", "x")]))
        self.assertEqual(
            1,
            self.doctor.exit_code([
                Check("a", "SKIP", "x"), Check("b", "FAIL", "x")
            ]),
        )

    def test_namespaced_command_runs_doctor_through_python_launcher(self):
        command = COMMAND_PATH.read_text(encoding="utf-8")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/bin/python-launcher", command)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/bin/p-doctor", command)
        self.assertIn("exit code", command)


if __name__ == "__main__":
    unittest.main()
