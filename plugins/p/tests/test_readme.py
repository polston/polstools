import re
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "p"
README_PATH = REPO_ROOT / "README.md"


class ReadmeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = README_PATH.read_text(encoding="utf-8")

    def test_introduction_and_installation_cover_both_harnesses(self):
        self.assertIn("Claude Code and Codex", self.readme)
        self.assertIn("claude plugin marketplace add polston/polstools", self.readme)
        self.assertIn("claude plugin install p@polstools --scope user", self.readme)
        self.assertIn("codex plugin marketplace add polston/polstools", self.readme)
        self.assertIn("codex plugin add p@polstools", self.readme)
        self.assertIn("Start a new session", self.readme)

    def test_updates_and_local_development_are_concrete(self):
        self.assertIn("claude plugin update p@polstools --scope user", self.readme)
        self.assertIn("codex plugin remove p@polstools", self.readme)
        self.assertIn("codex plugin marketplace remove polstools", self.readme)
        self.assertIn("claude plugin marketplace add <repo-root>", self.readme)
        self.assertIn("codex plugin marketplace add <repo-root>", self.readme)

    def test_doctor_and_validation_commands_are_copyable(self):
        self.assertIn("/p:doctor", self.readme)
        self.assertIn("$p:doctor", self.readme)
        self.assertIn("plugins/p/bin/p-doctor --repo-root .", self.readme)
        self.assertIn(
            "python -B -m unittest discover -s plugins/p/tests -t plugins/p/tests",
            self.readme,
        )
        self.assertIn("python -B plugins/p/bin/format-e2e", self.readme)
        self.assertIn("sh plugins/p/bin/repo-privacy-audit -C .", self.readme)

    def test_capability_catalogue_names_every_skill_and_command(self):
        expected = [path.parent.name for path in PLUGIN_ROOT.glob("skills/*/SKILL.md")]
        expected += [path.stem for path in PLUGIN_ROOT.glob("commands/*.md")]
        for name in expected:
            with self.subTest(name=name):
                self.assertIn("`" + name + "`", self.readme)

    def test_readme_keeps_the_external_evaluation_privacy_boundary(self):
        self.assertIn("plugins/p/EVALUATION.md", self.readme)
        self.assertIn("RETRO_HOME", self.readme)
        self.assertIsNone(
            re.search(r"[A-Za-z]:[\\/](?:Users|home)[\\/][A-Za-z0-9_.-]+", self.readme)
        )


if __name__ == "__main__":
    unittest.main()
