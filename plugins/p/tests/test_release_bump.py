"""Unit tests for .github/scripts/release_bump.py."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "release_bump.py"


def _git_env():
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


class ReleaseBumpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Test Author")
        self.git("config", "user.email", "author" + "@" + "example" + ".test")
        self._write_initial_repo("1.2.3")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        proc = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            capture_output=True,
            text=True,
            env=_git_env(),
        )
        return proc.stdout.strip()

    def write(self, rel_path, content):
        path = self.repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return path

    def commit(self, message):
        self.git("add", "-A")
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-q", "-F", "-"],
            input=message,
            check=True,
            capture_output=True,
            text=True,
            env=_git_env(),
        )
        return self.git("rev-parse", "HEAD")

    def run_bump(self, *args):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--repo", str(self.repo), *args],
            capture_output=True,
            text=True,
            env=_git_env(),
        )

    def _write_manifests(self, version):
        self.write(
            ".claude-plugin/marketplace.json",
            '{\n'
            '  "name": "polstools",\n'
            '  "plugins": [\n'
            '    {\n'
            '      "name": "p",\n'
            '      "source": "./plugins/p",\n'
            f'      "version": "{version}",\n'
            '      "keywords": ["skills", "profiles", "goals"]\n'
            '    }\n'
            '  ]\n'
            '}\n',
        )
        self.write(
            ".agents/plugins/marketplace.json",
            '{\n'
            '  "name": "polstools",\n'
            '  "plugins": [{"name": "p", "source": {"source": "local", "path": "./plugins/p"}}]\n'
            '}\n',
        )
        for rel in (
            "plugins/p/.claude-plugin/plugin.json",
            "plugins/p/.codex-plugin/plugin.json",
            "plugins/p/plugin.json",
        ):
            self.write(rel, f'{{\n  "name": "p",\n  "version": "{version}"\n}}\n')

    def _write_initial_repo(self, version):
        self._write_manifests(version)
        self.write(
            "CHANGELOG.md",
            "# Changelog\n\n"
            f"## [{version}]\n\n"
            "- Initial fixture release.\n",
        )
        self.write("plugins/p/bin/tool.py", "print('v1')\n")
        self.write("plugins/p/tests/test_tool.py", "pass\n")
        self.write("README.md", "# fixture\n")
        self.base_sha = self.commit(f"chore(release): initial {version}")

    def test_auto_base_discovers_last_version_change_and_bumps_patch(self):
        self.write("README.md", "# updated\n")
        self.commit("docs: update readme")
        self.write("plugins/p/bin/tool.py", "print('v2')\n")
        self.commit("fix(tool): tighten output (p 1.2.3) (#13)")

        proc = self.run_bump("--base", "auto", "--head", "HEAD", "--bump", "auto", "--dry-run", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(data["bumped"])
        self.assertEqual(data["base_sha"], self.base_sha)
        self.assertEqual(data["current_version"], "1.2.3")
        self.assertEqual(data["new_version"], "1.2.4")
        self.assertEqual(data["bump"], "patch")

    def test_manifest_mismatch_exits_2(self):
        self.write("plugins/p/plugin.json", '{\n  "name": "p",\n  "version": "1.2.9"\n}\n')
        proc = self.run_bump("--dry-run")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("mismatch", proc.stderr)

    def test_skip_guards_filter_release_and_skip_commits(self):
        self.write("plugins/p/bin/tool.py", "print('skip')\n")
        self.commit("ci: infra setup [skip release]")
        proc = self.run_bump("--dry-run", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertFalse(data["bumped"])
        self.assertEqual(data["bump"], "none")

        # Explicit --bump bypasses skip guard
        forced = self.run_bump("--bump", "minor", "--dry-run", "--json")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        forced_data = json.loads(forced.stdout)
        self.assertTrue(forced_data["bumped"])
        self.assertEqual(forced_data["new_version"], "1.3.0")

    def test_skip_ci_commit_is_also_skipped_under_auto(self):
        self.write("plugins/p/bin/tool.py", "print('skip-ci')\n")
        self.commit("chore: maintenance [skip ci]")
        proc = self.run_bump("--dry-run", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(json.loads(proc.stdout)["bumped"])

    def test_empty_plugin_diff_produces_no_bump(self):
        self.write("README.md", "# docs only\n")
        self.commit("feat: add readme section")
        proc = self.run_bump("--dry-run", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertFalse(data["bumped"])

    def test_semver_classification_matrix_and_tests_only_rule(self):
        # docs/test/ci/style touching only plugins/p/tests/ -> none
        self.write("plugins/p/tests/test_tool.py", "assert True\n")
        self.commit("test: strengthen fixture test")
        proc = self.run_bump("--dry-run", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(json.loads(proc.stdout)["bumped"])

        # docs touching non-test file under plugins/p/ -> patch floor
        self.write("plugins/p/EVALUATION.md", "# eval\n")
        self.commit("docs: document eval")
        proc = self.run_bump("--dry-run", "--json")
        self.assertEqual(json.loads(proc.stdout)["bump"], "patch")

        # feat -> minor overrides patch
        self.write("plugins/p/bin/tool.py", "print('feat')\n")
        self.commit("feat(tool): add capability")
        proc = self.run_bump("--dry-run", "--json")
        data = json.loads(proc.stdout)
        self.assertEqual(data["bump"], "minor")
        self.assertEqual(data["new_version"], "1.3.0")

        # breaking change -> major overrides minor
        self.write("plugins/p/bin/tool.py", "print('major')\n")
        self.commit("refactor(tool)!: drop legacy option")
        proc = self.run_bump("--dry-run", "--json")
        data = json.loads(proc.stdout)
        self.assertEqual(data["bump"], "major")
        self.assertEqual(data["new_version"], "2.0.0")

    def test_breaking_change_footer_triggers_major(self):
        self.write("plugins/p/bin/tool.py", "print('v3')\n")
        self.commit("feat(tool): redesign cli\n\nBREAKING CHANGE: flag removed")
        proc = self.run_bump("--dry-run", "--json")
        data = json.loads(proc.stdout)
        self.assertEqual(data["bump"], "major")
        self.assertEqual(data["new_version"], "2.0.0")

    def test_apply_preserves_single_line_keywords_updates_changelog_and_github_output(self):
        agents_before = (self.repo / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
        self.write("plugins/p/bin/tool.py", "print('fix')\n")
        self.commit("fix(tool): repair windows handling (p 1.2.3) (#13)")

        gh_out = Path(self.tmp.name) / "github_output.txt"
        proc = self.run_bump("--apply", "--json", "--github-output", str(gh_out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(data["bumped"])
        self.assertEqual(data["new_version"], "1.2.4")

        # Verify all 4 manifests updated & single-line keywords preserved
        marketplace_text = (self.repo / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
        self.assertIn('"keywords": ["skills", "profiles", "goals"]', marketplace_text)
        self.assertEqual(json.loads(marketplace_text)["plugins"][0]["version"], "1.2.4")
        for rel in (
            "plugins/p/.claude-plugin/plugin.json",
            "plugins/p/.codex-plugin/plugin.json",
            "plugins/p/plugin.json",
        ):
            self.assertEqual(json.loads((self.repo / rel).read_text(encoding="utf-8"))["version"], "1.2.4")

        # .agents/plugins/marketplace.json untouched
        self.assertEqual(
            (self.repo / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"),
            agents_before,
        )

        # CHANGELOG.md prepended cleanly with trailing (p 1.2.3) stripped
        changelog = (self.repo / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [1.2.4]\n\n- fix(tool): repair windows handling (#13)\n\n## [1.2.3]", changelog)
        self.assertNotIn("(p 1.2.3)", changelog)

        # GitHub output populated
        gh_lines = dict(line.split("=", 1) for line in gh_out.read_text(encoding="utf-8").splitlines())
        self.assertEqual(gh_lines["bumped"], "true")
        self.assertEqual(gh_lines["current_version"], "1.2.3")
        self.assertEqual(gh_lines["new_version"], "1.2.4")
        self.assertEqual(gh_lines["bump"], "patch")

    def test_duplicate_changelog_heading_exits_2(self):
        self.write(
            "CHANGELOG.md",
            "# Changelog\n\n## [1.2.4]\n\n- Already here.\n\n## [1.2.3]\n\n- Initial.\n",
        )
        self.write("plugins/p/bin/tool.py", "print('dup')\n")
        self.commit("fix(tool): duplicate heading check")
        proc = self.run_bump("--apply")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("already contains heading", proc.stderr)


if __name__ == "__main__":
    unittest.main()
