"""Antigravity step-export adapter.

An export is a JSONL stream of step snapshots under
``<root>/<session>/.system_generated/logs/``. A step can be written several
times while it runs; the latest valid snapshot of each ``step_index`` wins.
The export carries no authoritative main/subagent marker, no per-call tool
result status, and no reliable token accounting, so those stay unobservable
rather than being inferred from prose, paths, or GENERIC steps.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

from .base import (AdapterBase, AdapterResult, iter_jsonl, parse_timestamp,
                   prompt_evidence)
from ..schema import SCHEMA_VERSION, SpanKind, TraceRecord
from ..text_rules import evidence_excerpt

FULL_EXPORT = "transcript_full.jsonl"
SHORT_EXPORT = "transcript.jsonl"


def discover(root: Path, *, stopped=lambda: False, on_error=None):
    """One export per session: the full export when present, else the short one."""
    # glob/is_file can suppress permission errors, making inaccessible exports
    # look like an empty corpus. Missing paths are ordinary; unreadable ones
    # must reach the caller's coverage handling.
    for session in sorted(Path(root).iterdir()):
        if stopped():
            return
        logs = session / '.system_generated' / 'logs'
        for filename in (FULL_EXPORT, SHORT_EXPORT):
            path = logs / filename
            try:
                mode = path.stat().st_mode
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError as exc:
                if on_error is None:
                    raise
                on_error(exc)
                break
            if stat.S_ISREG(mode):
                yield path
                break


def _valid_step(record) -> bool:
    index = record.get("step_index")
    return (isinstance(index, int) and not isinstance(index, bool) and index >= 0
            and isinstance(record.get("type"), str) and bool(record["type"])
            and isinstance(record.get("source"), str) and bool(record["source"]))


def latest_steps(path: Path):
    steps = {}
    for _, record in iter_jsonl(path):
        if _valid_step(record):
            steps[record["step_index"]] = record
    return [steps[index] for index in sorted(steps)]


def _text(record) -> str:
    value = record.get("content")
    return value if isinstance(value, str) else ""


class AntigravityAdapter(AdapterBase):
    source = "antigravity"
    adapter_version = 5

    def __init__(self, id_salt: bytes, excluded_session_ids=None, capabilities=None):
        super().__init__(id_salt)
        if capabilities is None:
            from .registry import default_options_for
            capabilities = default_options_for(self.source)["capabilities"]
        self.excluded_session_ids = {
            str(value).lower() for value in (excluded_session_ids or ())
        }
        self.capabilities = self.parse_capabilities(capabilities)

    @staticmethod
    def session_id(path: Path, root: Path) -> str:
        try:
            return path.resolve().relative_to(root.resolve()).parts[0]
        except (ValueError, IndexError):
            return ""

    def _excluded(self, reason):
        return AdapterResult(source=self.source, included=False,
                             exclusion_reason=reason, is_subagent=False, records=())

    def read(self, path: Path, root: Path) -> AdapterResult:
        if self.session_id(path, root).lower() in self.excluded_session_ids:
            return self._excluded("active_or_explicitly_excluded")
        trace_id = self.trace_id(path, root)
        records = []
        human_prompts = tool_calls = 0
        first_at = last_at = None
        last_model_text = ""
        tool_sequence = -1
        for step in latest_steps(path):
            stamp = parse_timestamp(step.get("created_at"))
            if stamp is not None:
                first_at = min(first_at, stamp) if first_at else stamp
                last_at = max(last_at, stamp) if last_at else stamp
            kind, source = step["type"], step["source"]
            sequence = step["step_index"]
            if kind == "USER_INPUT" and source == "USER_EXPLICIT" \
                    and _text(step).strip():
                human_prompts += 1
                last_model_text = ""
                records.append(self._span(trace_id, sequence, SpanKind.PROMPT,
                                          step, "human"))
            elif kind == "PLANNER_RESPONSE" and source == "MODEL":
                calls = step.get("tool_calls")
                calls = calls if isinstance(calls, list) else []
                if not _text(step).strip() and not calls:
                    continue
                last_model_text = _text(step)
                records.append(self._span(trace_id, sequence, SpanKind.LLM,
                                          step, "agent"))
                for index, call in enumerate(calls):
                    if not isinstance(call, dict) or not isinstance(call.get("name"), str):
                        continue
                    tool_calls += 1
                    signature = (self.ids.make(call["name"], json.dumps(
                        call["args"], sort_keys=True, default=str))
                        if call["name"].strip() and call.get("args") is not None else "")
                    tool_sequence = self.indexed_sequence(tool_sequence, sequence, index)
                    records.append(self._span(
                        trace_id, tool_sequence, SpanKind.TOOL, step,
                        "agent", call["name"], signature,
                        span_id=self.indexed_span_id(trace_id, sequence, index, SpanKind.TOOL)))
            elif kind == "ERROR_MESSAGE":
                # A source-level error step, not attributable to any one call.
                records.append(self._span(
                    trace_id, sequence, "retro.source_error", step, "system",
                    status="error"))
        if not human_prompts:
            return self._excluded("no direct-human prompt")
        records.insert(0, TraceRecord(
            schema_version=SCHEMA_VERSION, trace_id=trace_id,
            span_id=self.ids.make(trace_id, "root"), parent_span_id=None,
            source=self.source, adapter_version=self.adapter_version,
            source_version="", span_kind=SpanKind.TRACE,
            started_at=first_at, ended_at=last_at, sequence=0,
            status="complete" if last_model_text.strip() else "open",
            main_or_subagent="unknown",
            duration_ms=(int((last_at - first_at).total_seconds() * 1000)
                         if first_at and last_at else 0),
            attributes={"usage": "not_observable", "population": "not_observable"},
        ))
        return AdapterResult(
            source=self.source, included=True, exclusion_reason="",
            is_subagent=False, records=tuple(records),
            human_prompt_count=human_prompts, tool_call_count=tool_calls,
            tool_result_count=0, skills=(), population_observable=False,
        )

    def private_prompt_evidence(self, path: Path, root: Path, redactor):
        """Redacted direct-human prompts keyed by their PROMPT span id.

        Walks the same latest-snapshot steps as read(). External annotation
        use only.
        """
        if self.session_id(path, root).lower() in self.excluded_session_ids:
            return {}
        trace_id = self.trace_id(path, root)
        evidence = {}
        context = []
        for step in latest_steps(path):
            kind, source = step["type"], step["source"]
            if kind == "PLANNER_RESPONSE" and source == "MODEL" and _text(step):
                context.append(_text(step))
            elif kind == "USER_INPUT" and source == "USER_EXPLICIT" \
                    and _text(step).strip():
                span_id = self.ids.make(trace_id, step["step_index"],
                                        SpanKind.PROMPT.value)
                evidence[span_id] = prompt_evidence(redactor, _text(step), context)
                context = []
        return evidence

    def private_tool_evidence(self, path: Path, root: Path, redactor):
        """Observable call context only; exports do not bind results to calls."""
        if self.session_id(path, root).lower() in self.excluded_session_ids:
            return {}
        trace_id = self.trace_id(path, root)
        evidence = {}
        recent_text = ""
        for step in latest_steps(path):
            if step["type"] == "USER_INPUT":
                recent_text = ""
            if step["type"] != "PLANNER_RESPONSE" or step["source"] != "MODEL":
                continue
            text = _text(step).strip()
            context = evidence_excerpt(redactor(text), 1200, tail_only=True) if text else ""
            calls = step.get("tool_calls")
            calls = calls if isinstance(calls, list) else []
            for index, call in enumerate(calls):
                if not isinstance(call, dict) or not isinstance(call.get("name"), str):
                    continue
                details = {"tool_kind": call["name"]}
                if call.get("args") is not None:
                    details["tool_input"] = evidence_excerpt(redactor(json.dumps(
                        call["args"], sort_keys=True, default=str)), 1200)
                if context:
                    details["intent_context"] = "Assistant text from same step: " + context
                elif recent_text:
                    details["intent_context"] = (
                        "Text from previous assistant step; call purpose not established: "
                        + recent_text)
                span_id = self.indexed_span_id(trace_id, step["step_index"], index,
                                               SpanKind.TOOL)
                evidence[span_id] = details
            if context:
                recent_text = context
        return evidence

    def _span(self, trace_id, sequence, kind, step, actor, tool="", signature="",
              status="ok", span_id=None):
        kind_value = kind.value if isinstance(kind, SpanKind) else str(kind)
        return TraceRecord(
            schema_version=SCHEMA_VERSION, trace_id=trace_id,
            span_id=span_id or self.ids.make(trace_id, sequence, kind_value),
            parent_span_id=self.ids.make(trace_id, "root"),
            source=self.source, adapter_version=self.adapter_version,
            source_version="", span_kind=kind,
            started_at=parse_timestamp(step.get("created_at")),
            sequence=sequence, status=status, actor_kind=actor,
            main_or_subagent="unknown", tool_kind=tool, call_signature=signature,
        )
