"""The evaluation layer and bin/retro.py share one implementation of the text rules."""

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))


def load_retro():
    spec = importlib.util.spec_from_file_location(
        "retro_text_rules_under_test", PLUGIN_ROOT / "bin" / "retro.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROBE = r"""
import importlib.util, json, sys
sys.path.insert(0, sys.argv[1])
loaded = []
original = importlib.util.spec_from_file_location
def recording(name, location=None, *args, **kwargs):
    loaded.append(str(location))
    return original(name, location, *args, **kwargs)
importlib.util.spec_from_file_location = recording
from retro_eval import label_cli, predictors, private_evidence
redactor = private_evidence._default_redactor()
legacy = label_cli._current_legacy_predictor()
row = {"user_turn": "no, use the other file", "context_chars": "500"}
results = {
    "redacted": redactor("key " + "k" * 40),
    "predictor": predictors.legacy_turn_friction_v1(row),
    "legacy": legacy({"predicted": "none", "reply_chars": 22,
                      "prior_chars": 500, "said": "no, use the other file"}),
    "script_loads": sorted(path for path in loaded if path.endswith("retro.py")),
}
print(json.dumps(results))
"""


class SharedTextRuleTests(unittest.TestCase):
    def test_evaluation_bridges_do_not_execute_the_script(self):
        completed = subprocess.run(
            [sys.executable, "-B", "-c", PROBE, str(PLUGIN_ROOT)],
            capture_output=True, text=True, check=True)
        results = json.loads(completed.stdout)
        self.assertEqual("key <long-token>", results["redacted"])
        self.assertEqual("correction", results["predictor"])
        self.assertEqual("correction", results["legacy"])
        self.assertEqual([], results["script_loads"])

    def test_script_reexports_the_shared_objects(self):
        from retro_eval import text_rules
        retro = load_retro()
        for name in ("redact", "_redaction_patterns", "classify_user_turn",
                     "is_approval", "_predict_at", "_INTERRUPT",
                     "CORRECTION_MAX_CHARS", "CORRECTION_MIN_PRIOR_CHARS"):
            with self.subTest(name=name):
                self.assertIs(getattr(text_rules, name), getattr(retro, name))

    def test_shared_rules_keep_their_behaviour(self):
        from retro_eval import text_rules
        home = str(Path.home())
        self.assertEqual("~/notes.txt", text_rules.redact(home + "/notes.txt"))
        self.assertEqual("", text_rules.redact(None))
        self.assertEqual("approval", text_rules.classify_user_turn("looks good", 500))
        self.assertEqual("question", text_rules.classify_user_turn("which one?", 500))
        self.assertEqual("interrupt", text_rules.classify_user_turn(
            "[Request interrupted by user]", 0))
        self.assertEqual("", text_rules.classify_user_turn("no", 10))
        self.assertEqual("none", text_rules._predict_at(
            {"predicted": "none", "reply_chars": 300, "prior_chars": 500,
             "said": "x" * 300}, 200, 200))


class ContentFlatteningTests(unittest.TestCase):
    def test_adapter_and_script_flatten_message_content_through_one_function(self):
        from retro_eval import text_rules
        from retro_eval.adapters import claude, codex
        retro = load_retro()
        self.assertIs(text_rules.text_of, retro.text_of)
        self.assertIs(text_rules.prose_of, retro.prose_of)
        self.assertIs(text_rules.content_text, retro._content_text)
        self.assertFalse(hasattr(claude, "_message_text"))
        self.assertIs(text_rules.text_of, claude.text_of)

    def test_text_of_joins_text_bare_strings_and_tool_results_prose_of_only_text(self):
        from retro_eval.text_rules import prose_of, text_of
        message = {"content": [
            "bare", {"type": "text", "text": "typed"},
            {"type": "tool_result", "content": "result"},
            {"type": "image", "source": "ignored"}]}
        self.assertEqual("bare\ntyped\nresult", text_of(message))
        self.assertEqual("typed", prose_of(message))
        self.assertEqual("plain", text_of({"content": "plain"}))
        self.assertEqual("", text_of("not a message"))


if __name__ == "__main__":
    unittest.main()
