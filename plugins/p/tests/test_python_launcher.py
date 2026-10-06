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

    def test_a_missing_override_is_reported_and_skipped(self):
        result = self.run_launcher(
            [self.good], POLSTOOLS_PYTHON=str(Path(self.tmp.name) / "no-such-python"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "%d.%d" % sys.version_info[:2])
        self.assertIn("POLSTOOLS_PYTHON", result.stderr)
        self.assertEqual(result.stderr.count("\n"), 1, result.stderr)

    def test_an_unset_or_empty_override_prints_nothing(self):
        for extra in ({}, {"POLSTOOLS_PYTHON": ""}):
            with self.subTest(extra=extra):
                result = self.run_launcher([self.good], **extra)
                self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_single_start_replays_the_input_byte_for_byte(self):
        replay = Path(self.tmp.name) / "replay"
        replay.mkdir()
        write_script(replay / "python3",
                     'printf "%s" "$POLSTOOLS_READY_TOKEN"; cat')
        env = {k: v for k, v in os.environ.items() if k != "POLSTOOLS_PYTHON"}
        env.update(PATH=str(replay) + os.pathsep + os.environ["PATH"],
                   POLSTOOLS_SINGLE_START="1")
        for text in ("", "\n", "one", "one\ntwo", "one\ntwo\n", "a\n\n\n",
                     "  lead\\n  tab\t\n"):
            with self.subTest(text=text):
                result = subprocess.run([SH, str(LAUNCHER), "target"], input=text,
                                        text=True, capture_output=True, env=env)
                self.assertEqual((result.returncode, result.stdout), (0, text))


class UvFallbackCacheTests(unittest.TestCase):
    """With no Python on PATH the launcher falls back to uv; a stub uv reports
    the cache directory it was given."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        write_script(self.bin / "uv", 'echo "cache=${UV_CACHE_DIR-unset}"')
        write_script(self.bin / "sh", 'exec "%s" "$@"' % SH.replace("\\", "/"))
        if os.name != "nt":
            (self.bin / "mkdir").symlink_to(shutil.which("mkdir"))
        self.tmpdir = self.root / "tmp"
        self.tmpdir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_launcher(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ("POLSTOOLS_PYTHON", "POLSTOOLS_UV_CACHE", "UV_CACHE_DIR",
                            "TMPDIR", "TEMP", "APPDATA", "LOCALAPPDATA")}
        env["PATH"] = str(self.bin)
        env.update(extra)
        return subprocess.run([SH, str(LAUNCHER), "-c", "pass"], text=True,
                              capture_output=True, env=env)

    def test_without_tmpdir_uv_keeps_its_own_per_user_cache(self):
        result = self.run_launcher()
        self.assertEqual(result.stdout.strip(), "cache=unset", result.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX ownership and links")
    def test_a_real_private_tmpdir_cache_is_used(self):
        result = self.run_launcher(TMPDIR=str(self.tmpdir))
        self.assertEqual(result.stdout.strip(),
                         "cache=%s" % (self.tmpdir / "polstools-uv-cache"))

    @unittest.skipIf(os.name == "nt", "POSIX ownership and links")
    def test_a_planted_link_or_foreign_file_is_not_used(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.tmpdir / "polstools-uv-cache").symlink_to(elsewhere)
        result = self.run_launcher(TMPDIR=str(self.tmpdir))
        self.assertEqual(result.stdout.strip(), "cache=unset", result.stderr)
        (self.tmpdir / "polstools-uv-cache").unlink()
        (self.tmpdir / "polstools-uv-cache").write_text("not a directory")
        result = self.run_launcher(TMPDIR=str(self.tmpdir))
        self.assertEqual(result.stdout.strip(), "cache=unset", result.stderr)

    def test_an_explicit_cache_is_never_second_guessed(self):
        result = self.run_launcher(UV_CACHE_DIR=str(self.root / "mine"))
        self.assertEqual(result.stdout.strip(), "cache=%s" % (self.root / "mine"))


if __name__ == "__main__":
    unittest.main()
