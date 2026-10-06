from pathlib import Path
import re
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REFERENCE = re.compile(r"<plugin-root>/([A-Za-z0-9_./-]+)")
SELF_LOCATION = re.compile(r"whose directory is\s+`<plugin-root>/(skills/[^`]+)`")
# Claude Code substitutes this variable in skill and command text; Codex and
# Antigravity do not, and no harness exports it to an agent's shell commands.
CLAUDE_ONLY = re.compile(r"\$\{?CLAUDE_PLUGIN_ROOT\}?")


def skill_files():
    return sorted(PLUGIN_ROOT.glob("skills/*/SKILL.md"))


def command_files():
    return sorted(PLUGIN_ROOT.glob("commands/*.md"))


class PluginRootConventionTests(unittest.TestCase):
    def test_every_plugin_root_reference_resolves_inside_the_plugin(self):
        for path in skill_files() + command_files():
            text = path.read_text(encoding="utf-8")
            for target in REFERENCE.findall(text):
                target = target.rstrip(".")
                with self.subTest(file=str(path.relative_to(PLUGIN_ROOT)), target=target):
                    self.assertTrue((PLUGIN_ROOT / target).exists())

    def test_each_skill_states_its_own_location_correctly(self):
        for path in skill_files():
            text = path.read_text(encoding="utf-8")
            with self.subTest(skill=path.parent.name):
                stated = SELF_LOCATION.findall(text)
                self.assertEqual(
                    [path.parent.relative_to(PLUGIN_ROOT).as_posix()], stated)

    def test_no_skill_depends_on_a_claude_only_root(self):
        for path in skill_files():
            with self.subTest(skill=path.parent.name):
                self.assertIsNone(
                    CLAUDE_ONLY.search(path.read_text(encoding="utf-8")))

    def test_command_fallback_reaches_the_same_skill_as_the_claude_path(self):
        for path in command_files():
            text = path.read_text(encoding="utf-8")
            claude = re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/(skills/[^`]+)`", text)
            portable = REFERENCE.findall(text)
            with self.subTest(command=path.stem):
                self.assertEqual(["skills/%s/SKILL.md" % path.stem], claude)
                self.assertEqual(claude, portable)


if __name__ == "__main__":
    unittest.main()
