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
from pathlib import Path

from .base import AdapterBase, AdapterResult, iter_jsonl, parse_timestamp
from ..schema import SCHEMA_VERSION, SpanKind, TraceRecord

FULL_EXPORT = "transcript_full.jsonl"
SHORT_EXPORT = "transcript.jsonl"


def discover(root: Path):
    """One export per session: the full export when present, else the short one."""
    for logs in sorted(Path(root).glob("*/.system_generated/logs")):
        full = logs / FULL_EXPORT
        short = logs / SHORT_EXPORT
        if full.is_file():
            yield full
        elif short.is_file():
            yield short


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
    adapter_version = 1

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
                    signature = self.ids.make(call["name"], json.dumps(
                        call.get("args"), sort_keys=True, default=str))
                    records.append(self._span(
                        trace_id, sequence * 1000 + index, SpanKind.TOOL, step,
                        "agent", call["name"], signature))
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

    def _span(self, trace_id, sequence, kind, step, actor, tool="", signature="",
              status="ok"):
        kind_value = kind.value if isinstance(kind, SpanKind) else str(kind)
        return TraceRecord(
            schema_version=SCHEMA_VERSION, trace_id=trace_id,
            span_id=self.ids.make(trace_id, sequence, kind_value),
            parent_span_id=self.ids.make(trace_id, "root"),
            source=self.source, adapter_version=self.adapter_version,
            source_version="", span_kind=kind,
            started_at=parse_timestamp(step.get("created_at")),
            sequence=sequence, status=status, actor_kind=actor,
            main_or_subagent="unknown", tool_kind=tool, call_signature=signature,
        )
