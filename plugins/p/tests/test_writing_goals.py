from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = REPO_ROOT / "plugins" / "p" / "skills" / "writing-goals"
SKILL = SKILL_ROOT / "SKILL.md"
REFERENCES = SKILL_ROOT / "references"
CLAUDE_ADAPTER = REFERENCES / "claude-code.md"
ANTIGRAVITY_ADAPTER = REFERENCES / "antigravity.md"
ADAPTERS = sorted(REFERENCES.glob("*.md"))
SHARED_SLOTS = {
    "Objective",
    "Baseline",
    "Read first",
    "Evidence",
    "Protected scope",
    "Execution loop",
    "Other exits",
    "Handoff",
}
ADAPTER_SLOTS = {"EVIDENCE", "ARTIFACT", "CONSTRAINTS", "PARKED", "BOUNDS"}
ANTIGRAVITY_SLOTS = {
    "OBJECTIVE",
    "BASELINE",
    "READ FIRST",
    "ARTIFACTS",
    "CONSTRAINTS & AUTHORITY",
    "EVIDENCE",
    "EXECUTION LOOP",
    "EXITS",
    "HANDOFF",
}
ADAPTER_HEADINGS = (
    "## Capability or semantic",
    "## Adapter",
    "## Equivalent outcome",
    "## Disposition",
)
HARNESS_INTERNAL_TOOL_NAMES = (
    "invoke_subagent",
    "ArtifactMetadata",
    "update_steps",
    "manage_task",
    "NotificationTimeoutSeconds",
)


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
    def test_entrypoint_routes_to_every_adapter_before_the_shared_method(self):
        text = SKILL.read_text(encoding="utf-8")
        route = text.index("## Route before drafting")
        contract = text.index("## Shared contract")
        routed = set(re.findall(r"`(references/[^`]+\.md)`", text))

        self.assertLess(route, contract)
        self.assertEqual({"references/" + path.name for path in ADAPTERS}, routed)
        self.assertGreaterEqual(len(ADAPTERS), 3)

    def test_shared_contract_lists_every_slot_and_cites_no_adapter_source(self):
        text = SKILL.read_text(encoding="utf-8")

        self.assertEqual(SHARED_SLOTS, table_keys(section(text, "## Shared contract")))
        sources = {
            url
            for path in ADAPTERS
            for url in re.findall(r"https://[^\s)>\]]+", path.read_text(encoding="utf-8"))
        }
        for url in sources:
            with self.subTest(url=url):
                self.assertNotIn(url, text)

    def test_every_adapter_has_the_adapter_template_headings(self):
        for path in ADAPTERS:
            text = path.read_text(encoding="utf-8")
            for heading in ADAPTER_HEADINGS:
                with self.subTest(adapter=path.name, heading=heading):
                    self.assertIn(heading, text)

    def test_claude_adapter_keeps_the_goal_slots_it_maps_to(self):
        claude = CLAUDE_ADAPTER.read_text(encoding="utf-8")

        self.assertEqual(ADAPTER_SLOTS, table_keys(section(claude, "## Adapter")))

    def test_antigravity_adapter_aligns_slots_with_shared_contract(self):
        agy = ANTIGRAVITY_ADAPTER.read_text(encoding="utf-8")

        self.assertEqual(ANTIGRAVITY_SLOTS, table_keys(section(agy, "## Adapter")))

    def test_skill_and_adapters_have_no_empty_sections_or_brittle_tool_names(self):
        for path in [SKILL, *ADAPTERS]:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIsNone(
                    re.search(r"^## .+\n\s*\n## ", text, re.MULTILINE),
                    f"Empty section heading found in {path.name}",
                )
                for tool_name in HARNESS_INTERNAL_TOOL_NAMES:
                    self.assertNotIn(tool_name, text)

    def test_shared_rules_cover_delegation_right_sizing_and_authority(self):
        text = SKILL.read_text(encoding="utf-8")
        rules = section(text, "## Shared rules")

        self.assertIn("cost", rules.lower())
        self.assertIn("speed", rules.lower())
        self.assertIn("reasoning", rules.lower())
        self.assertIn("subagent", rules.lower())
        self.assertIn("host state", rules.lower())
        self.assertIn("flag-separator", rules.lower())
        self.assertIn("per-command timeouts", rules.lower())
        self.assertIn("per-checkpoint failure retry cap", rules.lower())
        self.assertIn("self-contained", rules.lower())


if __name__ == "__main__":
    unittest.main()
