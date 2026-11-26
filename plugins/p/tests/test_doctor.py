import importlib.machinery
import importlib.util
import json
from pathlib import Path
import importlib.machinery
import shutil
import subprocess
import sys
import tempfile
import unittest

from fake_harness import FakeHarness, patched_env, write_plugin

from home_env import home_vars


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
        self.assertNotIn("fixture-codex-root", codex[0]["root"])
        self.assertTrue(codex[0]["root"].endswith("1.5.14"))
        explicit = self.doctor.normalize_codex_plugins({"installed": [{
            "pluginId": "p@polstools", "version": "1.5.14", "installedPath": "fixture-explicit",
        }]})
        self.assertEqual("fixture-explicit", explicit[0]["root"])

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
        self.assertIn("<python> <plugin-root>/bin/p-doctor", skill)
        self.assertIn("exit code", skill)


class FakeHarnessDoctorTests(unittest.TestCase):
    """Run collect() against fake harness CLIs and a fake home."""

    def setUp(self):
        self.doctor = load_doctor()
        self._tmp = tempfile.TemporaryDirectory()
        self.fake = FakeHarness(self._tmp.name)
        self.doctor.RUN = self.fake
        self.doctor.statusline_checks = lambda *a, **k: []
        self.doctor.activation_checks = lambda *a, **k: []
        self.doctor.python_checks = lambda *a, **k: []

    def tearDown(self):
        self._tmp.cleanup()

    def _collect(self, names, repo_root=None):
        with patched_env(self.fake.env("P_DOCTOR_", names)):
            _, checks = self.doctor.collect(repo_root)
        return {check.key: check for check in checks}, self.doctor.exit_code(checks)

    def _agy_imports_p(self, version):
        write_plugin(self.fake.home / ".gemini" / "config" / "plugins" / "p", version)
        self.fake.on("agy", ["plugin", "list"], (0, {"imports": [{
            "name": "p", "source": "antigravity",
            "importedAt": "2026-01-01T00:00:00Z", "components": ["skills"],
        }]}))

    def test_failing_agy_list_is_unavailable_not_uninstalled(self):
        self.fake.on("agy", ["plugin", "list"], (1, ""))
        found, code = self._collect(["agy"])
        self.assertEqual("ERROR", found["agy.query"].status)
        self.assertNotIn("agy.install", found)
        self.assertEqual(2, code)

    def test_no_harness_with_p_is_flagged_not_healthy(self):
        self.fake.on("agy", ["plugin", "list"], (0, "No imported plugins."))
        found, code = self._collect(["agy"])
        self.assertEqual("FAIL", found["install.any"].status)
        self.assertEqual(1, code)

    def test_no_harness_cli_at_all_is_flagged(self):
        found, code = self._collect([])
        self.assertEqual("FAIL", found["install.any"].status)
        self.assertEqual(1, code)

    def test_antigravity_install_is_healthy_without_hooks(self):
        version = self.doctor._manifest_version()
        self._agy_imports_p(version)
        found, code = self._collect(["agy"])
        self.assertEqual("PASS", found["agy.version"].status)
        self.assertFalse(any(".hook" in key for key in found))
        self.assertEqual(0, code)

    def test_antigravity_copy_without_a_manifest_is_not_a_version_match(self):
        self._agy_imports_p(self.doctor._manifest_version())
        (self.fake.home / ".gemini" / "config" / "plugins" / "p" / "plugin.json").unlink()
        found, code = self._collect(["agy"])
        self.assertEqual("FAIL", found["agy.version"].status)
        self.assertEqual(1, code)

    def test_same_version_with_different_files_is_flagged_against_a_checkout(self):
        version = self.doctor._manifest_version()
        self._agy_imports_p(version)
        repo = Path(self._tmp.name) / "checkout"
        (repo / ".claude-plugin").mkdir(parents=True)
        (repo / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"plugins": [{"name": "p", "version": version}]}), encoding="utf-8"
        )
        installed = self.fake.home / ".gemini" / "config" / "plugins" / "p"
        shutil.copytree(installed, repo / "plugins" / "p")
        found, code = self._collect(["agy"], repo_root=repo)
        self.assertEqual("PASS", found["agy.content"].status)
        (repo / "plugins" / "p" / "bin").mkdir(exist_ok=True)
        (repo / "plugins" / "p" / "bin" / "tool").write_text("changed", encoding="utf-8")
        found, code = self._collect(["agy"], repo_root=repo)
        self.assertEqual("PASS", found["agy.version"].status)
        self.assertEqual("FAIL", found["agy.content"].status)
        self.assertEqual(1, code)


class CodexInstalledRootTests(unittest.TestCase):
    """Codex's installed copy is its versioned cache directory, not the source."""

    CONTENT_CACHE = "cache content\n"
    CONTENT_SOURCE = "source content\n"

    def setUp(self):
        self.doctor = load_doctor()
        self._tmp = tempfile.TemporaryDirectory()
        self.fake = FakeHarness(self._tmp.name)
        self.doctor.RUN = self.fake
        self.doctor.statusline_checks = lambda *a, **k: []
        self.doctor.activation_checks = lambda *a, **k: []
        self.doctor.python_checks = lambda *a, **k: []
        self.version = self.doctor._manifest_version()
        self.source = Path(self._tmp.name) / "source" / "p"
        self._tree(self.source, self.CONTENT_SOURCE)
        self.fake.codex_installs_p(self.version, self.source)
        self.cache = self.fake.codex_cache_dir(self.version)

    def tearDown(self):
        self._tmp.cleanup()

    def _tree(self, root, content):
        write_plugin(root, self.version, {"example.txt": content})

    def _collect(self, repo_root=None):
        with patched_env(self.fake.env("P_DOCTOR_", ["codex"])):
            _, checks = self.doctor.collect(repo_root)
        return {check.key: check for check in checks}

    def _repo(self):
        repo = Path(self._tmp.name) / "checkout"
        (repo / ".claude-plugin").mkdir(parents=True)
        (repo / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"plugins": [{"name": "p", "version": self.version}]}), encoding="utf-8"
        )
        shutil.copytree(self.source, repo / "plugins" / "p")
        return repo

    def test_cache_copy_that_differs_from_the_source_fails_the_content_check(self):
        self._tree(self.cache, self.CONTENT_CACHE)
        found = self._collect(self._repo())
        self.assertEqual("FAIL", found["codex.content"].status)
        self.assertIn("differ from the --repo-root checkout in 1 files", found["codex.content"].summary)

    def test_missing_cache_directory_is_an_explicit_non_pass(self):
        found = self._collect()
        self.assertEqual("FAIL", found["codex.content"].status)
        self.assertIn("missing or unreadable", found["codex.content"].summary)

    def test_identical_cache_copy_passes(self):
        self._tree(self.cache, self.CONTENT_SOURCE)
        found = self._collect(self._repo())
        self.assertEqual("PASS", found["codex.content"].status)

    def test_cache_layout_matches_the_updaters(self):
        loader = importlib.machinery.SourceFileLoader(
            "p_update_layout", str(PLUGIN_ROOT / "bin" / "p-update")
        )
        update = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(update)
        with patched_env(self.fake.env("P_DOCTOR_", ["codex"])):
            expected = update.codex_cache_root() / self.version
            item = self.doctor.normalize_codex_plugins({"installed": [{
                "pluginId": "p@polstools", "name": "p", "version": self.version,
                "marketplaceName": "polstools", "enabled": True,
                "source": {"path": str(self.source), "source": "local"},
            }]})[0]
        self.assertEqual(expected, Path(item["root"]))


class ContentAndConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doctor = load_doctor()

    def test_copies_claiming_one_version_must_have_identical_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = write_plugin(Path(tmp) / "first", "2.0.0", {"bin/tool": "a"})
            second = write_plugin(Path(tmp) / "second", "2.0.0", {"bin/tool": "a"})
            (second / ".in_use").mkdir()
            (second / ".in_use" / "4242").write_text("", encoding="utf-8")
            roots = {"claude": ("2.0.0", first), "codex": ("2.0.0", second)}
            checks = self.doctor.content_checks(roots)
            self.assertEqual(["PASS"], [check.status for check in checks])
            (second / "bin" / "tool").write_text("b", encoding="utf-8")
            checks = self.doctor.content_checks(roots)
            self.assertEqual(["FAIL"], [check.status for check in checks])
            self.assertIn("bin/tool", checks[0].summary)

    def test_a_single_copy_without_a_checkout_is_not_claimed_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            only = write_plugin(Path(tmp) / "only", "2.0.0")
            checks = self.doctor.content_checks({"claude": ("2.0.0", only)})
        self.assertEqual(["SKIP"], [check.status for check in checks])

    def test_codex_config_single_quoted_table_keys_are_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.toml"
            config.write_text(
                "[hooks.state.'statusline@polstools:hooks/hooks.json:stop:0:0']\n"
                "enabled = true\n",
                encoding="utf-8",
            )
            self.assertEqual({"statusline@polstools"}, self.doctor.codex_config_ids(config))

    def test_codex_config_state_for_an_obsolete_plugin_id_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.toml"
            config.write_text(
                '[plugins."p@polstools"]\nenabled = true\n\n'
                '[hooks.state."p@polstools:hooks/hooks.json:session_start:0:0"]\n'
                'enabled = true\n',
                encoding="utf-8",
            )
            self.assertEqual(
                ["PASS"], [c.status for c in self.doctor.codex_config_checks(config)]
            )
            with config.open("a", encoding="utf-8") as handle:
                handle.write(
                    '\n[hooks.state."statusline@polstools:hooks/hooks.json:session_start:0:0"]\n'
                    'enabled = true\n'
                )
            checks = self.doctor.codex_config_checks(config)
        self.assertEqual(["FAIL"], [check.status for check in checks])
        self.assertIn("statusline@polstools", checks[0].summary)

    def test_activation_policy_runs_validate_from_the_plugin_root(self):
        seen = []

        class Result:
            def __init__(self, code):
                self.returncode = code

        for code, status in ((0, "PASS"), (2, "FAIL")):
            with self.subTest(code=code):
                checks = self.doctor.activation_checks(
                    Path("fixture-root"),
                    runner=lambda argv, **k: seen.append(argv) or Result(code),
                )
                self.assertEqual([status], [check.status for check in checks])
        self.assertEqual("validate", seen[0][-1])
        self.assertEqual(Path("fixture-root") / "bin" / "skill-profile-ctl", Path(seen[0][-2]))

    def test_statusline_check_exit_codes_map_to_check_status(self):
        class Result:
            def __init__(self, code):
                self.returncode = code

        for code, status in ((0, "PASS"), (1, "FAIL"), (2, "ERROR")):
            with self.subTest(code=code):
                checks = self.doctor.statusline_checks(runner=lambda *a, **k: Result(code))
                self.assertEqual([status], [check.status for check in checks])

    def test_statusline_check_passes_after_a_full_restore(self):
        ctl = str(PLUGIN_ROOT / "bin" / "statusline-ctl")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "userdir").mkdir()
            env = {
                **home_vars(str(root / "userdir")),
                "STATUSLINE_CLAUDE_SETTINGS": str(root / "claude-settings.json"),
                "STATUSLINE_CODEX_CONFIG": str(root / "codex-config.toml"),
                "STATUSLINE_STATE_DIR": str(root / "state"),
                "STATUSLINE_INSTALL_DIR": str(root / "install"),
                "STATUSLINE_CCSTATUSLINE_CONFIG": str(root / "ccstatusline.json"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            (root / "claude-settings.json").write_text("{}\n", "utf-8")
            (root / "codex-config.toml").write_text("[tui]\n", "utf-8")
            with patched_env(env):
                for command in ("apply", "restore"):
                    done = subprocess.run(
                        [sys.executable, "-B", ctl, command],
                        text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(0, done.returncode, done.stdout + done.stderr)
                checks = self.doctor.statusline_checks()
        self.assertEqual(["PASS"], [check.status for check in checks])

    def test_python_adequacy_follows_each_copys_launcher_exit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = {}
            for harness, code in (("claude", 0), ("codex", 2)):
                root = write_plugin(Path(tmp) / harness, "2.0.0", {
                    "bin/python-launcher": "#!/bin/sh\nexit %d\n" % code,
                })
                roots[harness] = ("2.0.0", root)
            checks = self.doctor.python_checks(roots)
        found = {check.key: check.status for check in checks}
        self.assertEqual({"claude.python": "PASS", "codex.python": "FAIL"}, found)


if __name__ == "__main__":
    unittest.main()
