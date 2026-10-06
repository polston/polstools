import json
import re
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "p"
README_PATH = REPO_ROOT / "README.md"
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
CLAUDE_PATH = REPO_ROOT / "CLAUDE.md"
LICENSE_PATH = REPO_ROOT / "LICENSE"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
HARNESSES = ("Claude Code", "Codex", "Antigravity")


def sections(text):
    """Map each level-2 heading to its body."""
    parts = re.split(r"(?m)^## (.+)$", text)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def code_lines(text):
    lines = []
    for fence in re.findall(r"```[a-z]*\n(.*?)```", text, flags=re.S):
        lines.extend(line.strip() for line in fence.splitlines() if line.strip())
    return lines


class ReadmeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = README_PATH.read_text(encoding="utf-8")
        cls.sections = sections(cls.readme)

    def test_every_supported_harness_is_covered_where_a_user_acts(self):
        intro = self.readme.split("\n## ", 1)[0]
        for harness in HARNESSES:
            with self.subTest(section="introduction", harness=harness):
                self.assertIn(harness, intro)
            for heading in ("Install", "Local development"):
                with self.subTest(section=heading, harness=harness):
                    self.assertIn("### " + harness, self.sections[heading])
            with self.subTest(section="Update", harness=harness):
                self.assertIn(harness, self.sections["Update"])
        uninstall = " ".join(code_lines(self.sections["Uninstall"]))
        for command in ("claude plugin uninstall", "codex plugin remove", "agy plugin uninstall"):
            with self.subTest(uninstall=command):
                self.assertIn(command, uninstall)

    def test_plugin_id_in_commands_is_plugin_at_marketplace(self):
        marketplace = json.loads(
            (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        plugin = json.loads(
            (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        expected = plugin["name"] + "@" + marketplace["name"]
        ids = set(re.findall(r"\b" + re.escape(plugin["name"]) + r"@[\w.-]+", self.readme))
        self.assertEqual({expected}, ids)

    def test_every_documented_script_exists(self):
        paths = set(re.findall(r"plugins/p/[\w./-]+", self.readme))
        self.assertTrue(paths)
        for path in sorted(paths):
            with self.subTest(path=path):
                self.assertTrue((REPO_ROOT / path.rstrip(".")).exists())

    def test_every_documented_skill_invocation_names_a_skill(self):
        skills = {path.parent.name for path in PLUGIN_ROOT.glob("skills/*/SKILL.md")}
        invoked = set(re.findall(r"[/$]p:([a-z][a-z-]*)", self.readme))
        self.assertTrue(invoked)
        self.assertLessEqual(invoked, skills)

    def test_relative_links_resolve(self):
        for target in re.findall(r"\]\((?!https?:)([^)#]+)", self.readme):
            with self.subTest(target=target):
                self.assertTrue((REPO_ROOT / target).exists())

    def test_capability_catalogue_names_every_skill_and_command(self):
        expected = [path.parent.name for path in PLUGIN_ROOT.glob("skills/*/SKILL.md")]
        expected += [path.stem for path in PLUGIN_ROOT.glob("commands/*.md")]
        for name in expected:
            with self.subTest(name=name):
                self.assertIn("`" + name + "`", self.sections["Capabilities"])

    def test_validation_section_lists_commands_that_exist(self):
        commands = code_lines(self.sections["Validate a checkout"])
        self.assertGreaterEqual(len(commands), 5)
        for command in commands:
            if command.startswith("git "):
                continue
            with self.subTest(command=command):
                script = re.search(r"plugins/p/bin/[\w.-]+", command)
                self.assertIsNotNone(script)
                self.assertTrue((REPO_ROOT / script.group(0)).is_file())

    def test_readme_keeps_the_external_evaluation_privacy_boundary(self):
        self.assertTrue((PLUGIN_ROOT / "EVALUATION.md").is_file())
        self.assertIsNone(
            re.search(r"[A-Za-z]:[\\/](?:Users|home)[\\/][A-Za-z0-9_.-]+", self.readme)
        )


class RepositoryDocumentTests(unittest.TestCase):
    def test_agent_instruction_files_remain_identical_in_substance(self):
        agents = AGENTS_PATH.read_text(encoding="utf-8").splitlines()[1:]
        claude = CLAUDE_PATH.read_text(encoding="utf-8").splitlines()[1:]
        trailer = agents.index("This file is kept identical in substance to `CLAUDE.md`; edit both together.")
        agents = agents[: trailer - 2]
        while agents and not agents[-1]:
            agents.pop()
        while claude and not claude[-1]:
            claude.pop()
        self.assertEqual(claude, agents)

    def test_licence_file_is_present(self):
        text = LICENSE_PATH.read_text(encoding="utf-8")
        self.assertGreater(len(text.strip()), 200)

    def test_changelog_has_an_entry_for_the_released_version(self):
        manifest = json.loads(
            (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        headings = re.findall(r"(?m)^## \[?(\d+\.\d+\.\d+|Unreleased)\]?", CHANGELOG_PATH.read_text(encoding="utf-8"))
        self.assertIn(manifest["version"], headings)
        self.assertEqual(len(headings), len(set(headings)))


if __name__ == "__main__":
    unittest.main()
