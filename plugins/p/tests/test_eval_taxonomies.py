"""Versioned repeated-call and tool-failure taxonomy contracts."""

import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.deterministic_scorers import (  # noqa: E402
    RepeatedCallScorer, ToolFailureScorer,
)
from retro_eval.schema import SpanKind, TraceRecord  # noqa: E402
from retro_eval.taxonomies import (  # noqa: E402
    classify_failure_evidence, load_tool_taxonomy,
)


def record(sequence, kind, *, signature="", tool="", status="ok", attributes=None):
    return TraceRecord(
        schema_version=1, trace_id="a" * 24,
        span_id=("%024x" % (sequence + 1)), parent_span_id=None,
        source="codex", adapter_version=1, source_version="1",
        span_kind=kind, started_at=None, sequence=sequence,
        call_signature=signature, tool_kind=tool, status=status,
        attributes=dict(attributes or {}),
    )


class ToolTaxonomyTests(unittest.TestCase):
    def test_missing_signatures_do_not_establish_repeated_calls(self):
        for second_tool in ("Read", "Edit"):
            result = RepeatedCallScorer("repeat", minimum_n=1).score([
                record(1, SpanKind.TOOL, tool="Read"),
                record(2, SpanKind.TOOL, tool=second_tool)])
            self.assertTrue(result.abstained)
            self.assertEqual(0, result.numerator)
            self.assertEqual(0, result.eligible_population)
            self.assertEqual(2, result.population)
            self.assertEqual(2, result.details["missing_call_signatures"])
        result = RepeatedCallScorer("repeat", minimum_n=1).score([
            record(1, SpanKind.TOOL, signature="known", tool="Read"),
            record(2, SpanKind.TOOL, tool="Read")])
        self.assertTrue(result.abstained)
        self.assertEqual(0, result.eligible_population)

    def test_unknown_signature_preserves_intervening_mutation_context(self):
        result = RepeatedCallScorer("repeat", minimum_n=2).score([
            record(1, SpanKind.TOOL, signature="same", tool="Read"),
            record(2, SpanKind.TOOL, tool="Edit"),
            record(3, SpanKind.TOOL, signature="same", tool="Read")])
        self.assertEqual(.5, result.value)
        self.assertEqual(2, result.eligible_population)
        self.assertEqual(3, result.population)
        self.assertEqual(1, result.details["missing_call_signatures"])
        self.assertEqual(1, result.details["repeat_taxonomy"]["post_state_change"]["numerator"])
        self.assertFalse(result.details["decision_support"])

    def test_native_file_mutations_explain_repeated_verification(self):
        for tool in ("replace_file_content", "write_to_file", "Edit", "apply_patch",
                     "edit_file", "write_file"):
            with self.subTest(tool=tool):
                result = RepeatedCallScorer("repeat", minimum_n=1).score([
                    record(1, SpanKind.TOOL, signature="test", tool="run_command"),
                    record(2, SpanKind.TOOL, signature="change", tool=tool),
                    record(3, SpanKind.TOOL, signature="test", tool="run_command")])
                self.assertEqual(1, result.numerator)
                self.assertEqual(1, result.details["repeat_taxonomy"]["post_state_change"]["numerator"])
                self.assertEqual(0, result.details["repeat_taxonomy"]["candidate_waste"]["numerator"])
                self.assertFalse(result.details["decision_support"])
        for intervening in ([], [record(2, SpanKind.TOOL, signature="read", tool="view_file")]):
            result = RepeatedCallScorer("repeat", minimum_n=1).score([
                record(1, SpanKind.TOOL, signature="test", tool="run_command"),
                *intervening,
                record(3, SpanKind.TOOL, signature="test", tool="run_command")])
            self.assertEqual(1, result.details["repeat_taxonomy"]["candidate_waste"]["numerator"])

    def test_timed_wait_is_polling_without_hiding_generic_task_operations(self):
        for tool, expected in (("sleep", "polling"), ("manage_task", "candidate_waste"),
                               ("schedule", "candidate_waste")):
            with self.subTest(tool=tool):
                result = RepeatedCallScorer("repeat", minimum_n=1).score([
                    record(1, SpanKind.TOOL, signature="same", tool=tool),
                    record(2, SpanKind.TOOL, signature="same", tool=tool)])
                self.assertEqual(1, result.details["repeat_taxonomy"][expected]["numerator"])

    def test_profile_is_versioned_and_declares_all_candidate_classes(self):
        profile = load_tool_taxonomy()
        self.assertEqual(1, profile.schema_version)
        self.assertEqual(7, profile.repeated_call_version)
        self.assertEqual(4, profile.tool_failure_version)
        self.assertEqual(
            {"polling", "post_state_change", "candidate_waste"},
            set(profile.repeated_call_classes),
        )
        self.assertIn("unknown", profile.failure_kinds)

    def test_repeated_call_v2_separates_candidate_denominators(self):
        records = [
            record(1, SpanKind.TOOL, signature="poll", tool="wait_agent"),
            record(2, SpanKind.TOOL, signature="poll", tool="wait_agent"),
            record(3, SpanKind.TOOL, signature="read", tool="Read"),
            record(4, SpanKind.TOOL, signature="mutate", tool="Edit"),
            record(5, SpanKind.TOOL, signature="read", tool="Read"),
            record(6, SpanKind.TOOL, signature="same", tool="Read"),
            record(7, SpanKind.TOOL, signature="same", tool="Read"),
        ]
        result = RepeatedCallScorer(
            "repeated_call_rate", minimum_n=1).score(records)
        taxonomy = result.details["repeat_taxonomy"]

        self.assertEqual(8, result.scorer_version)
        self.assertEqual(7, result.details["candidate_taxonomy_version"])
        self.assertEqual(3, result.numerator)
        self.assertEqual(7, result.eligible_population)
        self.assertEqual(
            {"numerator": 1, "eligible_population": 3}, taxonomy["polling"])
        self.assertEqual(
            {"numerator": 1, "eligible_population": 3},
            taxonomy["post_state_change"])
        self.assertEqual(
            {"numerator": 1, "eligible_population": 3},
            taxonomy["candidate_waste"])
        self.assertEqual(
            {"identical_signature_no_observed_change": 1,
             "intervening_mutation": 1, "polling_tool": 1},
            result.details["diagnosis_reasons"])
        self.assertFalse(result.details["decision_support"])
        self.assertIn("uncalibrated", " ".join(result.limitations))

    def test_tool_failure_v2_preserves_unknown_and_structured_kinds(self):
        records = [
            record(1, "retro.tool_result", tool="Read", status="error",
                   attributes={"error_kind": "missing_target"}),
            record(2, "retro.tool_result", tool="Bash", status="error"),
            record(3, "retro.tool_result", tool="Read", status="ok"),
        ]
        result = ToolFailureScorer(
            "tool_failure_rate", minimum_n=1).score(records)

        self.assertEqual(5, result.scorer_version)
        self.assertEqual(4, result.details["aggregation_taxonomy_version"])
        self.assertEqual(2, result.numerator)
        self.assertEqual(
            {"missing_target": 1, "unknown": 1},
            result.details["failure_kind"],
        )
        self.assertEqual(
            {"source_missing_target": 1, "source_unclassified": 1},
            result.details["failure_reason"])
        self.assertFalse(result.details["decision_support"])
        self.assertIn("unvalidated", " ".join(result.limitations))

    def test_failure_diagnosis_versions_preserve_legacy_and_unknown(self):
        records = [record(i, "retro.tool_result", tool="Bash", status="error",
                          attributes={"error_kind": "missing_target",
                                      "failure_taxonomy_version": version})
                   for i, version in enumerate((3, 4, None, True, "4", 0), 1)]
        result = ToolFailureScorer("tool_failure_rate", minimum_n=1).score(records)
        self.assertEqual({"3": 1, "4": 1, "unknown": 4},
                         result.details["failure_diagnosis_versions"])
        self.assertEqual({"missing_target": 6}, result.details["failure_kind"])
        self.assertEqual(6, result.numerator)

    def test_failure_causes_require_result_evidence_and_hook_refusals_win(self):
        cases = (
            ("grep status example.txt", "PreToolUse:Bash hook error: command blocked - policy constraint", "control_refusal"),
            ("grep status example.txt", "PreToolUse:Bash hook error: Function definitions are not allowed", "control_refusal"),
            ("grep 'not found' example.txt", "Exit code 1", "expected_probe"),
            ("grep 'permission' example.txt", "Exit code 1", "expected_probe"),
            ("echo 'usage:'; build", "Compiler exited 2", "execution_failure"),
            ("grep example missing.txt", "No such file or directory", "missing_target"),
            ("grep example file.txt", "", "unknown"),
        )
        for tool_input, result, expected in cases:
            with self.subTest(expected=expected, result=result):
                self.assertEqual(expected, classify_failure_evidence(tool_input, result).kind)

    def test_failure_diagnosis_is_conservative_and_reason_coded(self):
        cases = (
            ("", "Refusing to run outside the worktree",
             "control_refusal", "source_control_refusal"),
            ("", "No such file or directory",
             "missing_target", "source_missing_target"),
            ("", "Syntax error near unexpected token",
             "malformed_input", "source_malformed_input"),
            ("Get-Command absent", "Exit code 1",
             "expected_probe", "source_expected_probe"),
            ("build project", "compiler exited 2",
             "execution_failure", "source_execution_failure"),
            ("", "", "unknown", "source_unclassified"),
        )
        for tool_input, tool_result, kind, reason in cases:
            with self.subTest(kind=kind):
                diagnosis = classify_failure_evidence(tool_input, tool_result)
                self.assertEqual(kind, diagnosis.kind)
                self.assertEqual(reason, diagnosis.reason_code)


if __name__ == "__main__":
    unittest.main()
