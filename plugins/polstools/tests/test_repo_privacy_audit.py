import os
from pathlib import Path
import subprocess
import tempfile
import unittest


AUDITOR = Path(__file__).resolve().parents[1] / "bin" / "repo-privacy-audit"


class RepoPrivacyAuditTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        self.run_git("init", "-q")
        self.run_git("config", "user.name", "Test Author")
        self.run_git("config", "user.email", self.email("author"))

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def email(local_part):
        return local_part + "@" + "example" + ".test"

    def run_git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

    def commit(self, message):
        (self.repo / "marker.txt").write_text("marker\n", encoding="utf-8")
        self.run_git("add", "marker.txt")
        self.run_git("commit", "-q", "-m", message)

    def audit(self):
        return subprocess.run(
            [str(AUDITOR)],
            cwd=self.repo,
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
        )

    def test_commit_identity_email_is_accepted(self):
        self.commit("ordinary commit")

        result = self.audit()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("accepted authorship records: 1", result.stdout)
        self.assertIn("RESULT: every category read zero", result.stdout)

    def test_coauthor_trailer_email_is_accepted(self):
        message = (
            "ordinary commit\n\n"
            "Co-Authored-By: Automation <" + self.email("automation") + ">"
        )
        self.commit(message)

        result = self.audit()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("accepted authorship records: 2", result.stdout)
        self.assertIn("RESULT: every category read zero", result.stdout)

    def test_commit_message_email_remains_a_finding(self):
        self.commit("contact " + self.email("private"))

        result = self.audit()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        email_row = next(
            line for line in result.stdout.splitlines() if line.startswith("email")
        )
        self.assertEqual(email_row.split()[1:], ["1", "0", "0", "0", "0"])


if __name__ == "__main__":
    unittest.main()
