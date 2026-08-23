"""Codex rollout JSONL adapter."""

from __future__ import annotations

import json
from pathlib import Path

from .base import AdapterBase, AdapterResult, iter_jsonl, parse_timestamp
from ..schema import SCHEMA_VERSION, SpanKind, TraceRecord


class CodexAdapter(AdapterBase):
    source = "codex"

    def __init__(self, id_salt: bytes, excluded_session_ids=None,
                 handoff_tools=None, capabilities=None):
        super().__init__(id_salt)
        if handoff_tools is None or capabilities is None:
            from .registry import default_options_for
            defaults = default_options_for(self.source)
            handoff_tools = defaults["handoff_tools"] if handoff_tools is None else handoff_tools
            capabilities = defaults["capabilities"] if capabilities is None else capabilities
        self.excluded_session_ids = {
            str(value).lower() for value in (excluded_session_ids or ())
        }
        self.handoff_tools = frozenset(str(value) for value in handoff_tools)
        self.capabilities = self.parse_capabilities(capabilities)

    def read(self, path: Path, root: Path) -> AdapterResult:
        rows = list(iter_jsonl(path))
        meta = next((record.get("payload") for _, record in rows
                     if record.get("type") == "session_meta"
                     and isinstance(record.get("payload"), dict)), {})
        thread_source = meta.get("thread_source")
        session_id = str(meta.get("session_id") or meta.get("id") or "")
        if session_id.lower() in self.excluded_session_ids:
            return AdapterResult(
                source=self.source,
                included=False,
                exclusion_reason="active_or_explicitly_excluded",
                is_subagent=thread_source == "subagent",
                records=(),
            )
        if thread_source != "user":
            return AdapterResult(
                source=self.source,
                included=False,
                exclusion_reason="thread_source:%s" % thread_source,
                is_subagent=thread_source == "subagent",
                records=(),
            )

        trace_id = self.trace_id(path, root)
        source_version = str(meta.get("cli_version") or meta.get("version") or "")
        records = []
        human_prompts = tool_calls = tool_results = 0
        usage = {}
        first_at = last_at = None
        status = "open"
        duration_ms = 0
        for sequence, (_, record) in enumerate(rows):
            stamp = parse_timestamp(record.get("timestamp"))
            first_at = first_at or stamp
            last_at = stamp or last_at
            if record.get("type") == "event_msg":
                event = record.get("payload")
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "token_count":
                    info = event.get("info")
                    total = info.get("total_token_usage") if isinstance(info, dict) else None
                    if isinstance(total, dict):
                        usage = total
                elif event.get("type") == "task_complete":
                    status = "complete"
                    duration_ms = int(event.get("duration_ms") or 0)
                elif event.get("type") == "turn_aborted":
                    status = "aborted"
                    duration_ms = int(event.get("duration_ms") or 0)
                elif event.get("type") == "sub_agent_activity":
                    records.append(self._span(trace_id, sequence, SpanKind.AGENT,
                                              record, source_version, "agent"))
                continue
            if record.get("type") != "response_item":
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            item_type = payload.get("type")
            if item_type == "message" and payload.get("role") == "user":
                human_prompts += 1
                records.append(self._span(trace_id, sequence, SpanKind.PROMPT,
                                          record, source_version, "human"))
            elif item_type == "message" and payload.get("role") == "assistant":
                records.append(self._span(trace_id, sequence, SpanKind.LLM,
                                          record, source_version, "agent"))
            elif item_type in {"function_call", "custom_tool_call"}:
                tool_calls += 1
                tool = str(payload.get("name") or payload.get("tool_name") or "")
                raw = payload.get("arguments") or payload.get("input") or ""
                signature = self.ids.make(tool, json.dumps(raw, sort_keys=True, default=str))
                kind = SpanKind.HANDOFF if tool in self.handoff_tools else SpanKind.TOOL
                records.append(self._span(trace_id, sequence, kind,
                                          record, source_version, "agent", tool, signature))
            elif item_type in {"function_call_output", "custom_tool_call_output"}:
                tool_results += 1

        included = human_prompts > 0 and bool(records)
        if included:
            records.insert(0, TraceRecord(
                schema_version=SCHEMA_VERSION,
                trace_id=trace_id,
                span_id=self.ids.make(trace_id, "root"),
                parent_span_id=None,
                source=self.source,
                adapter_version=self.adapter_version,
                source_version=source_version,
                span_kind=SpanKind.TRACE,
                started_at=first_at,
                ended_at=last_at,
                sequence=0,
                status=status,
                input_tokens=int(usage.get("input_tokens") or 0),
                cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
                cache_write_input_tokens=int(usage.get("cache_write_input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                reasoning_output_tokens=int(usage.get("reasoning_output_tokens") or 0),
                duration_ms=duration_ms,
            ))
        return AdapterResult(
            source=self.source,
            included=included,
            exclusion_reason="" if included else "no direct-human prompt",
            is_subagent=False,
            records=tuple(records) if included else (),
            human_prompt_count=human_prompts,
            tool_call_count=tool_calls,
            tool_result_count=tool_results,
        )

    def private_tool_evidence(self, path: Path, root: Path, redactor):
        """Return redacted call evidence for external annotation only."""
        rows = list(iter_jsonl(path))
        meta = next((record.get("payload") for _, record in rows
                     if record.get("type") == "session_meta"
                     and isinstance(record.get("payload"), dict)), {})
        if meta.get("thread_source") != "user":
            return {}
        trace_id = self.trace_id(path, root)
        evidence = {}
        for sequence, (_, record) in enumerate(rows):
            if record.get("type") != "response_item":
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict) or payload.get("type") not in {
                    "function_call", "custom_tool_call"}:
                continue
            tool = str(payload.get("name") or payload.get("tool_name") or "")
            kind = SpanKind.HANDOFF if tool in self.handoff_tools else SpanKind.TOOL
            raw = payload.get("arguments") or payload.get("input") or ""
            shown = (json.dumps(raw, sort_keys=True, default=str)
                     if not isinstance(raw, str) else raw)
            span_id = self.ids.make(trace_id, sequence, kind.value)
            evidence[span_id] = {
                "tool_kind": tool,
                "tool_input": redactor(shown)[:1200],
            }
        return evidence

    def _span(self, trace_id, sequence, kind, record, source_version, actor,
              tool="", signature=""):
        return TraceRecord(
            schema_version=SCHEMA_VERSION,
            trace_id=trace_id,
            span_id=self.ids.make(trace_id, sequence, kind.value),
            parent_span_id=self.ids.make(trace_id, "root"),
            source=self.source,
            adapter_version=self.adapter_version,
            source_version=source_version,
            span_kind=kind,
            started_at=parse_timestamp(record.get("timestamp")),
            sequence=sequence,
            actor_kind=actor,
            tool_kind=tool,
            call_signature=signature,
        )
