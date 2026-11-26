import os
from pathlib import Path
import sys
import unittest
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

import skill_activation  # noqa: E402


# Names that the Antigravity executable never sets (absent from its strings).
NOT_ANTIGRAVITY = ("ANTIGRAVITY_SESSION_ID", "AGY_SESSION_ID", "AGY_CONVERSATION_ID")


class AntigravitySessionIdTests(unittest.TestCase):

    def test_conversation_id_selects_the_antigravity_session(self):
        self.assertEqual("conv-1", skill_activation.session_id_from_env(
            {"ANTIGRAVITY_CONVERSATION_ID": "conv-1"}))

    def test_guessed_names_are_not_session_ids(self):
        for name in NOT_ANTIGRAVITY:
            with self.subTest(name=name):
                self.assertIsNone(skill_activation.session_id_from_env({name: "conv-2"}))

    def test_retro_excludes_the_reporting_antigravity_conversation(self):
        from test_retro_extract import load_retro

        retro = load_retro()
        with mock.patch.dict(os.environ, {
                "ANTIGRAVITY_CONVERSATION_ID": "conv-4", "AGY_SESSION_ID": "guess-1"}):
            ids = retro.reporting_session_ids([])
        self.assertIn("conv-4", ids)
        self.assertNotIn("guess-1", ids)

if __name__ == "__main__":
    unittest.main()
