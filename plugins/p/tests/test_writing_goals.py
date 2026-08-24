import json
from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = REPO_ROOT / "plugins" / "p" / "skills" / "writing-goals"
SKILL = SKILL_ROOT / "SKILL.md"
CLAUDE_ADAPTER = SKILL_ROOT / "references" / "claude-code.md"
CODEX_ADAPTER = SKILL_ROOT / "references" / "codex.md"
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN_MANIFEST = REPO_ROOT / "plugins" / "p" / ".claude-plugin" / "plugin.json"


def section(text, heading):
    body = text.split(heading, 1)[1]
    return body.split("\n## ", 1)[0]


def table_keys(text):
    return {
        cells[0]
        for line in text.splitlines()
        if line.startswith("|")
        and (cells := [cell.strip() for cell in line.strip("|").split("|")])
        and cells[0] not in {"Field", "Slot", "---"}
    }


class CrossHarnessGoalGuidanceTests(unittest.TestCase):
    def test_entrypoint_routes_to_existing_adapter_before_shared_method(self):
        text = SKILL.read_text(encoding="utf-8")
        route = text.index("Identify the active harness before drafting")
        contract = text.index("## Shared contract")
        references = set(re.findall(r"`(references/[^`]+\.md)`", text))

        self.assertLess(route, contract)
        self.assertEqual(
            {"references/claude-code.md", "references/codex.md"}, references
        )
        self.assertTrue(all((SKILL_ROOT / path).is_file() for path in references))
        frontmatter = text.split("---", 2)[1]
        self.assertIn("Claude Code, Codex, or another harness", frontmatter)

    def test_shared_contract_is_complete_and_harness_neutral(self):
        text = SKILL.read_text(encoding="utf-8")
        shared = section(text, "## Shared contract")

        self.assertEqual(
            {
                "Objective",
                "Read first",
                "Evidence",
                "Protected scope",
                "Execution loop",
                "Other exits",
                "Handoff",
            },
            table_keys(shared),
        )
        for harness_specific in (
            "code.claude.com",
            "learn.chatgpt.com",
            "120 words",
            "/goal pause",
            "transcript-only",
        ):
            self.assertNotIn(harness_specific, text)

    def test_adapters_declare_sources_divergence_and_equivalent_outcome(self):
        expectations = {
            CLAUDE_ADAPTER: "https://code.claude.com/docs/en/goal",
            CODEX_ADAPTER: "https://learn.chatgpt.com/use-cases/follow-goals",
        }
        for path, source in expectations.items():
            text = path.read_text(encoding="utf-8")
            self.assertIn(source, text)
            self.assertIn("## Capability or semantic", text)
            self.assertIn("## Equivalent outcome", text)
            self.assertIn("## Disposition", text)
            self.assertIn("Convergence test:", text)

    def test_harness_specific_goal_contracts_remain_separate(self):
        claude = CLAUDE_ADAPTER.read_text(encoding="utf-8")
        codex = CODEX_ADAPTER.read_text(encoding="utf-8")

        self.assertEqual(
            {"EVIDENCE", "ARTIFACT", "CONSTRAINTS", "PARKED", "BOUNDS"},
            table_keys(section(claude, "## Adapter")),
        )
        for phrase in ("transcript", "120 words", "turn or time bound"):
            self.assertIn(phrase, claude)
        for phrase in (
            "read-first context",
            "checkpoints",
            "progress evidence",
            "pause, resume, and clear",
        ):
            self.assertIn(phrase, codex)
        self.assertNotIn("/goal pause", claude)

    def test_distribution_metadata_names_goal_guidance(self):
        marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
        entry = next(item for item in marketplace["plugins"] if item["name"] == "p")
        manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(entry["version"], manifest["version"])
        self.assertEqual(entry["description"], manifest["description"])
        self.assertEqual(entry["keywords"], manifest["keywords"])
        self.assertIn("goal", entry["description"].lower())
        self.assertIn("goals", entry["keywords"])


if __name__ == "__main__":
    unittest.main()
