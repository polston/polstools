import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from fake_harness import garbled_cli
from home_env import home_vars


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = PLUGIN_ROOT / "bin" / "p_validate.py"
DOCTOR_PATH = PLUGIN_ROOT / "bin" / "p-doctor"
AGY_LOADED = "skills: 25 processed\nhooks: skipped (not found)\n"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


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
        # Keep diagnostics isolated from the operator's configuration.
        base = {
            "PATH": os.environ.get("PATH", ""),
            **home_vars(str(root / "userdir")),
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

    def test_a_harness_query_that_hangs_is_an_error_not_a_hang(self):
        def hangs(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

        with mock.patch.object(self.doctor, "RUN", hangs):
            with self.assertRaisesRegex(RuntimeError, "did not finish"):
                self.doctor._run_json("claude", ["plugin", "list", "--json"])
        with self.assertRaisesRegex(RuntimeError, "did not finish"):
            self.doctor.query_agy("agy", runner=hangs)

    def _garbled_cli(self):
        return garbled_cli(self.tmp.name)

    def test_non_utf8_harness_output_is_a_query_failure_not_a_traceback(self):
        cli = self._garbled_cli()
        with self.assertRaisesRegex(RuntimeError, "UTF-8"):
            self.doctor._run_json(cli, ["plugin", "list", "--json"])
        with self.assertRaisesRegex(RuntimeError, "UTF-8"):
            self.doctor.query_agy(cli)
        with mock.patch.object(self.doctor, "_find_executable", lambda name: cli), \
                mock.patch.object(self.doctor, "package_metadata_checks", lambda: []):
            _, checks = self.doctor.collect()
        queries = [c for c in checks if c.key.endswith(".query")]
        self.assertEqual(3, len(queries))
        self.assertTrue(all(c.status == "ERROR" for c in queries))

    def _agy_payload(self, *imports):
        return {"imports": list(imports)}

    def test_unrelated_antigravity_import_is_not_a_polstools_id(self):
        plugins = self.doctor.normalize_agy_plugins(self._agy_payload(
            {"name": "p", "source": "https://github.com/polston/polstools/tree/main/plugins/p"},
            {"name": "someone-elses-plugin", "source": "https://example.invalid/other/repo"},
        ))
        checks = self.doctor.evaluate_harness("agy", plugins, "1.0.0")
        obsolete = [c for c in checks if c.key == "agy.obsolete"]
        self.assertEqual(["PASS"], [c.status for c in obsolete])
        self.assertNotIn("someone-elses-plugin", "".join(c.summary + c.fix for c in checks))

    def test_former_and_polstools_sourced_imports_are_obsolete_with_bare_names(self):
        plugins = self.doctor.normalize_agy_plugins(self._agy_payload(
            {"name": "statusline", "source": "x"},
            {"name": "extra", "source": "https://github.com/polston/polstools/tree/main/plugins/extra"},
        ))
        checks = self.doctor.evaluate_harness("agy", plugins, "1.0.0")
        fail = [c for c in checks if c.key == "agy.obsolete"][0]
        self.assertEqual("FAIL", fail.status)
        self.assertIn("`agy plugin uninstall statusline`", fail.fix)
        self.assertIn("`agy plugin uninstall extra`", fail.fix)
        self.assertNotIn("@polstools`", fail.fix)

    def test_non_object_antigravity_json_is_unreadable_not_a_crash(self):
        config = Path(self.tmp.name) / "userdir" / ".gemini" / "config"
        (config / "plugins" / "p").mkdir(parents=True)
        (config / "config.json").write_text("[]", encoding="utf-8")
        (config / "plugins" / "p" / "plugin.json").write_text("[]", encoding="utf-8")
        plugins = self.doctor.normalize_agy_plugins(self._agy_payload({"name": "p"}))
        self.assertEqual("", plugins[0]["version"])
        self.assertTrue(plugins[0]["enabled"])

    def test_current_agy_json_array_shape_normalizes_and_queries_with_json_flag(self):
        plugin_dir = Path(self.tmp.name) / "userdir" / ".gemini" / "config" / "plugins" / "p"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text('{"name":"p","version":"1.21.3"}', encoding="utf-8")
        seen_argv = []

        def fake_run(argv, **kwargs):
            seen_argv.append(argv)
            return subprocess.CompletedProcess(
                argv, 0,
                stdout='[{"name":"p","enabled":true,"version":"1.21.3","source":"antigravity","path":"%s","skills":["writing-goals"]}]' % str(plugin_dir).replace("\\", "\\\\"),
                stderr="",
            )

        raw = self.doctor.query_agy("agy", runner=fake_run)
        self.assertEqual([["agy", "plugin", "list", "--json"]], seen_argv)
        plugins = self.doctor.normalize_agy_plugins(raw)
        self.assertEqual(1, len(plugins))
        self.assertEqual("p@polstools", plugins[0]["id"])
        self.assertEqual("1.21.3", plugins[0]["version"])
        self.assertTrue(plugins[0]["enabled"])
        self.assertEqual(str(plugin_dir), plugins[0]["root"])
        self.assertEqual(["skills"], plugins[0]["components"])

    def test_validator_runs_agy_with_a_scratch_home_that_is_removed(self):
        root = Path(self.tmp.name)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        seen = root / "seen-env.txt"
        output = root / "agy-output.txt"
        output.write_text(AGY_LOADED, encoding="utf-8")
        names = ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                 "XDG_CACHE_HOME", "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA")
        script = fake_bin / "agy"
        lines = "".join('printf "%%s=%%s\\n" %s "$%s" >> "$SEEN_ENV"\n' % (n, n) for n in names)
        script.write_text("#!/bin/sh\n" + lines + 'cat "$AGY_OUTPUT"\n', encoding="utf-8")
        script.chmod(0o755)
        if os.name == "nt":
            # shutil.which finds only PATHEXT names on Windows, where a real
            # agy is an .exe or a .cmd shim. The shim runs a native Python
            # recorder: an MSYS sh would rewrite HOME into its own /c/... form
            # before recording it.
            (fake_bin / "agy.py").write_text(
                "import os, sys\n"
                "with open(os.environ['SEEN_ENV'], 'a', encoding='utf-8') as seen:\n"
                "    for name in %r:\n"
                "        seen.write('%%s=%%s\\n' %% (name, os.environ.get(name, '')))\n"
                "with open(os.environ['AGY_OUTPUT'], 'rb') as out:\n"
                "    sys.stdout.buffer.write(out.read())\n" % (names,),
                encoding="utf-8")
            (fake_bin / "agy.cmd").write_text(
                '@"%s" "%%~dp0agy.py" %%*\r\n' % sys.executable, encoding="utf-8")
        operator = str(root / "userdir")
        environ = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"],
                       SEEN_ENV=str(seen), AGY_OUTPUT=str(output), USERPROFILE=operator,
                       **{n: operator for n in names if n not in ("HOME", "USERPROFILE")})
        with mock.patch.dict(os.environ, environ, clear=True):
            errors = self.validator.antigravity_validate(PLUGIN_ROOT)
        self.assertEqual([], errors)
        recorded = dict(line.split("=", 1) for line in seen.read_text(encoding="utf-8").splitlines())
        self.assertEqual(set(names), set(recorded))
        scratch = Path(recorded["HOME"]).parent
        for name in names:
            self.assertNotEqual(operator, recorded[name], name)
            self.assertTrue(str(recorded[name]).startswith(str(scratch)), name)
        self.assertFalse(scratch.exists())


if __name__ == "__main__":
    unittest.main()
