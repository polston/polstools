import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PLUGIN_ROOT / "bin" / "python-launcher"
SH = shutil.which("sh")
PROBE = "import sys; print('%d.%d' % sys.version_info[:2])"


def write_script(path, body):
    path.write_bytes(("#!/bin/sh\n" + body + "\n").encode("utf-8"))
    path.chmod(0o755)


class LauncherSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.stubs = root / "stubs"
        self.good = root / "good"
        self.stubs.mkdir()
        self.good.mkdir()
        real = sys.executable.replace("\\", "/")
        write_script(self.good / "python3", 'exec "%s" "$@"' % real)
        # The Windows Store alias prints an install hint and exits 9009.
        write_script(self.stubs / "store-alias",
                     'echo "Python was not found" >&2; exit 9009')
        # An interpreter older than the floor fails the version probe.
        write_script(self.stubs / "too-old",
                     'case "$*" in *version_info*) exit 1;; esac; echo old; exit 0')
        # A shim that exits 0 for anything and prints nothing.
        write_script(self.stubs / "silent-success", 'exit 0')

    def tearDown(self):
        self.tmp.cleanup()

    def run_launcher(self, path, **extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ("POLSTOOLS_PYTHON", "APPDATA", "LOCALAPPDATA")}
        env["PATH"] = os.pathsep.join(str(p) for p in path)
        env.update(extra)
        return subprocess.run([SH, str(LAUNCHER), "-c", PROBE], text=True,
                              capture_output=True, env=env)

    def test_an_unusable_first_interpreter_falls_through_to_a_usable_one(self):
        for stub in ("store-alias", "too-old", "silent-success"):
            with self.subTest(stub=stub):
                bad = Path(self.tmp.name) / ("bad-" + stub)
                bad.mkdir()
                shutil.copy(self.stubs / stub, bad / "python3")
                result = self.run_launcher([bad, self.good])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(),
                                 "%d.%d" % sys.version_info[:2])

    def test_an_unusable_override_is_reported_and_skipped(self):
        result = self.run_launcher(
            [self.good], POLSTOOLS_PYTHON=str(self.stubs / "too-old"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("POLSTOOLS_PYTHON", result.stderr)

    def test_no_usable_interpreter_exits_2(self):
        bad = Path(self.tmp.name) / "only-bad"
        bad.mkdir()
        shutil.copy(self.stubs / "too-old", bad / "python3")
        result = self.run_launcher([bad])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
