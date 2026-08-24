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
    def test_profile_is_versioned_and_declares_all_candidate_classes(self):
        profile = load_tool_taxonomy()
        self.assertEqual(1, profile.schema_version)
        self.assertEqual(4, profile.repeated_call_version)
        self.assertEqual(3, profile.tool_failure_version)
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

        self.assertEqual(4, result.scorer_version)
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

        self.assertEqual(3, result.scorer_version)
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
