import importlib.machinery
import importlib.util
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import shutil
import tempfile
import unittest

from fake_harness import FakeHarness, patched_env, write_plugin


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


class SnapshotSafetyTests(unittest.TestCase):
    def setUp(self):
        self.update = load_update()

    def test_interrupted_backup_copy_is_never_taken_for_a_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            backup = Path(tmp) / "backup"
            installed = cache / "1.8.0"
            installed.mkdir(parents=True)
            for name in ("a", "b", "c"):
                (installed / name).write_text(name, encoding="utf-8")
            real_copytree = shutil.copytree

            def interrupted(source, target, **kwargs):
                Path(target).mkdir(parents=True)
                shutil.copy2(Path(source) / "a", Path(target) / "a")
                raise OSError("interrupted")

            self.update.shutil.copytree = interrupted
            try:
                with self.assertRaises(OSError):
                    with self.update.preserve_cache_versions(cache, backup):
                        pass
            finally:
                self.update.shutil.copytree = real_copytree
            self.assertFalse((backup / "1.8.0").exists())
            with self.update.preserve_cache_versions(cache, backup):
                shutil.rmtree(installed)
            self.assertEqual(["a", "b", "c"], sorted(p.name for p in installed.iterdir()))

    def test_a_leftover_partial_copy_is_discarded_not_restored_or_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            backup = Path(tmp) / "backup"
            (cache / "1.8.0").mkdir(parents=True)
            partial = backup / ("1.7.0" + self.update.PARTIAL_SUFFIX)
            partial.mkdir(parents=True)
            restored = self.update.reconcile_cache_versions(cache, backup)
            self.assertEqual([], restored)
            self.assertFalse(partial.exists())
            self.assertEqual([], self.update.prune_superseded_snapshots(
                cache, backup, {"1.8.0"}, now=0))
            ledger = json.loads((backup / "superseded.json").read_text("utf-8"))
            self.assertEqual({}, ledger)

    def test_failed_add_after_remove_says_codex_has_no_p(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            (cache / "1.8.0").mkdir(parents=True)

            def run(argv):
                if argv[2] == "add":
                    raise self.update.UpdateError("codex plugin add p@polstools exited 1")

            with self.assertRaisesRegex(self.update.UpdateError, "Codex has no p installed"):
                self.update.update_codex(run, cache, "1.8.0", False,
                                         backup_root=Path(tmp) / "backup")

    def test_superseded_snapshots_are_pruned_only_after_the_retention_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            backup = Path(tmp) / "backup"
            for root in (cache, backup):
                for version in ("1.7.0", "1.8.0", "1.9.0"):
                    (root / version).mkdir(parents=True)
            window = self.update.SNAPSHOT_RETENTION_SECONDS
            self.assertEqual(
                [], self.update.prune_superseded_snapshots(cache, backup, {"1.9.0"}, now=0)
            )
            self.assertEqual(
                [], self.update.prune_superseded_snapshots(cache, backup, {"1.9.0"}, now=window - 1)
            )
            pruned = self.update.prune_superseded_snapshots(cache, backup, {"1.9.0"}, now=window)
            self.assertEqual(["1.7.0", "1.8.0"], pruned)
            for root in (cache, backup):
                self.assertEqual(["1.9.0"], sorted(p.name for p in root.iterdir() if p.is_dir()))


class FakeHarnessUpdateTests(unittest.TestCase):
    """Drive main() against fake harness CLIs and a fake home."""

    def setUp(self):
        self.update = load_update()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.fake = FakeHarness(self.root)
        self.update.RUN = self.fake
        self.doctor_roots = []
        self.update.run_doctor = lambda root, **kw: self.doctor_roots.append(Path(root)) or 0
        self.sync_roots = []
        self.sync_code = 1
        self.update.sync_status_indicator = (
            lambda root, **kw: self.sync_roots.append(Path(root)) or self.sync_code
        )
        self.claude = {"version": "1.0.0", "scopes": ["user"]}
        self.native_roots = []
        self.update.resync_native = lambda root, **kw: self.native_roots.append(Path(root)) or 0

    def tearDown(self):
        self._tmp.cleanup()

    def _plugin(self, name, version):
        return write_plugin(self.root / "copies" / name / version, version,
                            {"bin/p-doctor": "# fixture doctor\n"})

    def _claude_with_p(self):
        def listing():
            root = self._plugin("claude", self.claude["version"])
            return 0, [{"id": "p@polstools", "version": self.claude["version"],
                        "scope": scope, "enabled": True, "installPath": str(root)}
                       for scope in self.claude["scopes"]]

        def updated():
            self.claude["version"] = "2.0.0"
            return 0, ""

        self.fake.on("claude", ["plugin", "list", "--json"], listing)
        for scope in ("user", "project"):
            self.fake.on("claude", ["plugin", "update", "p@polstools", "--scope", scope], updated)

    def _codex_without_p(self):
        self.fake.on("codex", ["plugin", "list", "--json"], (0, {"installed": []}))

    def _agy_with_p(self, version="1.0.0"):
        installed = self.fake.home / ".gemini" / "config" / "plugins" / "p"
        write_plugin(installed, version)
        self.fake.on("agy", ["plugin", "list"], (0, {"imports": [{
            "name": "p", "source": "antigravity",
            "importedAt": "2026-01-01T00:00:00Z", "components": ["skills"],
        }]}))
        return installed

    def _agy_installs_from(self, source_holder):
        def install(source):
            def run():
                source_holder.append(Path(source))
                shutil.rmtree(self.fake.home / ".gemini" / "config" / "plugins" / "p")
                shutil.copytree(source, self.fake.home / ".gemini" / "config" / "plugins" / "p")
                return 0, ""
            return run
        return install

    def _main(self, names, *argv):
        out, err = io.StringIO(), io.StringIO()
        with patched_env(self.fake.env("P_UPDATE_", names)), \
                redirect_stdout(out), redirect_stderr(err):
            code = self.update.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def _mutations(self):
        return [call for call in self.fake.calls
                if set(call[1:3]) & {"update", "remove", "add", "install", "upgrade"}]

    def test_harness_without_p_is_skipped_and_the_rest_update(self):
        self._claude_with_p()
        self._codex_without_p()
        code, out, err = self._main(["claude", "codex"])
        self.assertEqual(0, code, err)
        self.assertEqual(
            [("claude", "plugin", "update", "p@polstools", "--scope", "user")],
            self._mutations(),
        )
        self.assertIn("SKIP Codex", out)

    def test_unreadable_harness_stops_before_any_change(self):
        self._claude_with_p()
        self.fake.on("codex", ["plugin", "list", "--json"], (1, ""))
        code, _, err = self._main(["claude", "codex"])
        self.assertEqual(2, code)
        self.assertIn(("claude", "plugin", "list", "--json"), self.fake.calls)
        self.assertEqual([], self._mutations())

    def test_every_claude_scope_with_p_is_updated(self):
        self.claude["scopes"] = ["project", "user"]
        self._claude_with_p()
        code, _, err = self._main(["claude"])
        self.assertEqual(0, code, err)
        self.assertEqual(2, len(self._mutations()))

    def test_antigravity_installs_the_copy_claude_now_loads_and_its_version_is_verified(self):
        self._claude_with_p()
        self._agy_with_p("1.0.0")
        sources = []
        install = self._agy_installs_from(sources)
        expected = self.root / "copies" / "claude" / "2.0.0"
        self.fake.on("agy", ["plugin", "install", str(expected)], install(expected))
        code, _, err = self._main(["claude", "agy"])
        self.assertEqual(0, code, err)
        self.assertEqual([expected], sources)

    def test_antigravity_that_did_not_converge_fails_the_update(self):
        self._claude_with_p()
        self._agy_with_p("1.0.0")
        code, _, err = self._main(["claude", "agy"])
        self.assertEqual(2, code)
        self.assertIn("did not converge", err)

    def test_antigravity_alone_needs_an_explicit_source(self):
        self._agy_with_p("1.0.0")
        code, _, err = self._main(["agy"])
        self.assertEqual(2, code)
        self.assertEqual([], self._mutations())
        source = write_plugin(self.root / "release", "2.0.0", {"bin/p-doctor": "#\n"})
        sources = []
        self.fake.on("agy", ["plugin", "install", str(source)],
                     self._agy_installs_from(sources)(source))
        code, _, err = self._main(["agy"], "--agy-source", str(source))
        self.assertEqual(0, code, err)
        self.assertEqual([source], sources)

    def test_dry_run_reads_but_never_changes_anything(self):
        self._claude_with_p()
        self._codex_without_p()
        code, out, err = self._main(["claude", "codex"], "--dry-run")
        self.assertEqual(0, code, err)
        self.assertEqual([], self._mutations())
        self.assertEqual([], self.doctor_roots)
        self.assertTrue(any(line.startswith("PLAN claude") for line in out.splitlines()))

    def test_status_indicator_is_synced_from_the_updated_copy(self):
        self._claude_with_p()
        self.sync_code = 0
        code, _, err = self._main(["claude"])
        self.assertEqual(0, code, err)
        self.assertEqual([self.root / "copies" / "claude" / "2.0.0"], self.sync_roots)
        self.sync_code = 2
        self.claude["version"] = "1.0.0"
        code, _, _ = self._main(["claude"])
        self.assertEqual(1, code)

    def _codex_config(self, text):
        config = self.fake.home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(text, encoding="utf-8")

    def test_codex_skill_entries_are_regenerated_when_p_owns_a_region(self):
        self._claude_with_p()
        self._codex_config('model = "x"\n# p-skill-activation begin\n'
                           '[[skills.config]]\n# p-skill-activation end\n')
        code, out, err = self._main(["claude"], "--dry-run")
        self.assertEqual(0, code, err)
        self.assertIn("PLAN skill-profile-ctl sync-native from the updated copy", out.splitlines())
        self.assertEqual([], self.native_roots)
        code, _, err = self._main(["claude"])
        self.assertEqual(0, code, err)
        self.assertEqual([self.root / "copies" / "claude" / "2.0.0"], self.native_roots)

    def test_codex_skill_entries_are_left_alone_without_a_p_region(self):
        self._claude_with_p()
        self._codex_config('model = "x"\n')
        code, out, err = self._main(["claude"])
        self.assertEqual(0, code, err)
        self.assertEqual([], self.native_roots)
        self.assertNotIn("sync-native", out)

    def test_status_indicator_sync_runs_profile_sync_from_the_given_root(self):
        update = load_update()
        seen = []

        class Result:
            returncode = 1

        code = update.sync_status_indicator(
            Path("fixture-root"), runner=lambda argv, **kw: seen.append(argv) or Result()
        )
        self.assertEqual(1, code)
        self.assertEqual("profile-sync", seen[0][-1])
        self.assertEqual(Path("fixture-root") / "bin" / "statusline-ctl", Path(seen[0][-2]))


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
