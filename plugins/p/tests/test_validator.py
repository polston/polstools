import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout

from home_env import home_vars


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = PLUGIN_ROOT / "bin" / "p_validate.py"


def load_validator():
    spec = importlib.util.spec_from_file_location("p_validate_strict", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root, *args):
    return subprocess.run(
        ["git", "-c", "user.name=fixture", "-c", "user.email=fixture",
         "-c", "commit.gpgsign=false", "-c", "core.excludesFile=", "-C", str(root), *args],
        check=True, text=True, capture_output=True,
    ).stdout


class MutatedPackageTests(unittest.TestCase):
    """Each test mutates a private copy of the package and expects one finding."""

    @classmethod
    def setUpClass(cls):
        cls.validator = load_validator()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.plugin = Path(self._tmp.name) / "p"
        shutil.copytree(
            PLUGIN_ROOT, self.plugin, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _errors(self):
        return self.validator.validate_package(self.plugin)

    def _set_frontmatter(self, skill, lines):
        path = self.plugin / "skills" / skill / "SKILL.md"
        body = path.read_text(encoding="utf-8").split("\n---\n", 1)[1]
        path.write_text("---\n" + lines + "\n---\n" + body, encoding="utf-8")

    def test_unmodified_copy_is_clean(self):
        self.assertEqual([], self._errors())

    def test_frontmatter_that_yaml_would_reject_or_misread_is_flagged(self):
        cases = {
            "unclosed flow value": "name: doctor\ndescription: x: [unclosed {",
            "duplicate key": "name: doctor\ndescription: a\ndescription: b",
            "missing space after colon": "name:doctor\ndescription: a",
            "continuation line": "name: doctor\ndescription: a\n  more",
        }
        for label, lines in cases.items():
            with self.subTest(label):
                self._set_frontmatter("doctor", lines)
                errors = self._errors()
                self.assertEqual(1, len(errors), errors)
                self.assertTrue(errors[0].startswith("skill doctor "), errors)

    def test_quoted_frontmatter_values_are_accepted(self):
        self._set_frontmatter("doctor", "name: 'doctor'\ndescription: \"Use when: a hook fails\"")
        self.assertEqual([], self._errors())

    def test_windows_line_endings_in_a_skill_are_not_a_finding(self):
        path = self.plugin / "skills" / "doctor" / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual([], self._errors())

    def test_reference_to_a_missing_plugin_script_is_flagged(self):
        path = self.plugin / "skills" / "doctor" / "SKILL.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "\nRun `<plugin-root>/bin/absent-tool`.\n",
            encoding="utf-8",
        )
        self.assertEqual(
            ["skills/doctor/SKILL.md references missing plugin file bin/absent-tool"],
            self._errors(),
        )

    def test_bare_skill_relative_references_are_checked_against_the_skill(self):
        (self.plugin / "skills" / "fmt-on" / "scripts" / "toggle.py").unlink()
        self.assertEqual(
            ["skills/fmt-on/SKILL.md references missing plugin file scripts/toggle.py"],
            self._errors(),
        )

    def test_deleting_a_script_every_skill_calls_is_flagged(self):
        (self.plugin / "bin" / "skill-profile-ctl").unlink()
        errors = self._errors()
        self.assertTrue(errors)
        self.assertTrue(all(error.endswith("bin/skill-profile-ctl") for error in errors), errors)

    def test_hook_command_pointing_at_a_missing_script_is_flagged(self):
        path = self.plugin / "hooks" / "hooks.json"
        text = path.read_text(encoding="utf-8")
        entry = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/(bin/[A-Za-z0-9_.-]+)", text).group(1)
        path.write_text(text.replace(entry, entry + "-absent"), encoding="utf-8")
        errors = self._errors()
        self.assertEqual(2, len(errors), errors)
        self.assertTrue(all(error.endswith(entry + "-absent") for error in errors))

    def test_non_object_hook_events_are_a_finding_not_a_crash(self):
        (self.plugin / "hooks" / "hooks.json").write_text('{"hooks": null}', encoding="utf-8")
        errors = self._errors()
        self.assertEqual(1, len(errors), errors)

    def test_a_second_hook_contract_is_validated_independently(self):
        contract = {
            "label": "fixture harness hook manifest",
            "path": ("fixture-hooks.json",),
            "events": frozenset({"Stop"}),
        }
        original = self.validator.HOOK_CONTRACTS
        self.validator.HOOK_CONTRACTS = original + (contract,)
        try:
            self.assertEqual(["fixture harness hook manifest is missing or malformed"],
                             self._errors())
            (self.plugin / "fixture-hooks.json").write_text(json.dumps({"hooks": {"Stop": [
                {"hooks": [{"type": "command",
                            "command": "sh \"${CLAUDE_PLUGIN_ROOT}/bin/python-launcher\""}]}
            ]}}), encoding="utf-8")
            self.assertEqual([], self._errors())
        finally:
            self.validator.HOOK_CONTRACTS = original

    def test_keywords_must_agree_across_all_three_manifests(self):
        path = self.plugin / "plugin.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["keywords"] = manifest["keywords"][:-1]
        path.write_text(json.dumps(manifest), encoding="utf-8")
        errors = self._errors()
        self.assertEqual(1, len(errors), errors)
        self.assertIn("keywords", errors[0])


class GitHygieneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_validator()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        git(self.root, "init", "-q")
        manifest = self.root / "plugins" / "p" / ".claude-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
        (self.root / "plugins" / "p" / "tool").write_text("#!/bin/sh\nexit 0\n", "utf-8")
        (self.root / "plugins" / "p" / "notes.md").write_text("notes\n", "utf-8")
        (self.root / ".gitignore").write_text("/ignored-stray\n", "utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "fixture")

    def tearDown(self):
        self._tmp.cleanup()

    def _set_index_mode(self, path, mode):
        blob = git(self.root, "ls-files", "-s", path).split()[1]
        git(self.root, "update-index", "--cacheinfo", "%s,%s,%s" % (mode, blob, path))

    def _mode_errors(self):
        errors = []
        tracked = self.validator._tracked_entries(self.root)
        self.validator._validate_file_modes(self.root, tracked, errors)
        return errors

    def test_index_mode_must_follow_the_shebang(self):
        self.assertEqual(
            ["plugins/p/tool has a shebang but is not executable in the index"],
            self._mode_errors(),
        )
        self._set_index_mode("plugins/p/tool", "100755")
        self._set_index_mode("plugins/p/notes.md", "100755")
        self.assertEqual(
            ["plugins/p/notes.md is executable in the index but has no shebang"],
            self._mode_errors(),
        )

    def test_root_hygiene_flags_only_unexpected_root_files(self):
        (self.root / "ignored-stray").write_text("", "utf-8")
        (self.root / "empty-dir").mkdir()
        (self.root / "plugins" / "p" / "untracked-inside.md").write_text("x", "utf-8")
        errors = []
        self.validator._validate_root_hygiene(self.root, errors)
        self.assertEqual([], errors)
        (self.root / "stray").write_text("", "utf-8")
        self.validator._validate_root_hygiene(self.root, errors)
        self.assertEqual(["untracked, unignored file at the repository root: stray"], errors)

    def test_plugin_change_without_version_bump_is_flagged(self):
        base = git(self.root, "rev-parse", "HEAD").strip()
        self.assertEqual([], self.validator.validate_version_bump(self.root, base))
        (self.root / "plugins" / "p" / "notes.md").write_text("changed\n", "utf-8")
        errors = self.validator.validate_version_bump(self.root, base)
        self.assertEqual(1, len(errors), errors)
        self.assertIn("still 1.0.0", errors[0])
        manifest = self.root / "plugins" / "p" / ".claude-plugin" / "plugin.json"
        manifest.write_text(json.dumps({"version": "1.0.1"}), encoding="utf-8")
        self.assertEqual([], self.validator.validate_version_bump(self.root, base))
        manifest.write_text(json.dumps({"version": "0.9.0"}), encoding="utf-8")
        errors = self.validator.validate_version_bump(self.root, base)
        self.assertEqual(1, len(errors), errors)
        self.assertIn("lower than 1.0.0", errors[0])

    def test_outside_a_checkout_git_checks_are_not_applicable(self):
        with tempfile.TemporaryDirectory() as plain:
            self.assertIsNone(self.validator._tracked_entries(Path(plain)))


class EntrypointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_validator()

    def test_help_exits_without_validating(self):
        called = []
        original = self.validator.validate_repository
        self.validator.validate_repository = lambda *a, **k: called.append(1) or []
        try:
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as raised:
                self.validator.main(["--help"])
        finally:
            self.validator.validate_repository = original
        self.assertEqual(0, raised.exception.code)
        self.assertEqual([], called)

    def _stub_checks(self):
        ran = []
        for name in ("validate_repository", "smoke_relocated_copy", "antigravity_validate"):
            patcher = mock.patch.object(
                self.validator, name, lambda *a, _n=name, **k: ran.append(_n) or [])
            patcher.start()
            self.addCleanup(patcher.stop)
        return ran

    def _run_main(self, *argv):
        stderr = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            code = self.validator.main(list(argv))
        return code, stderr.getvalue()

    def test_unknown_base_revision_exits_2_and_says_why_before_any_check(self):
        ran = self._stub_checks()
        code, err = self._run_main("--base", "no-such-revision-for-p-validate")
        self.assertEqual(2, code)
        self.assertIn("base revision no-such-revision-for-p-validate", err)
        self.assertEqual([], ran)

    def test_base_that_exists_only_as_a_remote_branch_says_so(self):
        self._stub_checks()
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp) / "clone"
            clone.mkdir()
            env = {**home_vars(str(Path(tmp))), "PATH": os.environ.get("PATH", ""),
                   "GIT_CONFIG_NOSYSTEM": "1"}
            for args in (
                ["init", "-q", "-b", "trunk"],
                ["-c", "user.name=t", "-c", "user.email=" + "t" + chr(64) + "example.invalid",
                 "commit", "-q", "--allow-empty", "-m", "base"],
                ["update-ref", "refs/remotes/origin/main", "HEAD"],
                ["checkout", "-q", "--detach"],
                ["branch", "-q", "-D", "trunk"],
            ):
                subprocess.run(["git", "-C", str(clone), *args], env=env,
                               capture_output=True, check=True)
            with mock.patch.object(self.validator, "REPO_ROOT", clone):
                code, err = self._run_main("--base", "main")
        self.assertEqual(2, code)
        self.assertIn("base revision main is not a commit", err)
        self.assertNotIn("RuntimeError", err)

    def test_unexpected_exception_exits_2_not_1(self):
        def explode(*args, **kwargs):
            raise TypeError("fixture")

        original = self.validator.validate_repository
        self.validator.validate_repository = explode
        try:
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                code = self.validator.main([])
        finally:
            self.validator.validate_repository = original
        self.assertEqual(2, code)
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
