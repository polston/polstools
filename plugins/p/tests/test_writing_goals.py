from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = REPO_ROOT / "plugins" / "p" / "skills" / "writing-goals"
SKILL = SKILL_ROOT / "SKILL.md"
REFERENCES = SKILL_ROOT / "references"
CLAUDE_ADAPTER = REFERENCES / "claude-code.md"
ADAPTERS = sorted(REFERENCES.glob("*.md"))
SHARED_SLOTS = {
    "Objective",
    "Read first",
    "Evidence",
    "Protected scope",
    "Execution loop",
    "Other exits",
    "Handoff",
}
ADAPTER_SLOTS = {"EVIDENCE", "ARTIFACT", "CONSTRAINTS", "PARKED", "BOUNDS"}
ADAPTER_HEADINGS = ("## Capability or semantic", "## Adapter", "## Equivalent outcome")


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


if __name__ == "__main__":
    unittest.main()
