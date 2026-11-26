"""Structured replies retain answers without attributing question text to users."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fixtures import rollout_assistant, rollout_meta, rollout_user
from test_retro_extract import load_retro, write_rollout

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
from retro_eval.adapters.codex import CodexAdapter
from retro_eval.text_rules import classify_user_turn

STAMP = "2026-08-01T12:00:00.000Z"
PRIOR = ("Authored assistant explanation. " * 12).strip()


def envelope(entries):
    return ("<send_user_message_question_reply>\n" + json.dumps(entries)
            + "\n</send_user_message_question_reply>")


def reply(answer="yes", question="Proceed with the comparison?", identity="authored"):
    return {"answer": answer, "question": question, "questionItemId": identity}


class CodexQuestionReplyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.retro = load_retro()
        self.adapter = CodexAdapter(b"authored-reply-test")

    def inspect(self, body, prior=PRIOR):
        path = write_rollout(self.root / "sessions", "reply.jsonl", [
            rollout_meta(STAMP), rollout_assistant(prior, STAMP),
            rollout_user(body, STAMP)])
        ledger = self.retro.measure_codex(path, self.root / "sessions")
        normalized = self.adapter.read(path, self.root / "sessions")
        evidence = self.adapter.private_prompt_evidence(
            path, self.root / "sessions", lambda text: text)
        self.assertEqual(1, ledger["user_prompts"])
        self.assertEqual(1, normalized.human_prompt_count)
        self.assertEqual(1, len(evidence))
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root)}):
            moments = self.retro._moments_codex(ledger)
        return ledger, next(iter(evidence.values())), moments

    def test_question_words_and_length_do_not_change_approval(self):
        for question in ("Proceed?", "Proceed after checking the missing result?",
                         ("Additional context. " * 80).strip()):
            with self.subTest(question_chars=len(question)):
                ledger, evidence, moments = self.inspect(envelope([reply(question=question)]))
                self.assertEqual(1, ledger["approval_turns"])
                self.assertEqual(0, ledger["correction_candidates"])
                self.assertEqual("yes", evidence["user_turn"])
                self.assertEqual(3, evidence["user_turn_chars"])
                self.assertEqual(len(PRIOR) + len(question), evidence["context_chars"])
                self.assertTrue(evidence["context"].endswith(question[-1200:]))
                self.assertEqual("approval", classify_user_turn(
                    evidence["user_turn"], evidence["context_chars"]))
                self.assertEqual("yes", moments[0]["said"])
                self.assertEqual("approval", moments[0]["kind"])

    def test_correction_answer_survives_neutral_question(self):
        ledger, evidence, moments = self.inspect(envelope([reply(
            answer="No, the result is still wrong.")]))
        self.assertEqual(1, ledger["correction_candidates"])
        self.assertEqual("No, the result is still wrong.", evidence["user_turn"])
        self.assertEqual("correction", moments[0]["kind"])

    def test_multiple_replies_keep_order_and_question_context(self):
        entries = [reply("First answer", "First question?", "one"),
                   reply("Second answer", "Second question?", "two")]
        _, evidence, _ = self.inspect(envelope(entries))
        self.assertEqual("First answer\nSecond answer", evidence["user_turn"])
        self.assertTrue(evidence["context"].endswith("First question?\nSecond question?"))
        self.assertEqual(len(PRIOR) + sum(len(x["question"]) for x in entries),
                         evidence["context_chars"])

    def test_malformed_quoted_and_mixed_wrappers_are_not_decoded(self):
        valid = envelope([reply()])
        bodies = [valid + "\nMy additional explanation.", "Quoted example:\n" + valid,
                  "```\n" + valid + "\n```", envelope([]), envelope([reply(answer="")]),
                  envelope([reply(answer=42)]), envelope([{"answer": "yes"}]),
                  envelope([{**reply(), "unknown": True}]),
                  valid.replace('"answer": "yes"', '"answer": "no", "answer": "yes"'),
                  valid.replace('[{', '[{broken'), valid[:-5]]
        for body in bodies:
            with self.subTest(body=body[:60]):
                _, evidence, _ = self.inspect(body)
                self.assertEqual(body[:1200], evidence["user_turn"])
                self.assertEqual(len(body), evidence["user_turn_chars"])
                self.assertEqual(len(PRIOR), evidence["context_chars"])

    def test_answer_looking_like_machine_context_is_still_an_answer(self):
        answer = "<skill>this is quoted in my answer</skill>"
        _, evidence, _ = self.inspect(envelope([reply(answer=answer)]))
        self.assertEqual(answer, evidence["user_turn"])

    def test_question_supplies_observed_context_when_not_in_assistant_message(self):
        question = "A detailed question with necessary context. " * 8
        ledger, evidence, moments = self.inspect(envelope([reply(question=question)]), prior="")
        self.assertEqual(len(question.rstrip()), evidence["context_chars"])
        self.assertEqual(1, ledger["approval_turns"])
        self.assertEqual("approval", moments[0]["kind"])

    def test_interrupt_marker_inside_answer_does_not_remove_its_prompt(self):
        answer = "[request interrupted] quoted diagnostic"
        ledger, evidence, moments = self.inspect(envelope([reply(answer=answer)]))
        self.assertEqual(answer, evidence["user_turn"])
        self.assertEqual(1, ledger["interrupts"])
        self.assertEqual("interrupt", moments[0]["kind"])

    def test_question_context_is_redacted_before_excerpting(self):
        question = "A" * 40 + "." * 280
        _, _, moments = self.inspect(envelope([reply(question=question)]))
        self.assertNotIn("A" * 20, moments[0]["after"])
        self.assertIn("<long-token>", moments[0]["after"])

    def test_multibyte_answers_preserve_classifier_boundaries(self):
        for length in (200, 201, 600, 601, 1200, 1201):
            answer = "界" * length
            with self.subTest(characters=length):
                ledger, evidence, _ = self.inspect(envelope([reply(answer=answer)]))
                self.assertEqual(length, evidence["user_turn_chars"])
                self.assertEqual(answer[:1200], evidence["user_turn"])
                expected = classify_user_turn(answer, evidence["context_chars"])
                self.assertEqual(int(expected == "correction"),
                                 ledger["correction_candidates"])

    def test_previous_answer_context_does_not_leak_to_next_prompt(self):
        question = "First question context. " * 10
        path = write_rollout(self.root / "sessions", "consecutive.jsonl", [
            rollout_meta(STAMP), rollout_assistant(PRIOR, STAMP),
            rollout_user(envelope([reply(question=question)]), STAMP),
            rollout_user("Another request.", STAMP)])
        evidence = list(self.adapter.private_prompt_evidence(
            path, self.root / "sessions", lambda text: text).values())
        self.assertEqual(2, len(evidence))
        self.assertEqual("", evidence[1]["context"])
        self.assertEqual(0, evidence[1]["context_chars"])
        ledger = self.retro.measure_codex(path, self.root / "sessions")
        self.assertEqual(2, ledger["user_prompts"])
        self.assertEqual(1, ledger["approval_turns"])
        self.assertEqual(0, ledger["correction_candidates"])
