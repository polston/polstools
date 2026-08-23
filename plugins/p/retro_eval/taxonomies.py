"""Versioned, data-driven tool evaluation taxonomies."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .schema import SpanKind


_FAILURE_PATTERNS = (
    ("control_refusal", "source_control_refusal", re.compile(
        r"\b(refus(?:e|ed|ing)|permission|approval|sandbox|access is denied)\b|"
        r"outside.{0,40}worktree", re.I | re.S)),
    ("missing_target", "source_missing_target", re.compile(
        r"\b(no such file|not found|cannot find|does not exist|missing target)\b",
        re.I)),
    ("malformed_input", "source_malformed_input", re.compile(
        r"\b(syntax error|parse error|invalid (?:argument|option|syntax)|"
        r"unrecogni[sz]ed (?:argument|option)|unexpected token|usage:)\b", re.I)),
)
_EXPECTED_PROBE = re.compile(
    r"\b(get-command|test-path|which|where\.exe|rg|grep)\b|"
    r"\bgit\s+(?:diff|status)\b.{0,80}\b--quiet\b|"
    r"\btest\s+-[defx]\b", re.I | re.S)


@dataclass(frozen=True)
class FailureDiagnosis:
    kind: str
    reason_code: str


def classify_failure_evidence(tool_input, tool_result) -> FailureDiagnosis:
    """Classify source evidence conservatively without retaining its content."""
    tool_input = str(tool_input or "")
    tool_result = str(tool_result or "")
    combined = tool_input + "\n" + tool_result
    if not combined.strip():
        return FailureDiagnosis("unknown", "source_unclassified")
    for kind, reason, pattern in _FAILURE_PATTERNS:
        if pattern.search(combined):
            return FailureDiagnosis(kind, reason)
    if _EXPECTED_PROBE.search(tool_input):
        return FailureDiagnosis("expected_probe", "source_expected_probe")
    return FailureDiagnosis("execution_failure", "source_execution_failure")


@dataclass(frozen=True)
class ToolTaxonomy:
    schema_version: int
    repeated_call_version: int
    repeated_call_classes: tuple[str, ...]
    polling_tools: frozenset[str]
    mutation_tools: frozenset[str]
    tool_failure_version: int
    failure_kinds: tuple[str, ...]

    def __post_init__(self):
        if self.schema_version != 1:
            raise ValueError("unsupported tool taxonomy schema")
        if self.repeated_call_version < 2 or self.tool_failure_version < 2:
            raise ValueError("tool taxonomy requires versioned v2 scorers")
        required = {"polling", "post_state_change", "candidate_waste"}
        if set(self.repeated_call_classes) != required:
            raise ValueError("repeated-call classes are incomplete")
        if "unknown" not in self.failure_kinds:
            raise ValueError("tool-failure kinds require unknown")
        if len(set(self.failure_kinds)) != len(self.failure_kinds):
            raise ValueError("tool-failure kinds must be unique")

    def is_polling(self, tool_kind: str) -> bool:
        return str(tool_kind).casefold() in self.polling_tools

    def is_mutation(self, tool_kind: str) -> bool:
        return str(tool_kind).casefold() in self.mutation_tools

    def failure_kind(self, value: object) -> str:
        kind = str(value or "unknown")
        return kind if kind in self.failure_kinds else "unknown"


@dataclass(frozen=True)
class RepeatCandidate:
    current: object
    previous: object
    candidate_class: str
    intervening_tools: tuple[str, ...]
    reason_code: str


def repeated_call_candidates(records, taxonomy: ToolTaxonomy | None = None):
    """Return each repeated call once with its structural candidate class."""
    taxonomy = taxonomy or load_tool_taxonomy()
    by_trace = {}
    for record in records:
        kind = (record.span_kind.value if isinstance(record.span_kind, SpanKind)
                else record.span_kind)
        if kind == SpanKind.TOOL.value:
            by_trace.setdefault(record.trace_id, []).append(record)
    candidates = []
    for spans in by_trace.values():
        spans = sorted(spans, key=lambda item: item.sequence)
        previous = {}
        for index, span in enumerate(spans):
            prior_index = previous.get(span.call_signature)
            if prior_index is not None:
                intervening = tuple(item.tool_kind
                                    for item in spans[prior_index + 1:index])
                if taxonomy.is_polling(span.tool_kind):
                    candidate_class = "polling"
                    reason_code = "polling_tool"
                elif any(taxonomy.is_mutation(tool) for tool in intervening):
                    candidate_class = "post_state_change"
                    reason_code = "intervening_mutation"
                else:
                    candidate_class = "candidate_waste"
                    reason_code = "identical_signature_no_observed_change"
                candidates.append(RepeatCandidate(
                    current=span, previous=spans[prior_index],
                    candidate_class=candidate_class,
                    intervening_tools=intervening, reason_code=reason_code))
            previous[span.call_signature] = index
    return tuple(candidates)


def load_tool_taxonomy(path: Path | None = None) -> ToolTaxonomy:
    path = path or Path(__file__).resolve().parents[1] / "profiles" / "tool-taxonomy.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        repeated = payload["repeated_call"]
        failure = payload["tool_failure"]
        return ToolTaxonomy(
            schema_version=int(payload["schema_version"]),
            repeated_call_version=int(repeated["version"]),
            repeated_call_classes=tuple(str(item) for item in repeated["classes"]),
            polling_tools=frozenset(str(item).casefold()
                                    for item in repeated["polling_tools"]),
            mutation_tools=frozenset(str(item).casefold()
                                     for item in repeated["mutation_tools"]),
            tool_failure_version=int(failure["version"]),
            failure_kinds=tuple(str(item) for item in failure["kinds"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid tool taxonomy profile") from exc
