import re
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
SPEC_PATH = PLUGIN_ROOT / "style" / "response-format.md"
REMINDER_PATH = PLUGIN_ROOT / "style" / "turn-reminder.md"
MAINTENANCE_PATH = (
    PLUGIN_ROOT / "skills" / "maintaining-the-format-plugin" / "SKILL.md"
)
DESIGN_PATH = REPO_ROOT / "docs" / "plans" / "2026-08-19-response-format.md"
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'_-]*")


class FormatContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = SPEC_PATH.read_text(encoding="utf-8")
        cls.reminder = REMINDER_PATH.read_text(encoding="utf-8")
        cls.maintenance = MAINTENANCE_PATH.read_text(encoding="utf-8")
        cls.design = DESIGN_PATH.read_text(encoding="utf-8")

    def test_full_contract_is_final_only(self):
        self.assertIn("Structure every turn-ending reply", self.spec)
        self.assertNotIn("Structure every reply", self.spec)
        self.assertIn("An interim message", self.spec)
        self.assertIn("immediately precedes a tool call", self.spec)
        self.assertIn(
            "Interim message immediately before a tool call",
            self._normalized(self.reminder),
        )
        self.assertIn("repeat actions in final", self.reminder)

    def test_density_caps_and_conditional_separator_are_reinjected(self):
        self.assertRegex(
            self.spec,
            r"at most five non-`None` top-level items across FINDINGS, PROBLEMS, and ASKS",
        )
        self.assertIn("at most five non-`None`", self.reminder)
        self.assertIn("at most 30 words", self.spec)
        self.assertIn("at most 30 words", self.reminder)
        self.assertIn("Write `---` only when optional detail follows it", self.spec)
        self.assertIn("Optional detail after `---`", self.reminder)

    def test_payloads_stay_within_the_measured_context_budgets(self):
        self.assertLessEqual(len(SPEC_PATH.read_bytes()), 4200)
        self.assertLessEqual(len(REMINDER_PATH.read_bytes()), 650)

    def test_problem_actions_and_ask_location_are_reinjected(self):
        self.assertIn("`**N.1.** Consequence`", self.spec)
        self.assertIn("`**N.2.** Recommendation`", self.spec)
        self.assertIn("the only place for questions that need a reader answer", self.spec)
        self.assertIn("Each PROBLEM: reader consequence + recommendation", self.reminder)
        self.assertIn("questions\nonly in ASKS", self.reminder)

    def test_material_proposals_disclose_the_complete_change_surface(self):
        labels = ("ADD", "CHANGE", "REMOVE", "PRESERVE")
        for index, label in enumerate(labels, 1):
            self.assertIn(f"`**N.2.{index}. {label}**`", self.spec)
            self.assertIn(label, self.reminder)
        self.assertIn("current state → proposed state", self.spec)
        self.assertIn(
            "current → proposed plus mechanism", self._normalized(self.reminder)
        )
        self.assertIn("write `None.` for an empty", self.spec)
        self.assertIn("Keep above the rule", self.reminder)
        self.assertIn("Omit it for completed work", self.spec)

    def test_only_open_problem_and_ask_numbers_persist(self):
        self.assertIn("Unresolved PROBLEMS and ASKS retain their numbers", self.spec)
        self.assertIn(
            "do not reuse a retired problem or ask number",
            self._normalized(self.spec),
        )
        self.assertIn("FINDINGS restart at 1", self.spec)
        self.assertIn("Open PROBLEM/ASK numbers persist without reuse", self.reminder)

    def test_example_obeys_density_and_word_caps(self):
        example = self.spec.split("<example>\n", 1)[1].split("\n</example>", 1)[0]
        findings, problems, asks = self._sections(example)
        finding_items = self._flat_items(findings)
        problem_titles = [
            match.group(1)
            for line in problems
            if (match := re.fullmatch(r"\*\*\d+\. (.+)\*\*", line))
        ]
        problem_subpoints = [
            match.group(1)
            for line in problems
            if (match := re.fullmatch(r"\*\*\d+\.\d+\.\*\* (.+)", line))
        ]
        proposal_parts = [
            (match.group(1), match.group(2))
            for line in problems
            if (
                match := re.fullmatch(
                    r"\*\*\d+\.\d+\.\d+\. (ADD|CHANGE|REMOVE|PRESERVE)\*\* (.+)",
                    line,
                )
            )
        ]
        ask_items = self._flat_items(asks)

        self.assertLessEqual(
            len(finding_items) + len(problem_titles) + len(ask_items), 5
        )
        self.assertTrue(all(self._words(item) <= 30 for item in finding_items))
        self.assertTrue(all(self._words(item) <= 15 for item in problem_titles))
        self.assertTrue(all(self._words(item) <= 30 for item in problem_subpoints))
        self.assertEqual(
            [label for label, _ in proposal_parts],
            ["ADD", "CHANGE", "REMOVE", "PRESERVE"],
        )
        self.assertTrue(all(self._words(item) <= 30 for _, item in proposal_parts))
        self.assertTrue(all(self._words(item) <= 30 for item in ask_items))
        self.assertTrue(all("?" not in line for line in findings + problems))
        self.assertTrue(all("?" in item for item in ask_items))

    def test_maintenance_guidance_is_phase_aware(self):
        self.assertIn("## Behavioral health check", self.maintenance)
        self.assertIn("Report interim and turn-ending messages separately", self.maintenance)
        self.assertIn("aggregate counts only", self.maintenance)
        self.assertIn("A turn-ending reply can omit `---`", self.maintenance)

    def test_design_record_names_current_layout(self):
        for stale in (
            "plugins/format/",
            "/format:off",
            "/format:on",
            "19-check",
            "25-check",
            "%LOCALAPPDATA%",
            "~/.local/state",
        ):
            self.assertNotIn(stale, self.design)
        self.assertIn("plugins/p/bin/python-launcher", self.design)
        self.assertIn("plugins/p/bin/format-e2e", self.design)
        self.assertIn("54-check end-to-end verifier", self.design)
        self.assertIn("off by default", self.design)
        self.assertIn("polstools/format.json", self.design)
        self.assertIn("P_FORMAT_DEFAULT", self.design)
        self.assertIn("P_FORMAT_HARNESS", self.design)
        self.assertIn("4,200-byte", self.design)
        self.assertIn("650-byte", self.design)
        self.assertIn("/p:fmt-off", self.design)
        self.assertIn("/p:fmt-on", self.design)

    @staticmethod
    def _sections(example):
        lines = example.splitlines()
        findings_at = lines.index("# FINDINGS")
        problems_at = lines.index("# PROBLEMS")
        asks_at = lines.index("# ASKS")
        separator_at = lines.index("---")
        return (
            [line for line in lines[findings_at + 1 : problems_at] if line],
            [line for line in lines[problems_at + 1 : asks_at] if line],
            [line for line in lines[asks_at + 1 : separator_at] if line],
        )

    @staticmethod
    def _flat_items(lines):
        return [
            match.group(1)
            for line in lines
            if (match := re.fullmatch(r"\*\*\d+\.\*\* (.+)", line))
        ]

    @staticmethod
    def _words(text):
        return len(WORD.findall(text))

    @staticmethod
    def _normalized(text):
        return " ".join(text.split())


if __name__ == "__main__":
    unittest.main()
