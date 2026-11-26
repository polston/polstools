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
            {
                "auditing-agent-contracts",
                "investigating-and-improving",
                "reviewing-improvement-effects",
                "scouting-tools-for-open-frictions",
            },
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

    def test_nested_harness_sessions_fail_closed_until_one_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.env(root, session="inner-codex")
            env["CLAUDE_CODE_SESSION_ID"] = "outer-claude"
            with self.assertRaises(self.activation.PolicyError):
                self.activation.set_profile(self.manifest, "work", "session", env=env)
            self.assertFalse((root / "sessions").exists())
            with self.assertRaises(self.activation.PolicyError):
                self.activation.resolve(self.manifest, env=env)
            env["P_SKILL_HARNESS"] = "codex"
            self.activation.set_profile(self.manifest, "work", "session", env=env)
            self.assertEqual(self.activation.resolve(self.manifest, env=env)["profile"], "work")
            outer = dict(env, P_SKILL_HARNESS="claude")
            self.assertEqual(self.activation.resolve(self.manifest, env=outer)["profile"], "home")

    def test_the_harness_override_accepts_agy_for_antigravity(self):
        env = {"CLAUDE_CODE_SESSION_ID": "outer-claude",
               "ANTIGRAVITY_CONVERSATION_ID": "inner-agy"}
        with self.assertRaises(self.activation.PolicyError):
            self.activation.session_id_from_env(env)
        for name in ("agy", "antigravity"):
            self.assertEqual(self.activation.session_id_from_env(
                dict(env, P_SKILL_HARNESS=name)), "inner-agy")

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
                    resolved, "local-session-history", "auditing-agent-contracts"
                )
            )
            self.activation.set_override(
                self.manifest, "auditing-agent-contracts", True, "session", env=env
            )
            resolved = self.activation.resolve(self.manifest, env=env)
            self.assertTrue(
                self.activation.capability_enabled(
                    resolved, "local-session-history", "auditing-agent-contracts"
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


class NativeSyncFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.activation = load_activation()
        cls.manifest = cls.activation.load_manifest(MANIFEST_PATH)

    def sync(self, root, config, profile):
        env = {
            "P_SKILL_CONFIG_FILE": str(root / "global.json"),
            "P_SKILL_STATE_DIR": str(root / "sessions"),
            "P_CODEX_CONFIG_FILE": str(config),
        }
        self.activation.set_profile(self.manifest, profile, "global", env=env)
        return self.activation.sync_native(self.manifest, PLUGIN_ROOT, env=env)

    def test_sync_writes_through_a_symlinked_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dotfiles").mkdir()
            real = root / "dotfiles" / "config.toml"
            real.write_text('model = "example"\n', encoding="utf-8")
            link = root / "config.toml"
            try:
                link.symlink_to(real)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            self.assertTrue(self.sync(root, link, "work"))
            self.assertTrue(link.is_symlink())
            self.assertIn(self.activation.NATIVE_BEGIN, real.read_text("utf-8"))

    def test_sync_round_trip_preserves_crlf_and_bom_bytes(self):
        for original in (
            b'[tui]\r\nstatus_line = ["model"]\r\n',
            b'\xef\xbb\xbf[tui]\nstatus_line = ["model"]\n',
        ):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config = root / "config.toml"
                config.write_bytes(original)
                self.assertTrue(self.sync(root, config, "work"))
                synced = config.read_bytes()
                self.assertTrue(synced.startswith(original))
                if b"\r\n" in original:
                    self.assertNotIn(b"\n", synced.replace(b"\r\n", b""))
                self.assertTrue(self.sync(root, config, "home"))
                self.assertEqual(config.read_bytes(), original)


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
            self.assertNotIn("sync-native", selected.stdout)
            home = self.run_ctl(["use", "home", "--global"], env)
            self.assertEqual(home.returncode, 0)
            self.assertIn("sync-native", home.stdout)
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


class ControllerExitContractTests(unittest.TestCase):
    """Runs a copy of the controller in a scratch plugin layout so its
    sibling statusline-ctl and its source tree can be substituted."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.plugin = self.root / "plugin"
        for relative in ("bin/skill-profile-ctl", "lib/skill_activation.py",
                         "profiles/skill-activation-v1.json"):
            target = self.plugin / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((PLUGIN_ROOT / relative).read_bytes())
        manifest = json.loads(MANIFEST_PATH.read_text("utf-8"))
        for component, details in manifest["components"].items():
            if details["source"] == "skill":
                path = self.plugin / "skills" / component / "SKILL.md"
            else:
                path = self.plugin / "commands" / (component + ".md")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x\n", encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
                                 "P_SKILL_PROFILE", "P_SKILL_SKIP_STATUS_SYNC")}
        self.env.update({
            "P_SKILL_CONFIG_FILE": str(self.root / "global.json"),
            "P_SKILL_STATE_DIR": str(self.root / "sessions"),
            "P_CODEX_CONFIG_FILE": str(self.root / "config.toml"),
            "CODEX_THREAD_ID": "session-a",
        })

    def tearDown(self):
        self.tmp.cleanup()

    def run_ctl(self, *args, **extra):
        return subprocess.run(
            [sys.executable, str(self.plugin / "bin" / "skill-profile-ctl"), *args],
            text=True, encoding="utf-8", capture_output=True,
            env=dict(self.env, P_SKILL_SKIP_STATUS_SYNC="1", **extra))

    def test_check_names_the_ambiguous_session_for_a_core_component(self):
        result = self.run_ctl("check", "home", CLAUDE_CODE_SESSION_ID="outer-claude")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("P_SKILL_HARNESS", result.stderr)
        self.assertNotIn("policy is invalid", result.stderr)
        result = self.run_ctl("check-capability", "core",
                              CLAUDE_CODE_SESSION_ID="outer-claude")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("P_SKILL_HARNESS", result.stderr)
        self.assertNotIn("policy is invalid", result.stderr)

    def test_validate_flags_a_coverage_mismatch_with_exit_1(self):
        self.assertEqual(self.run_ctl("validate").returncode, 0)
        (self.plugin / "skills" / "unclassified-skill").mkdir()
        (self.plugin / "skills" / "unclassified-skill" / "SKILL.md").write_text("x\n")
        self.assertEqual(self.run_ctl("validate").returncode, 1)
        (self.plugin / "profiles" / "skill-activation-v1.json").write_text("{broken")
        self.assertEqual(self.run_ctl("validate").returncode, 2)

    def test_lock_refuses_session_overrides_and_flags_global_writes(self):
        locked = self.run_ctl("disable", "auditing-a-repo-for-private-data",
                              "--session", P_SKILL_PROFILE="home")
        self.assertEqual(locked.returncode, 2)
        self.assertFalse((self.root / "sessions").exists())
        for args in (("use", "work", "--global"),
                     ("disable", "auditing-a-repo-for-private-data", "--global")):
            with self.subTest(args=args):
                result = self.run_ctl(*args, P_SKILL_PROFILE="home")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("P_SKILL_PROFILE", result.stdout + result.stderr)
        self.assertEqual(json.loads((self.root / "global.json").read_text("utf-8"))["profile"],
                         "work")

    def test_no_renderer_to_update_is_not_a_warning(self):
        status = self.plugin / "bin" / "statusline-ctl"
        for code, warned in ((0, False), (1, False), (2, True)):
            with self.subTest(code=code):
                status.write_text("import sys\nprint('detail')\nsys.exit(%d)\n" % code)
                env = {k: v for k, v in self.env.items() if k != "P_SKILL_SKIP_STATUS_SYNC"}
                result = subprocess.run(
                    [sys.executable, str(self.plugin / "bin" / "skill-profile-ctl"), "home"],
                    text=True, encoding="utf-8", capture_output=True, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(bool(result.stderr.strip()), warned, result.stderr)


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
            "auditing-agent-contracts",
            "scouting-tools-for-open-frictions",
        ):
            text = (PLUGIN_ROOT / "skills" / component / "SKILL.md").read_text("utf-8")
            self.assertIn(
                "skill-profile-ctl check-capability local-session-history --component "
                + component,
                text,
            )

    def test_plugin_installs_no_session_hooks(self):
        self.assertFalse((PLUGIN_ROOT / "hooks" / "hooks.json").exists())
        self.assertFalse((PLUGIN_ROOT / "hooks.json").exists())


class MultiUserStateIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.activation = load_activation()

    def test_state_dir_respects_explicit_override(self):
        env = {"P_SKILL_STATE_DIR": "/custom/path"}
        self.assertEqual(self.activation._state_dir(env), Path("/custom/path"))

    def test_state_dir_prefers_xdg_runtime_dir(self):
        env = {"XDG_RUNTIME_DIR": "/run/user/1000"}
        self.assertEqual(
            self.activation._state_dir(env),
            Path("/run/user/1000/p-skill-activation"),
        )

    def test_state_dir_is_distinct_per_user(self):
        def directory_for(user):
            if hasattr(os, "getuid"):
                with mock.patch.object(self.activation.os, "getuid", return_value=user):
                    return self.activation._state_dir({})
            return self.activation._state_dir({"USERNAME": "user%d" % user})

        first, second = directory_for(1001), directory_for(1002)
        self.assertNotEqual(first, second)
        self.assertEqual(first, directory_for(1001))
        self.assertEqual(first.parent, second.parent)

    def env(self, root, session="session-a"):
        return {
            "P_SKILL_CONFIG_FILE": str(root / "global.json"),
            "P_SKILL_STATE_DIR": str(root / "sessions"),
            "CODEX_THREAD_ID": session,
        }

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_session_writes_make_the_state_directory_private(self):
        manifest = self.activation.load_manifest(MANIFEST_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sessions").mkdir(mode=0o755)
            os.chmod(root / "sessions", 0o755)
            self.activation.set_profile(manifest, "work", "session", env=self.env(root))
            self.assertEqual((root / "sessions").stat().st_mode & 0o777, 0o700)

    def test_symlinked_state_directory_fails_closed(self):
        manifest = self.activation.load_manifest(MANIFEST_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "elsewhere").mkdir()
            try:
                (root / "sessions").symlink_to(root / "elsewhere", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            env = self.env(root)
            with self.assertRaises(self.activation.PolicyError):
                self.activation.set_profile(manifest, "work", "session", env=env)
            with self.assertRaises(self.activation.PolicyError):
                self.activation.resolve(manifest, env=env)
            self.assertEqual(list((root / "elsewhere").iterdir()), [])

    def test_a_session_that_keeps_resolving_survives_pruning(self):
        manifest = self.activation.load_manifest(MANIFEST_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.env(root, "long-lived")
            self.activation.set_profile(manifest, "work", "session", env=env)
            (state,) = list((root / "sessions").iterdir())
            old = time.time() - 15 * 24 * 3600
            os.utime(state, (old, old))
            self.assertEqual(self.activation.resolve(manifest, env=env)["profile"], "work")
            self.activation.set_profile(manifest, "home", "session", env=self.env(root, "other"))
            self.assertEqual(self.activation.resolve(manifest, env=env)["profile"], "work")

class ReadOnlyActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.activation = load_activation()
        cls.manifest = cls.activation.load_manifest(MANIFEST_PATH)

    def state(self, root, overrides=None):
        suffix = str(os.getuid()) if hasattr(os, "getuid") else os.environ.get("USERNAME", "default")
        directory = root / ("p-skill-activation-" + suffix)
        directory.mkdir(mode=0o700)
        path = directory / (hashlib.sha256(b"read-only-session").hexdigest() + ".json")
        self.activation.write_state(path, "work", overrides or {})
        return path

    def env(self, root):
        return {"CODEX_THREAD_ID": "read-only-session",
                "USERNAME": os.environ.get("USERNAME", "default"),
                "P_SKILL_CONFIG_FILE": str(root / "global.json")}

    def test_platform_candidates_have_no_filesystem_dependency(self):
        candidates = self.activation._temporary_root_candidates
        env = {"TMPDIR": "first", "TEMP": "second", "TMP": "third"}
        self.assertEqual(candidates(env, "posix", "/fixture/home", "relative"),
                         ["first", "second", "third", "/tmp", "/var/tmp", "/usr/tmp", "relative"])
        windows = candidates(dict(env, SYSTEMROOT=r"C:\Windows"), "nt", r"C:\Profiles\fixture", r"C:\work")
        self.assertEqual(windows, ["first", "second", "third",
                                  r"C:\Profiles\fixture\AppData\Local\Temp",
                                  r"C:\Windows\Temp", r"c:\temp", r"c:\tmp",
                                  r"\temp", r"\tmp", r"C:\work"])

    def test_fallback_policy_and_root_alias_are_preserved_without_temp_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "first", root / "second"
            first.mkdir()
            second.mkdir()
            self.state(second, {"writing-plans": False})
            with mock.patch.object(self.activation, "_temporary_root_candidates",
                                   return_value=[str(first), str(second), str(second / ".")]), \
                    mock.patch.object(self.activation.tempfile, "tempdir", None), \
                    mock.patch.object(self.activation.tempfile, "gettempdir", side_effect=AssertionError("write probe")):
                resolved = self.activation.resolve(self.manifest, env=self.env(root))
            self.assertEqual(resolved["source"], "session")
            self.assertEqual(self.activation.component_state(resolved, "writing-plans"), "disabled")

    def test_multiple_roots_and_access_denial_do_not_inherit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "first", root / "second"
            first.mkdir()
            second.mkdir()
            self.state(first)
            self.state(second)
            with mock.patch.object(self.activation, "_temporary_root_candidates",
                                   return_value=[str(first), str(second)]), \
                    mock.patch.object(self.activation.tempfile, "tempdir", None):
                with self.assertRaisesRegex(self.activation.StateLocationError, "multiple"):
                    self.activation.resolve(self.manifest, env=self.env(root))
                with mock.patch.object(self.activation, "_private_dir", side_effect=PermissionError("denied")):
                    with self.assertRaises(self.activation.StateLocationError):
                        self.activation.resolve(self.manifest, env=self.env(root))

    def test_file_temp_root_is_skipped_but_file_activation_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "first", root / "second"
            first.write_text("not a directory")
            second.mkdir()
            policy = self.state(second, {"writing-plans": False})
            env = dict(self.env(root), TMPDIR=str(first), TEMP=str(second), TMP=str(second))
            with mock.patch.object(self.activation.tempfile, "tempdir", None):
                for candidate in (first, first / "nested"):
                    with self.subTest(candidate=candidate):
                        fallback_env = dict(env, TMPDIR=str(candidate))
                        resolved = self.activation.resolve(self.manifest, env=fallback_env)
                        self.assertEqual(resolved["source"], "session")
                        self.assertEqual(self.activation.component_state(resolved, "writing-plans"), "disabled")
                        # The existing writer must select the same fallback.
                        with mock.patch.dict(os.environ, fallback_env):
                            self.assertEqual(self.activation.session_state_path(
                                "read-only-session", fallback_env), policy)
                        self.activation.tempfile.tempdir = None
                first.unlink()
                first.mkdir()
                (first / policy.parent.name).write_text("invalid activation directory")
                with self.assertRaises(self.activation.StateLocationError):
                    self.activation.resolve(self.manifest, env=env)
                # Explicit roots retain configuration-error behavior even
                # when their ancestor is a file rather than a directory.
                with self.assertRaises(self.activation.StateLocationError):
                    self.activation.resolve(self.manifest, env=dict(
                        env, P_SKILL_STATE_DIR=str(first / policy.parent.name / "nested")))

    def test_symlinked_temp_root_alias_is_one_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            target.mkdir()
            self.state(target)
            alias = root / "alias"
            try:
                alias.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks unavailable")
            with mock.patch.object(self.activation, "_temporary_root_candidates",
                                   return_value=[str(target), str(alias)]), \
                    mock.patch.object(self.activation.tempfile, "tempdir", None):
                self.assertEqual(self.activation.resolve(self.manifest, env=self.env(root))["source"], "session")

    def test_explicit_and_xdg_reads_never_probe_temp_and_unreadable_state_is_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.state(root)
            env = dict(self.env(root), P_SKILL_STATE_DIR=str(path.parent))
            with mock.patch.object(self.activation.tempfile, "gettempdir", side_effect=AssertionError("write probe")):
                self.assertEqual(self.activation.resolve(self.manifest, env=env)["profile"], "work")
                with mock.patch.object(Path, "open", side_effect=PermissionError("denied")):
                    with self.assertRaises(self.activation.StateLocationError):
                        self.activation.resolve(self.manifest, env=env)
                runtime = root / "runtime"
                runtime.mkdir()
                path.parent.rename(runtime / "p-skill-activation")
                xdg_env = dict(self.env(root), XDG_RUNTIME_DIR=str(runtime))
                self.assertEqual(self.activation.resolve(self.manifest, env=xdg_env)["profile"], "work")

    def test_absence_inherits_global_without_creating_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.env(root)
            self.activation.write_state(root / "global.json", "work", {})
            with mock.patch.object(self.activation, "_temporary_root_candidates", return_value=[str(root)]), \
                    mock.patch.object(self.activation.tempfile, "tempdir", None), \
                    mock.patch.object(self.activation.tempfile, "gettempdir", side_effect=AssertionError("write probe")):
                self.assertEqual(self.activation.resolve(self.manifest, env=env)["source"], "global")
                (root / "global.json").unlink()
                self.assertEqual(self.activation.resolve(self.manifest, env=env)["source"], "default")
            self.assertEqual(list(root.iterdir()), [])

    def test_fresh_controller_denies_writes_and_preserves_core_disable(self):
        # The child has a cold tempfile cache. An audit hook rejects even an
        # attempted temp write; permissions alone do not suffice when run as root.
        script = '''
import os, runpy, sys
def deny_writes(event, args):
    if event == "open":
        if args[0] == os.environ.get("P_ACTIVATION_TEST_DENY_READ"):
            raise PermissionError("test denies policy access")
        mode, flags = args[1], args[2]
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
            raise PermissionError("test denies writes")
    if event in {"os.mkdir", "os.remove", "os.rename", "os.chmod", "os.utime"}:
        raise PermissionError("test denies mutations")
sys.addaudithook(deny_writes)
controller = sys.argv[1]
sys.argv = [controller] + sys.argv[2:]
runpy.run_path(controller, run_name="__main__")
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "first", root / "second"
            first.mkdir()
            second.mkdir()
            policy = self.state(first, {"writing-plans": False})
            # Also exercise the best-effort refresh under denied utime.
            os.utime(policy, (1, 1))
            env = {key: value for key, value in os.environ.items()
                   if key not in {"P_SKILL_STATE_DIR", "P_SKILL_PROFILE", "XDG_RUNTIME_DIR", "P_SKILL_HARNESS"}
                   and key not in self.activation.SESSION_ENV_VARS}
            env.update(self.env(root), TMPDIR=str(first), TEMP=str(second), TMP=str(second))
            def check(*args):
                return subprocess.run([sys.executable, "-B", "-c", script, str(CTL_PATH), *args],
                                      env=env, text=True, capture_output=True)
            disabled = check("check", "writing-plans")
            self.assertEqual(disabled.returncode, 1, disabled.stderr)
            self.activation.write_state(policy, "work", {"writing-plans": True})
            enabled = check("check", "writing-plans")
            self.assertEqual(enabled.returncode, 0, enabled.stderr)
            self.state(second)
            for args in (("check", "writing-plans"), ("check-capability", "core")):
                ambiguous = check(*args)
                self.assertEqual(ambiguous.returncode, 2, ambiguous.stderr)
                self.assertIn("multiple", ambiguous.stderr)
            # Pin the known root to isolate unreadability from ambiguity.
            env["P_SKILL_STATE_DIR"] = str(policy.parent)
            env["P_ACTIVATION_TEST_DENY_READ"] = str(policy)
            for args in (("check", "writing-plans"), ("check-capability", "core")):
                unreadable = check(*args)
                self.assertEqual(unreadable.returncode, 2, unreadable.stderr)
                self.assertIn("unreadable", unreadable.stderr)


if __name__ == "__main__":
    unittest.main()
