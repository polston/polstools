import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
LIB_PATH = PLUGIN_ROOT / "lib" / "skill_activation.py"
CTL_PATH = PLUGIN_ROOT / "bin" / "skill-profile-ctl"
MANIFEST_PATH = PLUGIN_ROOT / "profiles" / "skill-activation-v1.json"


def load_activation():
    spec = importlib.util.spec_from_file_location("skill_activation_test", LIB_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ActivationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.activation = load_activation()
        cls.manifest = cls.activation.load_manifest(MANIFEST_PATH)

    def env(self, root, session="session-a"):
        return {
            "P_SKILL_CONFIG_FILE": str(root / "global.json"),
            "P_SKILL_STATE_DIR": str(root / "sessions"),
            "P_CODEX_CONFIG_FILE": str(root / "config.toml"),
            "CODEX_THREAD_ID": session,
        }

    def test_manifest_has_exact_source_coverage(self):
        self.activation.validate_manifest(self.manifest, PLUGIN_ROOT)

    def test_no_state_preserves_every_existing_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolved = self.activation.resolve(self.manifest, env=self.env(Path(tmp)))
        self.assertEqual(resolved["profile"], "home")
        self.assertTrue(
            all(
                self.activation.component_state(resolved, component) == "enabled"
                for component in self.manifest["components"]
            )
        )

    def test_work_disables_private_workflows_and_limits_mixed_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self.env(Path(tmp))
            env["P_SKILL_PROFILE"] = "work"
            resolved = self.activation.resolve(self.manifest, env=env)
        disabled = {
            component
            for component in self.manifest["components"]
            if self.activation.component_state(resolved, component) == "disabled"
        }
        self.assertEqual(
            disabled,
            {
                "auditing-a-repo-for-private-data",
                "auditing-workflow-rules-against-behavior",
                "counting-stopped-promises",
                "deciding-the-prompt-cache-ttl",
                "finding-friction-in-recent-sessions",
                "reviewing-evaluation-taxonomies",
            },
        )
        self.assertEqual(
            {
                component
                for component in self.manifest["components"]
                if self.activation.component_state(resolved, component) == "limited"
            },
            {"maintaining-the-format-plugin", "scouting-tools-for-open-frictions"},
        )

    def test_precedence_is_environment_then_session_then_global_then_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.env(root)
            self.activation.set_profile(self.manifest, "work", "global", env=env)
            self.assertEqual(self.activation.resolve(self.manifest, env=env)["profile"], "work")
            self.activation.set_profile(self.manifest, "home", "session", env=env)
            self.assertEqual(self.activation.resolve(self.manifest, env=env)["profile"], "home")
            env["P_SKILL_PROFILE"] = "work"
            resolved = self.activation.resolve(self.manifest, env=env)
            self.assertEqual((resolved["profile"], resolved["source"]), ("work", "environment"))

    def test_session_state_is_isolated_and_filename_hides_the_session_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.env(root, "session-private-marker")
            second = self.env(root, "session-b")
            self.activation.set_profile(self.manifest, "work", "session", env=first)
            self.assertEqual(self.activation.resolve(self.manifest, env=first)["profile"], "work")
            self.assertEqual(self.activation.resolve(self.manifest, env=second)["profile"], "home")
            names = [path.name for path in (root / "sessions").iterdir()]
            self.assertFalse(any("session-private-marker" in name for name in names))

    def test_overrides_enable_disable_and_cannot_change_control_plane(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self.env(Path(tmp))
            self.activation.set_profile(self.manifest, "work", "session", env=env)
            self.activation.set_override(
                self.manifest,
                "auditing-a-repo-for-private-data",
                True,
                "session",
                env=env,
            )
            resolved = self.activation.resolve(self.manifest, env=env)
            self.assertEqual(
                self.activation.component_state(resolved, "auditing-a-repo-for-private-data"),
                "enabled",
            )
            self.activation.set_override(
                self.manifest, "checking-branch-base-before-a-pr", False, "session", env=env
            )
            resolved = self.activation.resolve(self.manifest, env=env)
            self.assertEqual(
                self.activation.component_state(resolved, "checking-branch-base-before-a-pr"),
                "disabled",
            )
            with self.assertRaises(self.activation.PolicyError):
                self.activation.set_override(self.manifest, "home", False, "session", env=env)

    def test_component_override_enables_its_conditional_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self.env(Path(tmp))
            self.activation.set_profile(self.manifest, "work", "session", env=env)
            resolved = self.activation.resolve(self.manifest, env=env)
            self.assertFalse(
                self.activation.capability_enabled(
                    resolved, "local-session-history", "maintaining-the-format-plugin"
                )
            )
            self.activation.set_override(
                self.manifest, "maintaining-the-format-plugin", True, "session", env=env
            )
            resolved = self.activation.resolve(self.manifest, env=env)
            self.assertTrue(
                self.activation.capability_enabled(
                    resolved, "local-session-history", "maintaining-the-format-plugin"
                )
            )

    def test_invalid_higher_precedence_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.env(root)
            self.activation.set_profile(self.manifest, "home", "global", env=env)
            path = self.activation.session_state_path("session-a", env)
            path.parent.mkdir(parents=True)
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(self.activation.PolicyError):
                self.activation.resolve(self.manifest, env=env)
            env["P_SKILL_PROFILE"] = "unknown"
            with self.assertRaises(self.activation.PolicyError):
                self.activation.resolve(self.manifest, env=env)

    def test_profile_selection_and_reset_can_repair_malformed_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.env(root)
            path = self.activation.session_state_path("session-a", env)
            path.parent.mkdir(parents=True)
            path.write_text("{broken", encoding="utf-8")
            self.activation.set_profile(self.manifest, "work", "session", env=env)
            self.assertEqual(self.activation.resolve(self.manifest, env=env)["profile"], "work")
            path.write_text("{broken", encoding="utf-8")
            self.activation.reset_scope(self.manifest, "session", env=env)
            self.assertFalse(path.exists())

    def test_stale_override_warns_without_breaking_other_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.env(root)
            self.activation.write_state(
                root / "global.json", "home", {"removed-component": False}
            )
            resolved = self.activation.resolve(self.manifest, env=env)
            self.assertEqual(resolved["stale_overrides"], ["removed-component"])
            self.assertEqual(
                self.activation.component_state(resolved, "checking-branch-base-before-a-pr"),
                "enabled",
            )

    def test_atomic_replace_failure_leaves_old_state_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.activation.write_state(path, "home", {})
            original = path.read_bytes()
            with mock.patch.object(self.activation.os, "replace", side_effect=OSError("fail")):
                with self.assertRaises(OSError):
                    self.activation.write_state(path, "work", {})
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_pruning_removes_only_old_owned_json_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.env(root)
            directory = root / "sessions"
            directory.mkdir()
            old = directory / ("a" * 64 + ".json")
            keep = directory / ("b" * 64 + ".json")
            unrelated = directory / "keep.txt"
            for path in (old, keep, unrelated):
                path.write_text("x", encoding="utf-8")
            os.utime(old, (1, 1))
            self.activation.prune_session_state(env=env, now=time.time())
            self.assertFalse(old.exists())
            self.assertTrue(keep.exists())
            self.assertTrue(unrelated.exists())


class NativeAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.activation = load_activation()
        cls.manifest = cls.activation.load_manifest(MANIFEST_PATH)

    def resolution(self, profile, overrides=None):
        return self.activation._resolution(
            self.manifest, profile, overrides or {}, "test"
        )

    def test_work_adapter_is_owned_exact_path_output_and_preserves_unrelated_config(self):
        original = (
            'model = "example"\n\n'
            '[[skills.config]]\nname = "unrelated"\nenabled = true\n\n'
            '[tui]\nstatus_line = ["model"]\n'
        )
        updated = self.activation.update_native_text(
            original, PLUGIN_ROOT, self.resolution("work")
        )
        self.assertIn('name = "unrelated"', updated)
        self.assertIn('status_line = ["model"]', updated)
        self.assertEqual(updated.count(self.activation.NATIVE_BEGIN), 1)
        self.assertEqual(updated.count("[[skills.config]]"), 7)
        for component in (
            "auditing-a-repo-for-private-data",
            "reviewing-evaluation-taxonomies",
        ):
            self.assertIn(component, updated)

    def test_adapter_replaces_stale_owned_paths_and_home_removes_only_owned_region(self):
        stale = (
            'theme = "ansi"\n\n'
            + self.activation.NATIVE_BEGIN
            + '\n[[skills.config]]\npath = "/stale/SKILL.md"\nenabled = false\n'
            + self.activation.NATIVE_END
            + "\n"
        )
        work = self.activation.update_native_text(stale, PLUGIN_ROOT, self.resolution("work"))
        self.assertNotIn("/stale/", work)
        home = self.activation.update_native_text(work, PLUGIN_ROOT, self.resolution("home"))
        self.assertEqual(home, 'theme = "ansi"\n\n')

    def test_adapter_refuses_malformed_owned_markers(self):
        with self.assertRaises(self.activation.PolicyError):
            self.activation.update_native_text(
                self.activation.NATIVE_BEGIN + "\n", PLUGIN_ROOT, self.resolution("work")
            )

    def test_adapter_does_not_claim_marker_text_inside_unrelated_values(self):
        original = (
            'note = "' + self.activation.NATIVE_BEGIN + '"\n'
            'other = "' + self.activation.NATIVE_END + '"\n'
        )
        updated = self.activation.update_native_text(
            original, PLUGIN_ROOT, self.resolution("work")
        )
        self.assertTrue(updated.startswith(original))
        self.assertEqual(updated.count(self.activation.NATIVE_BEGIN), 2)
        self.assertEqual(updated.count(self.activation.NATIVE_END), 2)

    def test_adapter_preserves_crlf_newlines(self):
        original = '[tui]\r\nstatus_line = ["model"]\r\n'
        updated = self.activation.update_native_text(
            original, PLUGIN_ROOT, self.resolution("work")
        )
        self.assertNotIn("\n", updated.replace("\r\n", ""))
        restored = self.activation.update_native_text(
            updated, PLUGIN_ROOT, self.resolution("home")
        )
        self.assertEqual(restored, original)


class ActivationCliTests(unittest.TestCase):
    def env(self, root, session="session-a"):
        env = dict(os.environ)
        for name in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID"):
            env.pop(name, None)
        env.update(
            {
                "P_SKILL_CONFIG_FILE": str(root / "global.json"),
                "P_SKILL_STATE_DIR": str(root / "sessions"),
                "P_CODEX_CONFIG_FILE": str(root / "config.toml"),
                "P_SKILL_SKIP_STATUS_SYNC": "1",
                "CODEX_THREAD_ID": session,
            }
        )
        env.pop("P_SKILL_PROFILE", None)
        return env

    def run_ctl(self, args, env):
        return subprocess.run(
            [sys.executable, str(CTL_PATH), *args],
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=env,
        )

    def test_session_toggles_take_effect_without_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self.env(Path(tmp))
            work = self.run_ctl(["work"], env)
            self.assertEqual(work.returncode, 0, work.stderr)
            self.assertIn("p:w", work.stdout)
            blocked = self.run_ctl(["check", "auditing-a-repo-for-private-data"], env)
            self.assertEqual(blocked.returncode, 1)
            home = self.run_ctl(["home"], env)
            self.assertEqual(home.returncode, 0, home.stderr)
            self.assertIn("p:h", home.stdout)
            self.assertEqual(
                self.run_ctl(["check", "auditing-a-repo-for-private-data"], env).returncode,
                0,
            )

    def test_environment_lock_refuses_conflicting_toggle_without_state_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.env(root)
            env["P_SKILL_PROFILE"] = "work"
            result = self.run_ctl(["home"], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("locks this process", result.stderr)
            self.assertFalse((root / "sessions").exists())

    def test_status_json_has_no_local_paths_or_state_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.env(root)
            result = self.run_ctl(["status", "--json"], env)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value["profile"], "home")
            self.assertNotIn(str(root), result.stdout)
            self.assertNotIn("session-a", result.stdout)

    def test_global_profile_does_not_rewrite_codex_until_explicit_native_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.env(root)
            config = root / "config.toml"
            original = '[tui]\nstatus_line = ["model"]\n'
            config.write_text(original, encoding="utf-8")
            selected = self.run_ctl(["use", "work", "--global"], env)
            self.assertEqual(selected.returncode, 0, selected.stderr)
            self.assertEqual(config.read_text("utf-8"), original)
            synced = self.run_ctl(["sync-native"], env)
            self.assertEqual(synced.returncode, 0, synced.stderr)
            self.assertIn("p-skill-activation begin", config.read_text("utf-8"))
            self.assertEqual(self.run_ctl(["use", "home", "--global"], env).returncode, 0)
            self.assertIn("p-skill-activation begin", config.read_text("utf-8"))
            self.assertEqual(self.run_ctl(["sync-native"], env).returncode, 0)
            self.assertEqual(config.read_text("utf-8"), original)

    def test_validate_and_unknown_component_exit_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self.env(Path(tmp))
            valid = self.run_ctl(["validate"], env)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            unknown = self.run_ctl(["check", "unknown-component"], env)
            self.assertEqual(unknown.returncode, 2)

    def test_corrupt_policy_blocks_sensitive_work_but_not_core_or_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.env(root)
            session_path = root / "sessions"
            session_path.mkdir()
            digest_name = hashlib.sha256(b"session-a").hexdigest() + ".json"
            (session_path / digest_name).write_text("{broken", encoding="utf-8")
            sensitive = self.run_ctl(["check", "auditing-a-repo-for-private-data"], env)
            self.assertEqual(sensitive.returncode, 2)
            core = self.run_ctl(["check", "checking-branch-base-before-a-pr"], env)
            self.assertEqual(core.returncode, 0)
            repaired = self.run_ctl(["home"], env)
            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            self.assertIn("p:h", repaired.stdout)

    def test_launchers_resolve_controller_relatively(self):
        for profile in ("home", "work"):
            script = PLUGIN_ROOT / "skills" / profile / "scripts" / "toggle.py"
            text = script.read_text("utf-8")
            self.assertIn("Path(__file__).resolve().parents[3]", text)
            self.assertNotIn("CLAUDE_PLUGIN_ROOT", text)


class ActivationInstrumentationTests(unittest.TestCase):
    def test_every_skill_and_command_declares_its_gate(self):
        manifest = json.loads(MANIFEST_PATH.read_text("utf-8"))
        for component, details in manifest["components"].items():
            path = (
                PLUGIN_ROOT / "skills" / component / "SKILL.md"
                if details["source"] == "skill"
                else PLUGIN_ROOT / "commands" / (component + ".md")
            )
            text = path.read_text("utf-8")
            self.assertIn("skill-profile-ctl check " + component, text, str(path))

    def test_conditional_branches_declare_component_aware_capability_gate(self):
        for component in (
            "maintaining-the-format-plugin",
            "scouting-tools-for-open-frictions",
        ):
            text = (PLUGIN_ROOT / "skills" / component / "SKILL.md").read_text("utf-8")
            self.assertIn(
                "skill-profile-ctl check-capability local-session-history --component "
                + component,
                text,
            )

    def test_hook_wiring_has_no_activation_controller(self):
        hooks = (PLUGIN_ROOT / "hooks" / "hooks.json").read_text("utf-8")
        self.assertNotIn("skill-profile-ctl", hooks)


if __name__ == "__main__":
    unittest.main()
