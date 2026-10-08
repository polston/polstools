from pathlib import Path
import re
import subprocess
import unittest


PLUGIN = Path(__file__).resolve().parents[1]

# skill -> the script it tells the operator to run
SKILL_SCRIPTS = {
    "auditing-a-repo-for-private-data": "repo-privacy-audit",
    "checking-branch-base-before-a-pr": "branch-base-check",
}


class RepoSafetySkillTests(unittest.TestCase):
    def test_each_skill_runs_its_script_with_flags_the_script_accepts(self):
        for skill, script in SKILL_SCRIPTS.items():
            with self.subTest(skill=skill):
                text = (PLUGIN / "skills" / skill / "SKILL.md").read_text(
                    encoding="utf-8")
                line = next((l for l in text.splitlines()
                             if "<plugin-root>/bin/" + script in l), None)
                self.assertIsNotNone(line, skill + " does not name " + script)
                self.assertTrue((PLUGIN / "bin" / script).is_file())
                usage = subprocess.run(
                    ["sh", str(PLUGIN / "bin" / script), "--help"],
                    capture_output=True, text=True).stdout
                for flag in re.findall(r"(?<![\w-])-[A-Za-z]\b", line):
                    self.assertIn(flag, usage, script + " --help lacks " + flag)

    def test_audit_skill_does_not_show_the_retired_argument_list_form(self):
        text = (PLUGIN / "skills" / "auditing-a-repo-for-private-data"
                / "SKILL.md").read_text(encoding="utf-8")

        self.assertFalse("$(git rev-list --all)" in text,
                         "the skill still shows the argument-list form")


if __name__ == "__main__":
    unittest.main()
