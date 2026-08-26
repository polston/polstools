import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
UPDATE_PATH = PLUGIN_ROOT / "bin" / "p-update"
SKILL_PATH = PLUGIN_ROOT / "skills" / "update" / "SKILL.md"


def load_update():
    loader = importlib.machinery.SourceFileLoader("p_update", str(UPDATE_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class CachePreservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.update = load_update()

    def _cache(self, root):
        cache = root / "cache" / "polstools" / "p"
        for version in ("1.7.0", "1.8.0"):
            package = cache / version
            package.mkdir(parents=True)
            (package / "marker.txt").write_text(version, encoding="utf-8")
        return cache

    def test_preservation_restores_removed_versions_without_overwriting_new_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = self._cache(root)
            backup = root / "durable-backup"
            with self.update.preserve_cache_versions(cache, backup):
                shutil.rmtree(cache)
                current = cache / "1.9.0"
                current.mkdir(parents=True)
                (current / "marker.txt").write_text("new", encoding="utf-8")

            self.assertEqual("1.7.0", (cache / "1.7.0" / "marker.txt").read_text("utf-8"))
            self.assertEqual("1.8.0", (cache / "1.8.0" / "marker.txt").read_text("utf-8"))
            self.assertEqual("new", (cache / "1.9.0" / "marker.txt").read_text("utf-8"))

    def test_preservation_restores_versions_when_reinstall_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = self._cache(root)
            with self.assertRaisesRegex(RuntimeError, "add failed"):
                with self.update.preserve_cache_versions(cache, root / "durable-backup"):
                    shutil.rmtree(cache)
                    raise RuntimeError("add failed")

            self.assertTrue((cache / "1.7.0" / "marker.txt").is_file())
            self.assertTrue((cache / "1.8.0" / "marker.txt").is_file())

    def test_reconcile_restores_snapshot_left_by_an_interrupted_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache" / "polstools" / "p"
            backup = root / "durable-backup"
            snapshot = backup / "1.8.0"
            snapshot.mkdir(parents=True)
            (snapshot / "marker.txt").write_text("preserved", encoding="utf-8")

            restored = self.update.reconcile_cache_versions(cache, backup)

            self.assertEqual(["1.8.0"], restored)
            self.assertEqual(
                "preserved", (cache / "1.8.0" / "marker.txt").read_text("utf-8")
            )

    def test_codex_reinstall_refuses_to_remove_without_installed_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            with self.assertRaisesRegex(self.update.UpdateError, "snapshot"):
                self.update.update_codex(
                    lambda argv: calls.append(argv),
                    Path(tmp) / "missing-cache",
                    "1.8.0",
                    refresh_marketplace=False,
                    backup_root=Path(tmp) / "durable-backup",
                )
            self.assertEqual([], calls)

    def test_codex_reinstall_restores_active_snapshot_and_uses_supported_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(Path(tmp))
            calls = []

            def run(argv):
                calls.append(argv)
                if argv[1:4] == ["plugin", "remove", "p@polstools"]:
                    shutil.rmtree(cache)
                if argv[1:4] == ["plugin", "add", "p@polstools"]:
                    current = cache / "1.9.0"
                    current.mkdir(parents=True)
                    (current / "marker.txt").write_text("new", encoding="utf-8")

            self.update.update_codex(
                run,
                cache,
                "1.8.0",
                refresh_marketplace=True,
                backup_root=Path(tmp) / "durable-backup",
            )

            self.assertEqual(
                [
                    ["codex", "plugin", "marketplace", "upgrade", "polstools"],
                    ["codex", "plugin", "remove", "p@polstools"],
                    ["codex", "plugin", "add", "p@polstools"],
                ],
                calls,
            )
            self.assertTrue((cache / "1.8.0" / "marker.txt").is_file())
            self.assertEqual("new", (cache / "1.9.0" / "marker.txt").read_text("utf-8"))

    def test_doctor_output_and_flagged_exit_are_preserved(self):
        class Result:
            returncode = 1
            stdout = "[FAIL] cross_harness.version\nRESULT: flagged\n"
            stderr = "private diagnostic"

        stream = io.StringIO()
        code = self.update.run_doctor(
            Path("fixture-plugin-root"),
            runner=lambda *args, **kwargs: Result(),
            stream=stream,
        )
        self.assertEqual(1, code)
        self.assertEqual(Result.stdout, stream.getvalue())
        self.assertNotIn(Result.stderr, stream.getvalue())


class PackagingTests(unittest.TestCase):
    def test_update_skill_and_activation_are_packaged(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("name: update", skill)
        self.assertIn("skill-profile-ctl check update", skill)
        self.assertIn("<plugin-root>/bin/p-update", skill)
        activation = json.loads(
            (PLUGIN_ROOT / "profiles" / "skill-activation-v1.json").read_text("utf-8")
        )
        self.assertEqual(
            {"source": "skill", "requires": ["control-plane"]},
            activation["components"]["update"],
        )

    def test_doctor_and_readme_route_updates_through_the_guard(self):
        doctor = (PLUGIN_ROOT / "bin" / "p-doctor").read_text(encoding="utf-8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("p-update", doctor)
        self.assertIn("sh plugins/p/bin/python-launcher plugins/p/bin/p-update", readme)
        self.assertIn("preserves prior Codex cache snapshots", readme)


if __name__ == "__main__":
    unittest.main()
