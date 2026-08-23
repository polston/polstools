"""Deterministic scorers over normalized, content-free trace records."""

from __future__ import annotations

import time
import importlib
from collections import Counter

from .schema import SpanKind
from .scoring import ScoreResult
from .statistics import wilson_interval
from .statistics import paired_effect_interval
from .taxonomies import load_tool_taxonomy, repeated_call_candidates


def _kind(record):
    return record.span_kind.value if isinstance(record.span_kind, SpanKind) else record.span_kind


class _RateScorer:
    version = 1
    scope = "trace_set"
    required_capabilities = ()

    def __init__(self, scorer_id, minimum_n, max_evidence_refs=None):
        if minimum_n < 1:
            raise ValueError("minimum_n must be positive")
        if max_evidence_refs is not None and max_evidence_refs < 0:
            raise ValueError("max_evidence_refs cannot be negative")
        self.scorer_id = scorer_id
        self.minimum_n = minimum_n
        self.max_evidence_refs = max_evidence_refs

    def counts(self, records):
        raise NotImplementedError

    def score(self, records):
        started = time.perf_counter()
        numerator, denominator, population, evidence, limitations, details = self.counts(records)
        evidence = tuple(evidence[:self.max_evidence_refs]
                         if self.max_evidence_refs is not None else evidence)
        if denominator < self.minimum_n:
            return ScoreResult(
                scorer_id=self.scorer_id, scorer_version=self.version, scope=self.scope,
                value=None, label="insufficient_evidence", abstained=True,
                reason="eligible population below configured minimum",
                evidence_refs=evidence, population=population,
                eligible_population=denominator,
                latency_ms=int((time.perf_counter() - started) * 1000),
                estimated_cost=0.0, limitations=tuple(limitations),
                details=details,
            )
        low, high = wilson_interval(numerator, denominator)
        return ScoreResult(
            scorer_id=self.scorer_id, scorer_version=self.version, scope=self.scope,
            value=numerator / denominator, label="measured", abstained=False,
            reason="deterministic normalized-trace count",
            evidence_refs=evidence, population=population,
            eligible_population=denominator,
            latency_ms=int((time.perf_counter() - started) * 1000),
            estimated_cost=0.0, limitations=tuple(limitations),
            numerator=numerator, interval_low=low, interval_high=high,
            uncertainty_method="Wilson 95% interval",
            details=details,
        )


class RepeatedCallScorer(_RateScorer):
    required_capabilities = ("tool_trajectory",)

    def __init__(self, scorer_id, minimum_n, max_evidence_refs=None,
                 taxonomy=None):
        super().__init__(scorer_id, minimum_n, max_evidence_refs)
        self.taxonomy = taxonomy or load_tool_taxonomy()
        self.version = self.taxonomy.repeated_call_version

    def counts(self, records):
        by_trace = {}
        all_tools = 0
        for record in records:
            if _kind(record) != SpanKind.TOOL.value:
                continue
            all_tools += 1
            by_trace.setdefault(record.trace_id, []).append(record)
        eligible = [span for spans in by_trace.values() if len(spans) >= 2 for span in spans]
        duplicates = 0
        duplicate_by_tool = Counter()
        evidence = []
        repeat_classes = Counter()
        diagnosis_reasons = Counter()
        for candidate in repeated_call_candidates(records, self.taxonomy):
            duplicates += 1
            duplicate_by_tool[candidate.current.tool_kind] += 1
            evidence.append(candidate.current.span_id)
            repeat_classes[candidate.candidate_class] += 1
            diagnosis_reasons[candidate.reason_code] += 1
        taxonomy = {
            name: {"numerator": repeat_classes[name],
                   "eligible_population": duplicates}
            for name in self.taxonomy.repeated_call_classes
        }
        return duplicates, len(eligible), all_tools, evidence, (
            "identical calls can be legitimate after a state change",
            "candidate waste is uncalibrated and barred from decision support",
        ), {"duplicate_by_tool": dict(sorted(duplicate_by_tool.items())),
            "repeat_taxonomy": taxonomy,
            "diagnosis_reasons": dict(sorted(diagnosis_reasons.items())),
            "validation_status": "unvalidated",
            "decision_support": False}


class SkillLifecycleScorer(_RateScorer):
    version = 2
    required_capabilities = ("skill_lifecycle",)

    def counts(self, records):
        traces = [record for record in records
                  if _kind(record) == SpanKind.TRACE.value]
        starts = [record for record in records
                  if _kind(record) == "retro.skill.start"]
        ends = [record for record in records
                if _kind(record) == "retro.skill.end"]
        start_ids = {str(record.attributes.get("invocation_id") or "")
                     for record in starts}
        end_ids = {str(record.attributes.get("invocation_id") or "")
                   for record in ends}
        matched = {value for value in start_ids & end_ids if value}
        traces_with_starts = {record.trace_id for record in starts}
        outcomes = Counter(str(record.attributes.get("outcome")
                               or "not_observable") for record in ends)
        unmatched_starts = max(0, len(start_ids - matched))
        unmatched_terminals = max(0, len(end_ids - matched))
        details = {
            "starts": len(starts),
            "ends": len(ends),
            "matched_terminals": len(matched),
            "unmatched_starts": unmatched_starts,
            "unmatched_terminals": unmatched_terminals,
            "unmatched_start_rate": (
                unmatched_starts / len(start_ids) if start_ids else 0.0),
            "unmatched_terminal_rate": (
                unmatched_starts / len(start_ids) if start_ids else 0.0),
            "orphan_terminal_rate": (
                unmatched_terminals / len(end_ids) if end_ids else 0.0),
            "lifecycle_completion_rate": (
                len(matched) / len(start_ids) if start_ids else 0.0),
            "outcomes": dict(sorted(outcomes.items())),
            "explicit_chain_steps": sum(
                _kind(record) == "retro.skill.chain_step" for record in records),
            "opportunity_rate": "not_observable",
            "missed_trigger_rate": "not_observable",
        }
        return (len(traces_with_starts), len(traces), len(traces),
                [record.span_id for record in starts], (
                    "only explicit attribution boundaries are measured",
                    "skill opportunities and missed triggers are not observable",
                    "outcomes remain not_observable without an explicit source field",
                ), details)


class VerifiedOutcomeScorer(_RateScorer):
    required_capabilities = ("outcomes",)

    def counts(self, records):
        traces = [record for record in records if _kind(record) == SpanKind.TRACE.value]
        complete = [record for record in traces if record.status == "complete"]
        return len(complete), len(traces), len(traces), [item.span_id for item in complete], (
            "source completion is not equivalent to subjective quality",
        ), {"status_counts": dict(sorted(Counter(item.status for item in traces).items()))}


class ToolFailureScorer(_RateScorer):
    required_capabilities = ("tool_result_status",)

    def __init__(self, scorer_id, minimum_n, max_evidence_refs=None,
                 taxonomy=None):
        super().__init__(scorer_id, minimum_n, max_evidence_refs)
        self.taxonomy = taxonomy or load_tool_taxonomy()
        self.version = self.taxonomy.tool_failure_version

    def counts(self, records):
        results = [record for record in records if _kind(record) == "retro.tool_result"]
        failures = [record for record in results if record.status == "error"]
        results_by_tool = Counter(record.tool_kind for record in results)
        by_tool = Counter(record.tool_kind for record in failures)
        failure_kind = Counter(self.taxonomy.failure_kind(
            record.attributes.get("error_kind")) for record in failures)
        failure_reason = Counter(
            str(record.attributes.get("error_reason") or (
                "source_unclassified"
                if self.taxonomy.failure_kind(
                    record.attributes.get("error_kind")) == "unknown"
                else "source_" + self.taxonomy.failure_kind(
                    record.attributes.get("error_kind"))))
            for record in failures)
        return len(failures), len(results), len(results), [item.span_id for item in failures], (
            "authoritative source failures can include expected negative probes",
            "failure taxonomy is unvalidated and barred from decision support",
        ), {"by_tool": {tool: {"failures": by_tool[tool], "results": count}
                         for tool, count in sorted(results_by_tool.items())},
            "failure_kind": dict(sorted(failure_kind.items())),
            "failure_reason": dict(sorted(failure_reason.items())),
            "validation_status": "unvalidated",
            "decision_support": False}


class InputTokensPerOutcomeScorer:
    version = 1
    scope = "trace_set"
    required_capabilities = ("usage", "outcomes")

    def __init__(self, scorer_id, minimum_n, interval_backend, confidence,
                 seed, max_evidence_refs=None):
        self.scorer_id = scorer_id
        self.minimum_n = minimum_n
        self.confidence = confidence
        self.seed = seed
        self.max_evidence_refs = max_evidence_refs
        if isinstance(interval_backend, dict):
            backend_class = getattr(
                importlib.import_module(str(interval_backend["module"])),
                str(interval_backend["class"]),
            )
            self.interval_backend = backend_class(**dict(interval_backend.get("options") or {}))
        else:
            self.interval_backend = interval_backend

    def score(self, records):
        started = time.perf_counter()
        traces = [record for record in records
                  if _kind(record) == SpanKind.TRACE.value]
        eligible = [record for record in traces
                    if record.status == "complete" and record.input_tokens > 0]
        evidence = [record.span_id for record in eligible]
        if self.max_evidence_refs is not None:
            evidence = evidence[:self.max_evidence_refs]
        if len(eligible) < self.minimum_n:
            return ScoreResult(
                scorer_id=self.scorer_id, scorer_version=self.version,
                scope=self.scope, value=None, label="insufficient_evidence",
                abstained=True, reason="eligible population below configured minimum",
                evidence_refs=tuple(evidence), population=len(traces),
                eligible_population=len(eligible),
                latency_ms=int((time.perf_counter() - started) * 1000),
                estimated_cost=0.0,
                limitations=("zero-token traces are not observable for this metric",),
            )
        values = [record.input_tokens for record in eligible]
        try:
            effect = paired_effect_interval(
                [0] * len(values), values, backend=self.interval_backend,
                statistic="mean", confidence=self.confidence, seed=self.seed)
        except RuntimeError as exc:
            return ScoreResult(
                scorer_id=self.scorer_id, scorer_version=self.version,
                scope=self.scope, value=None, label="dependency_unavailable",
                abstained=True, reason=str(exc), evidence_refs=tuple(evidence),
                population=len(traces), eligible_population=len(eligible),
                latency_ms=int((time.perf_counter() - started) * 1000),
                estimated_cost=0.0,
                limitations=("configured uncertainty backend is unavailable",),
            )
        return ScoreResult(
            scorer_id=self.scorer_id, scorer_version=self.version,
            scope=self.scope, value=effect["effect"], label="measured",
            abstained=False, reason="completed traces with observable usage",
            evidence_refs=tuple(evidence), population=len(traces),
            eligible_population=len(eligible),
            latency_ms=int((time.perf_counter() - started) * 1000),
            estimated_cost=0.0,
            limitations=("task difficulty is not yet matched",),
            numerator=sum(values), interval_low=effect["interval"][0],
            interval_high=effect["interval"][1],
            uncertainty_method=effect["backend"],
        )
