import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "branch-base-check"


def git_env():
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


class BranchBaseCheckTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        seed = self.root / "seed"
        self.git(self.root, "init", "-q", "-b", "main", str(seed))
        self.identify(seed)
        self.commit(seed, "first")
        self.upstream = self.root / "upstream.git"
        self.git(self.root, "clone", "-q", "--bare", str(seed), str(self.upstream))
        self.local = self.clone("local")

    def tearDown(self):
        self.tempdir.cleanup()

    def git(self, cwd, *args):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True,
            text=True, env=git_env())

    def identify(self, repo):
        self.git(repo, "config", "user.name", "Test Author")
        self.git(repo, "config", "user.email", "author@" + "example" + ".test")

    def commit(self, repo, message):
        self.git(repo, "commit", "-q", "--allow-empty", "-m", message)

    def clone(self, name):
        path = self.root / name
        self.git(self.root, "clone", "-q", str(self.upstream), str(path))
        self.identify(path)
        return path

    def check(self, *args, repo=None):
        return subprocess.run(
            ["sh", str(SCRIPT), "-C", str(repo or self.local), *args],
            capture_output=True, text=True, env=git_env())

    def test_in_sync_exits_0(self):
        result = self.check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("IN SYNC", result.stdout)

    def test_ahead_exits_1_and_lists_the_commits(self):
        self.commit(self.local, "unpushed work")

        result = self.check()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ahead:  1", result.stdout)
        self.assertIn("unpushed work", result.stdout)

    def test_behind_exits_1(self):
        other = self.clone("other")
        self.commit(other, "remote work")
        self.git(other, "push", "-q", "origin", "main")

        result = self.check()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("behind: 1", result.stdout)

    def test_failed_fetch_exits_2_instead_of_trusting_stale_refs(self):
        other = self.clone("other")
        self.commit(other, "remote work")
        self.git(other, "push", "-q", "origin", "main")
        self.upstream.rename(self.root / "moved.git")

        result = self.check()

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("IN SYNC", result.stdout)
        self.assertIn("fetch from 'origin' failed", result.stderr)
        self.assertNotIn(str(self.upstream), result.stderr)

    def test_no_fetch_compares_with_refs_as_last_fetched_and_says_so(self):
        self.upstream.rename(self.root / "moved.git")

        result = self.check("-n")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("as last fetched", result.stdout)

    def test_the_only_remote_is_used_when_none_is_named_origin(self):
        self.git(self.local, "remote", "rename", "origin", "upstream")
        self.commit(self.local, "unpushed work")

        result = self.check()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("remote: upstream/main", result.stdout)

    def test_a_remote_name_with_sed_metacharacters_is_taken_literally(self):
        name = "x|y|e;s|z"
        self.git(self.local, "remote", "rename", "origin", name)
        self.git(self.local, "push", "-q", name, "main:trunk")
        self.git(self.local, "fetch", "-q", name)
        self.git(self.local, "remote", "set-head", name, "trunk")
        self.git(self.local, "branch", "trunk")

        result = self.check("-r", name)

        self.assertIn("remote: " + name + "/trunk", result.stdout,
                      result.stdout + result.stderr)

    def test_several_remotes_and_no_origin_exit_2(self):
        self.git(self.local, "remote", "rename", "origin", "upstream")
        self.git(self.local, "remote", "add", "fork", str(self.upstream))

        result = self.check()

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("pass -r", result.stderr)

    def test_no_remote_at_all_exits_0(self):
        self.git(self.local, "remote", "remove", "origin")

        result = self.check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no remotes", result.stdout)

    def test_named_remote_that_does_not_exist_exits_2(self):
        result = self.check("-r", "nowhere")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_never_pushed_base_exits_1(self):
        self.git(self.local, "branch", "lonely")

        result = self.check("-b", "lonely")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("never been pushed", result.stdout)

    def test_default_branch_is_asked_of_the_remote_when_unrecorded(self):
        seed = self.root / "trunk-seed"
        self.git(self.root, "init", "-q", "-b", "trunk", str(seed))
        self.identify(seed)
        self.commit(seed, "first")
        self.git(self.root, "clone", "-q", "--bare", str(seed), "trunk.git")
        repo = self.root / "trunk-local"
        self.git(self.root, "clone", "-q", str(self.root / "trunk.git"), str(repo))
        self.git(repo, "remote", "set-head", "origin", "-d")

        result = self.check(repo=repo)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("base: trunk", result.stdout)

    def test_unusable_arguments_exit_2(self):
        for args in (("-b",), ("-r", ""), ("--nope",)):
            with self.subTest(args=args):
                self.assertEqual(self.check(*args).returncode, 2)

    def test_help_prints_the_header_and_no_code(self):
        result = subprocess.run(["sh", str(SCRIPT), "--help"],
                                capture_output=True, text=True)

        self.assertEqual(result.returncode, 0)
        self.assertIn("Exit: 0", result.stdout)
        self.assertNotIn("set -eu", result.stdout)


if __name__ == "__main__":
    unittest.main()
