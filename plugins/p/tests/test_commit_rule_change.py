import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "commit-rule-change"


def git_env():
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


class CommitRuleChangeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name) / "rules"
        self.git("init", "-q", str(self.repo), cwd=self.tempdir.name)
        self.git("config", "user.name", "Test Author")
        self.git("config", "user.email", "author@" + "example" + ".test")
        self.write("rule.md", "v1")
        self.git("add", "rule.md")
        self.git("commit", "-q", "-m", "base")

    def tearDown(self):
        self.tempdir.cleanup()

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=cwd or self.repo, check=True,
            capture_output=True, text=True, env=git_env()).stdout

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        return path

    def run_script(self, *args):
        return subprocess.run(
            ["sh", str(SCRIPT), *args], cwd=self.repo, capture_output=True,
            text=True, env=git_env())

    def commits(self):
        return int(self.git("rev-list", "--count", "HEAD").strip())

    def last_commit_files(self):
        return sorted(self.git("show", "--name-only", "--format=", "HEAD").split())

    def staged(self):
        return sorted(self.git("diff", "--cached", "--name-only").split())

    def refuse_commits(self):
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

    def test_a_relative_name_starting_with_a_dash_commits_only_that_file(self):
        self.write("-x.md", "rule")
        self.write("other.txt", "dirty")
        self.git("add", "other.txt")
        self.write("unrelated.txt", "untracked")
        before = self.commits()

        result = self.run_script("-x.md")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.last_commit_files(), ["-x.md"])

    def test_commits_only_the_given_file(self):
        self.write("rule.md", "v2")
        self.write("other.md", "unrelated")
        self.git("add", "other.md")

        result = self.run_script(str(self.repo / "rule.md"))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.last_commit_files(), ["rule.md"])
        self.assertEqual(self.staged(), ["other.md"])

    def test_new_and_deleted_files_are_committed(self):
        self.write("new.md", "fresh")
        self.assertEqual(self.run_script("new.md").returncode, 0)
        self.assertEqual(self.last_commit_files(), ["new.md"])

        (self.repo / "new.md").unlink()
        self.assertEqual(self.run_script("new.md").returncode, 0)
        self.assertEqual(self.last_commit_files(), ["new.md"])
        self.assertEqual(self.git("ls-files", "new.md"), "")

    def test_unchanged_file_is_a_no_op(self):
        before = self.commits()

        result = self.run_script("rule.md")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.commits(), before)

    def test_refused_commit_of_a_new_file_leaves_it_unstaged(self):
        self.refuse_commits()
        self.write("new.md", "fresh")

        result = self.run_script("new.md")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.staged(), [])
        self.assertEqual(self.git("ls-files", "new.md"), "")

    def test_refused_commit_restores_a_hand_staged_version(self):
        self.write("rule.md", "staged by hand")
        self.git("add", "rule.md")
        hand_staged = self.git("ls-files", "-s", "rule.md")
        self.write("rule.md", "edited again")
        self.refuse_commits()

        result = self.run_script("rule.md")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.git("ls-files", "-s", "rule.md"), hand_staged)

    def test_directory_target_is_refused(self):
        self.write("rules/a.md", "a")
        self.write("rules/b.md", "b")
        before = self.commits()

        result = self.run_script("rules")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.commits(), before)
        self.assertEqual(self.staged(), [])

    def test_deleted_directory_is_not_taken_for_a_deleted_file(self):
        self.write("gone/a.md", "a")
        self.git("add", "gone")
        self.git("commit", "-q", "-m", "dir")
        for child in (self.repo / "gone").iterdir():
            child.unlink()
        (self.repo / "gone").rmdir()
        before = self.commits()

        result = self.run_script("gone")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.commits(), before)

    def test_glob_characters_in_a_name_are_literal(self):
        self.write("x1.md", "one")
        self.write("x2.md", "two")
        self.git("add", "x1.md", "x2.md")
        self.git("commit", "-q", "-m", "pair")
        self.write("x1.md", "changed")
        self.write("x2.md", "changed")
        self.write("x[12].md", "literal")

        result = self.run_script("x[12].md")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.last_commit_files(), ["x[12].md"])

    def test_ignored_file_is_left_alone(self):
        self.write(".gitignore", "secret.md")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "ignore")
        self.write("secret.md", "kept out")
        before = self.commits()

        result = self.run_script("secret.md")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("leaving it alone", result.stdout)
        self.assertEqual(self.commits(), before)

    def test_shell_syntax_in_a_name_is_not_executed(self):
        name = "a`touch pwned`$(touch pwned2).md"
        self.write(name, "x")

        result = self.run_script(name)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.repo / "pwned").exists())
        self.assertFalse((self.repo / "pwned2").exists())

    def test_help_prints_the_header_and_no_code(self):
        result = self.run_script("--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("Exit codes", result.stdout)
        self.assertNotIn("set -u", result.stdout)
        self.assertNotIn("usage()", result.stdout)

    def test_no_argument_exits_2(self):
        self.assertEqual(self.run_script().returncode, 2)


if __name__ == "__main__":
    unittest.main()
