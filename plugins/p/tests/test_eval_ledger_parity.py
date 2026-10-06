"""Differential tests: the retro.py ledger versus the evaluation adapters.

Each pair either agrees on every input here, or differs on an input pinned
below as a deliberate, documented difference. A new disagreement fails.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fixtures import (claude_assistant, claude_user, rollout_assistant,
                      rollout_call, rollout_meta, rollout_user)
from test_retro_extract import load_retro

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.adapters import base  # noqa: E402
from retro_eval.adapters.base import iter_jsonl, parse_timestamp  # noqa: E402
from retro_eval.adapters.claude import ClaudeAdapter  # noqa: E402
from retro_eval.adapters.codex import CodexAdapter  # noqa: E402
from retro_eval.instruction_manifest import _parse_stamp  # noqa: E402
from retro_eval.pipeline import EvaluationPipeline  # noqa: E402
from retro_eval.schema import SpanKind  # noqa: E402
from retro_eval.taxonomies import classify_failure_evidence, load_tool_taxonomy  # noqa: E402

retro = load_retro()
SALT = b"s" * 32
T0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
STAMP = "2026-08-01T12:00:00Z"
LONG = "A substantial assistant explanation. " * 12


def _profile_options(name):
    profile = json.loads((PLUGIN_ROOT / "profiles" / "sources.json")
                         .read_text(encoding="utf-8"))
    return next(item["options"] for item in profile["sources"]
                if item["name"] == name)


class _Corpus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "root"
        self.root.mkdir()

    def write(self, rel, records):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records),
                        encoding="utf-8")
        return path


class ClaudeHumanPromptParity(_Corpus):
    def counts(self, user_record):
        path = self.write("proj/session.jsonl", [
            claude_assistant(LONG, T0), user_record,
            claude_assistant("done", T0 + timedelta(minutes=1))])
        ledger = retro.measure(path, "claude", self.root)["user_prompts"]
        adapter = ClaudeAdapter(SALT).read(path, self.root).human_prompt_count
        return ledger, adapter

    def test_profile_prompt_sources_equal_ledger_sources(self):
        self.assertEqual(retro.HUMAN_PROMPT_SOURCES,
                         frozenset(_profile_options("claude")["direct_prompt_sources"]))

    def test_agreeing_inputs(self):
        tool_generated = dict(claude_user("tool text", T0),
                              sourceToolAssistantUUID="a1")
        tool_result = dict(claude_user("result text", T0), toolUseResult={"ok": 1})
        unsourced_wrapper = claude_user("<system-reminder>x</system-reminder>", T0)
        del unsourced_wrapper["promptSource"]
        cases = {
            "typed": (claude_user("please continue", T0), 1),
            "queued": (claude_user("next one", T0, source="queued"), 1),
            "system": (claude_user("injected", T0, source="system"), 0),
            "sdk": (claude_user("programmatic", T0, source="sdk"), 0),
            "tool_generated": (tool_generated, 0),
            "tool_result": (tool_result, 0),
            "unsourced_wrapper": (unsourced_wrapper, 0),
        }
        for name, (record, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual((expected, expected), self.counts(record))

    def test_pinned_differences(self):
        # Ledger counts an unsourced plain-text record; the adapter excludes
        # ambiguous legacy records unless origin.kind names a human.
        unsourced = claude_user("please continue", T0)
        del unsourced["promptSource"]
        unsourced_human = dict(unsourced, origin={"kind": "human"})
        meta = dict(claude_user("meta text", T0), isMeta=True)
        image_only = claude_user("", T0)
        image_only["message"]["content"] = [{"type": "image", "source": {}}]
        cases = {
            "unsourced_plain_text": (unsourced, (1, 0)),
            "unsourced_origin_human": (unsourced_human, (1, 1)),
            "typed_is_meta": (meta, (1, 0)),
            "typed_without_text": (image_only, (0, 1)),
        }
        for name, (record, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(expected, self.counts(record))


class ClaudeSkillRunParity(_Corpus):
    def stamped(self, skill, minutes, message=None):
        record = claude_assistant("working", T0 + timedelta(minutes=minutes))
        if skill is not None:
            record["attributionSkill"] = skill
        if message is not None:
            record["message"] = message
        return record

    def counts(self, records):
        path = self.write("proj/session.jsonl",
                          [claude_user("start", T0)] + records)
        ledger = retro.measure(path, "claude", self.root)["skill_runs"]
        result = ClaudeAdapter(SALT).read(path, self.root)
        adapter = sum(1 for r in result.records
                      if "start_basis" in r.attributes)
        return ledger, adapter

    def test_agreeing_and_edge_inputs(self):
        cases = {
            "one_run": ([self.stamped("a", 1), self.stamped("a", 2)], 1),
            "two_skills": ([self.stamped("a", 1), self.stamped("b", 2)], 2),
            "gap_ends_a_run": ([self.stamped("a", 1), self.stamped(None, 2),
                                self.stamped("a", 3)], 2),
            "stamp_type_differs": ([self.stamped(7, 1), self.stamped("7", 2)], 1),
            "non_dict_message_is_ignored": (
                [self.stamped("a", 1), self.stamped("b", 2, message="text"),
                 self.stamped("a", 3)], 1),
        }
        for name, (records, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual((expected, expected), self.counts(records))


class CodexHumanPromptParity(_Corpus):
    def test_profile_openers_equal_ledger_openers(self):
        self.assertEqual(retro.MACHINE_PROMPT_OPENERS,
                         tuple(_profile_options("codex")["machine_prompt_openers"]))

    def test_wrapper_and_empty_user_messages_are_not_human_prompts(self):
        path = self.write("rollout-a.jsonl", [
            rollout_meta(STAMP),
            rollout_user("<environment_context>cwd</environment_context>", STAMP),
            rollout_user("<skill><name>x</name></skill>", STAMP),
            rollout_user("", STAMP),
            rollout_user("inspect the example", STAMP),
            rollout_assistant(LONG, STAMP),
            rollout_user("now the second one", STAMP),
        ])
        ledger = retro.measure_codex(path, self.root)["user_prompts"]
        result = CodexAdapter(SALT).read(path, self.root)
        self.assertEqual(2, ledger)
        self.assertEqual(ledger, result.human_prompt_count)
        prompts = [r for r in result.records if r.span_kind == SpanKind.PROMPT]
        self.assertEqual(2, len(prompts))

    def test_pinned_wrapper_only_rollout_is_a_ledger_row_but_not_a_trace(self):
        path = self.write("rollout-b.jsonl", [
            rollout_meta(STAMP),
            rollout_user("<environment_context>x</environment_context>", STAMP),
            rollout_assistant("hi", STAMP)])
        row = retro.measure_codex(path, self.root)
        result = CodexAdapter(SALT).read(path, self.root)
        self.assertEqual(("main", 0), (row["population"], row["user_prompts"]))
        self.assertEqual((False, "no direct-human prompt"),
                         (result.included, result.exclusion_reason))

    def test_main_population_membership_agrees(self):
        variants = {"user": ("user", None), "subagent": ("subagent", None),
                    "automation": ("automation", None), "absent": (None, None),
                    "absent_with_parent": (None, "parent-1"),
                    "unrecognised": ("other", None)}
        for name, (thread_source, parent) in variants.items():
            with self.subTest(name):
                path = self.write("rollout-%s.jsonl" % name, [
                    rollout_meta(STAMP, thread_source=thread_source, parent=parent),
                    rollout_user("inspect the example", STAMP),
                    rollout_assistant("hi", STAMP)])
                population = retro.measure_codex(path, self.root)["population"]
                included = CodexAdapter(SALT).read(path, self.root).included
                self.assertEqual(population == "main", included)

    def test_equivalent_json_arguments_share_one_signature(self):
        path = self.write("rollout-c.jsonl", [
            rollout_meta(STAMP), rollout_user("inspect", STAMP),
            rollout_call("shell", '{"cmd": "ls", "cwd": "."}', STAMP, "c1",
                         "function_call"),
            rollout_call("shell", '{"cwd":".","cmd":"ls"}', STAMP, "c2",
                         "function_call")])
        self.assertEqual(1, retro.measure_codex(path, self.root)["repeat_calls"])
        tools = [r for r in CodexAdapter(SALT).read(path, self.root).records
                 if r.span_kind == SpanKind.TOOL]
        self.assertEqual(2, len(tools))
        self.assertEqual(tools[0].call_signature, tools[1].call_signature)


class SignatureParity(_Corpus):
    def test_pinned_input_less_claude_calls_repeat_only_in_the_adapter(self):
        path = self.write("proj/s.jsonl", [
            claude_user("go", T0),
            claude_assistant("one", T0, tools=[("TaskList", None)]),
            claude_assistant("two", T0, tools=[("TaskList", None)])])
        self.assertEqual(0, retro.measure(path, "claude", self.root)["repeat_calls"])
        tools = [r for r in ClaudeAdapter(SALT).read(path, self.root).records
                 if r.span_kind == SpanKind.TOOL]
        self.assertEqual(tools[0].call_signature, tools[1].call_signature)


class TimestampParity(_Corpus):
    def test_aware_timestamps_agree(self):
        for value in ("2026-08-01T12:00:00Z", "2026-08-01T14:00:00+02:00",
                      "2026-08-01T12:00:00.123456+00:00"):
            with self.subTest(value):
                expected = retro.parse_ts(value)
                self.assertEqual(expected, parse_timestamp(value))
                self.assertEqual(expected, _parse_stamp(value))

    def test_unusable_values_parse_to_none(self):
        for value in (None, "", "not a time", 1754049600, ["2026-08-01"]):
            with self.subTest(repr(value)):
                self.assertIsNone(retro.parse_ts(value))
                self.assertIsNone(parse_timestamp(value))

    def test_naive_timestamps_are_rejected_not_guessed(self):
        self.assertIsNone(parse_timestamp("2026-08-01T12:00:00"))
        with self.assertRaises(ValueError):
            _parse_stamp("2026-08-01T12:00:00")
        # Pinned: the ledger keeps a naive value; its owner decides.
        self.assertIsNone(retro.parse_ts("2026-08-01T12:00:00").tzinfo)

    def test_mixed_naive_and_aware_records_do_not_break_the_adapter(self):
        naive = claude_user("please continue", T0)
        naive["timestamp"] = "2026-08-01T12:00:00"
        path = self.write("proj/s.jsonl", [
            naive, claude_assistant("done", T0 + timedelta(minutes=1))])
        result = ClaudeAdapter(SALT).read(path, self.root)
        self.assertTrue(result.included)
        root = result.records[0]
        self.assertEqual(T0 + timedelta(minutes=1), root.started_at)


class JsonlParity(_Corpus):
    def test_dict_records_agree_and_partial_lines_are_skipped(self):
        path = self.root / "mixed.jsonl"
        path.write_text('{"a": 1}\n[1, 2]\n"text"\n\n{"b": 2}\n{"c":',
                        encoding="utf-8")
        ledger = [r for r in retro.read_records(path) if isinstance(r, dict)]
        self.assertEqual(ledger, [record for _, record in iter_jsonl(path)])

    def test_unreadable_source_raises_in_both_readers(self):
        unreadable = self.root / "dir.jsonl"
        unreadable.mkdir()
        with self.assertRaises(retro.TranscriptUnreadable):
            list(retro.read_records(unreadable))
        with self.assertRaises(getattr(base, "SourceUnreadable", ())):
            list(iter_jsonl(unreadable))

    def test_pipeline_reports_unreadable_source_by_reason(self):
        (self.root / "proj").mkdir()
        (self.root / "proj" / "dir.jsonl").mkdir()
        self.write("proj/ok.jsonl", [claude_user("go", T0),
                                     claude_assistant("done", T0)])
        work = Path(self.tmp.name) / "work"
        summary = EvaluationPipeline(work).extract(roots={"claude": self.root})
        self.assertEqual({"unreadable": 1}, summary.exclusion_reasons)
        self.assertEqual(1, summary.included_traces)


class FailureVocabularyParity(unittest.TestCase):
    """The ledger's mechanical-failure columns and the evaluation taxonomy are
    different vocabularies; this pins where they agree and where they do not."""

    def test_vocabularies_are_disjoint(self):
        columns = {column for column, _, _ in retro.FAILURE_MARKERS}
        self.assertFalse(columns & set(load_tool_taxonomy().failure_kinds))

    def test_marker_mapping(self):
        cases = (
            ("Read", "File does not exist.", "missing_path_target", "missing_target"),
            ("Grep", "Path does not exist", "missing_path_target", "missing_target"),
            # Known divergences: the taxonomy has no rule for these harness
            # markers and falls through to execution_failure.
            ("Bash", "InputValidationError: bad", "invalid_tool_input",
             "execution_failure"),
            ("Write", "File has not been read yet.", "unread_before_write",
             "execution_failure"),
            ("StructuredOutput", "Output does not match required schema",
             "schema_rejected", "execution_failure"),
        )
        for tool, body, column, kind in cases:
            with self.subTest(column=column, tool=tool):
                self.assertEqual(column, retro.classify_failure(tool, body))
                self.assertEqual(kind, classify_failure_evidence("", body).kind)


if __name__ == "__main__":
    unittest.main()
