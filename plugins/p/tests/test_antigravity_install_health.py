import importlib.machinery
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = PLUGIN_ROOT / "bin" / "p_validate.py"
DOCTOR_PATH = PLUGIN_ROOT / "bin" / "p-doctor"
AGY_LOADED = (
    "  \x1b[32m[ok]\x1b[0m    /tmp/x/p\n"
    "          \x1b[32m✔\x1b[0m skills      : 28 processed\n"
    "          \x1b[32m✔\x1b[0m hooks       : 1 processed\n"
)
AGY_SKIPPED = AGY_LOADED.replace(
    "\x1b[32m✔\x1b[0m hooks       : 1 processed", "- hooks       : skipped (not found)")


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def copy_plugin(destination):
    shutil.copytree(PLUGIN_ROOT, destination,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"))
    return destination


class AntigravityInstallHealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load("p_validate_agy", VALIDATOR_PATH)
        cls.doctor = load("p_doctor_agy", DOCTOR_PATH)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "userdir").mkdir()
        (root / "tmp").mkdir()
        # The doctor starts the hook with a copy of this process's environment,
        # so build that environment from a small base under the temp directory.
        base = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(root / "userdir"),
            "TMPDIR": str(root / "tmp"),
            "POLSTOOLS_PYTHON": sys.executable,
            "PYTHONDONTWRITEBYTECODE": "1",
            "P_SKILL_CONFIG_FILE": str(root / "global.json"),
            "P_SKILL_STATE_DIR": str(root / "sessions"),
        }
        patcher = mock.patch.dict(os.environ, base, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def agy_errors(self, plugin_root):
        return [e for e in self.validator.validate_package(plugin_root)
                if "Antigravity hook" in e]

    def test_validator_accepts_the_shipped_manifest(self):
        self.assertEqual([], self.agy_errors(PLUGIN_ROOT))

    def test_validator_rejects_missing_or_claude_shaped_manifests(self):
        root = copy_plugin(Path(self.tmp.name) / "p")
        (root / "hooks.json").unlink()
        self.assertEqual(1, len(self.agy_errors(root)))
        shutil.copy(root / "hooks" / "hooks.json", root / "hooks.json")
        self.assertEqual(
            ["Antigravity hook manifest does not wire the format gate"],
            self.agy_errors(root))

    def test_validator_reads_whether_agy_loaded_the_hooks(self):
        strip = lambda text: self.validator.re.sub(r"\x1b\[[0-9;]*m", "", text)
        self.assertTrue(self.validator.AGY_HOOKS_LOADED_RE.search(strip(AGY_LOADED)))
        self.assertFalse(self.validator.AGY_HOOKS_LOADED_RE.search(strip(AGY_SKIPPED)))

    def test_doctor_probe_passes_the_shipped_hook(self):
        checks = self.doctor.probe_antigravity_hook(PLUGIN_ROOT)
        self.assertEqual(
            [("agy.hook.pre_invocation_off", "PASS"),
             ("agy.hook.pre_invocation_first", "PASS"),
             ("agy.hook.pre_invocation_later", "PASS")],
            [(c.key, c.status) for c in checks])

    def test_doctor_probe_reports_an_unreadable_installed_manifest(self):
        root = copy_plugin(Path(self.tmp.name) / "p")
        shutil.copy(root / "hooks" / "hooks.json", root / "hooks.json")
        self.assertEqual(
            [("agy.hooks", "ERROR")],
            [(c.key, c.status) for c in self.doctor.probe_antigravity_hook(root)])

    def test_doctor_probe_flags_a_hook_that_does_not_inject(self):
        root = copy_plugin(Path(self.tmp.name) / "p")
        (root / "bin" / "agy-format-hook").write_text(
            "printf '{}\\n'\n", encoding="utf-8")
        statuses = {c.key: c.status for c in self.doctor.probe_antigravity_hook(root)}
        self.assertEqual("PASS", statuses["agy.hook.pre_invocation_off"])
        self.assertEqual("FAIL", statuses["agy.hook.pre_invocation_first"])
        self.assertEqual("FAIL", statuses["agy.hook.pre_invocation_later"])

    def test_registration_reads_imported_components(self):
        check = self.doctor.antigravity_hook_registration
        self.assertEqual("PASS", check({"components": ["skills", "commands", "hooks"]}).status)
        self.assertEqual("FAIL", check({"components": ["skills", "commands"]}).status)
        self.assertEqual("SKIP", check({}).status)

    def test_validator_runs_agy_with_a_scratch_home_that_is_removed(self):
        root = Path(self.tmp.name)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        seen = root / "seen-home.txt"
        output = root / "agy-output.txt"
        output.write_text(AGY_LOADED, encoding="utf-8")
        script = fake_bin / "agy"
        script.write_text(
            '#!/bin/sh\nprintf %s "$HOME" > "$SEEN_HOME"\ncat "$AGY_OUTPUT"\n',
            encoding="utf-8")
        script.chmod(0o755)
        environ = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"],
                       SEEN_HOME=str(seen), AGY_OUTPUT=str(output),
                       USERPROFILE=str(root / "userdir"))
        with mock.patch.dict(os.environ, environ, clear=True):
            errors = self.validator.antigravity_validate(PLUGIN_ROOT)
        self.assertEqual([], errors)
        recorded = seen.read_text(encoding="utf-8")
        self.assertNotEqual(str(root / "userdir"), recorded)
        self.assertNotEqual(os.environ["HOME"], recorded)
        self.assertFalse(Path(recorded).exists())


if __name__ == "__main__":
    unittest.main()
