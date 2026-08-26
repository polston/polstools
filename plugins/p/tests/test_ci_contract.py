import re
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validate.yml"


class ContinuousIntegrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_runs_on_pull_requests_main_pushes_and_manual_dispatch(self):
        self.assertRegex(self.workflow, r"(?m)^on:\s*$")
        self.assertRegex(self.workflow, r"(?m)^  pull_request:\s*$")
        self.assertRegex(self.workflow, r"(?m)^  push:\s*$")
        self.assertIn("branches: [main]", self.workflow)
        self.assertRegex(self.workflow, r"(?m)^  workflow_dispatch:\s*$")

    def test_matrix_covers_windows_and_linux_without_fail_fast(self):
        self.assertIn("ubuntu-latest", self.workflow)
        self.assertIn("windows-latest", self.workflow)
        self.assertIn("fail-fast: false", self.workflow)
        self.assertIn("runs-on: ${{ matrix.os }}", self.workflow)

    def test_permissions_and_actions_are_narrow_and_pinned(self):
        self.assertRegex(
            self.workflow,
            r"(?m)^permissions:\s*\n  contents: read\s*$",
        )
        self.assertIn("uses: actions/checkout@v4", self.workflow)
        self.assertIn("uses: actions/setup-python@v5", self.workflow)
        self.assertNotRegex(self.workflow, r"permissions:\s*write")

    def test_runs_the_repository_validation_contract(self):
        commands = (
            "sh plugins/p/bin/python-launcher -B -m unittest discover -s plugins/p/tests -t plugins/p/tests",
            "sh plugins/p/bin/python-launcher -B plugins/p/bin/format-e2e",
            "sh plugins/p/bin/p-validate",
            "sh plugins/p/bin/repo-privacy-audit -C .",
            "git diff --check",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, self.workflow)
        self.assertIn("shell: bash", self.workflow)

    def test_runtime_remains_dependency_free(self):
        self.assertNotIn("pip install", self.workflow)
        self.assertNotIn("npm install", self.workflow)
        self.assertNotIn("uv sync", self.workflow)


if __name__ == "__main__":
    unittest.main()
