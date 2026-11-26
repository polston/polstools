"""Codex rollout JSONL adapter."""

from __future__ import annotations

import json
from pathlib import Path

from .base import (AdapterBase, AdapterResult, iter_jsonl, parse_timestamp,
                   prompt_evidence)
from ..text_rules import (codex_machine_prompt_origin, codex_question_reply,
                          evidence_excerpt)
from ..schema import SCHEMA_VERSION, SpanKind, TraceRecord


def _message_text(payload):
    content = payload.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text") or "").strip()
        for block in content
        if isinstance(block, dict)
        and block.get("type") in {"input_text", "output_text", "text"}
        and str(block.get("text") or "").strip())


def _canonical_arguments(raw):
    """Serialize call input so equivalent JSON argument strings sign alike."""
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except ValueError:
            pass
    return json.dumps(raw, sort_keys=True, default=str)


def _tool_input(payload):
    """Preserve observed empty/false input; absence and null remain unknown."""
    for field in ("arguments", "input"):
        if payload.get(field) is not None:
            return payload[field]
    return None


class CodexAdapter(AdapterBase):
    source = "codex"
    adapter_version = 11

    def __init__(self, id_salt: bytes, excluded_session_ids=None,
                 handoff_tools=None, capabilities=None,
                 machine_prompt_openers=None):
        super().__init__(id_salt)
        if handoff_tools is None or capabilities is None \
                or machine_prompt_openers is None:
            from .registry import default_options_for
            defaults = default_options_for(self.source)
            handoff_tools = defaults["handoff_tools"] if handoff_tools is None else handoff_tools
            capabilities = defaults["capabilities"] if capabilities is None else capabilities
            if machine_prompt_openers is None:
                machine_prompt_openers = defaults["machine_prompt_openers"]
        self.machine_prompt_openers = tuple(str(v) for v in machine_prompt_openers)
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
        machine_origins = []
        human_prompts = tool_calls = tool_results = 0
        usage = {}
        accounting_events = 0
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
                        accounting_events += 1
                elif event.get("type") == "task_started":
                    status = "open"
                    duration_ms = 0
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
                body = _message_text(payload)
                origin = codex_machine_prompt_origin(payload, body, self.machine_prompt_openers)
                if origin:
                    machine_origins.append({"sequence": sequence, "origin": "machine",
                                            "method": origin})
                if not body or origin:
                    continue
                human_prompts += 1
                status = "open"
                duration_ms = 0
                records.append(self._span(trace_id, sequence, SpanKind.PROMPT,
                                          record, source_version, "human"))
            elif item_type == "message" and payload.get("role") == "assistant":
                records.append(self._span(trace_id, sequence, SpanKind.LLM,
                                          record, source_version, "agent"))
            elif item_type in {"function_call", "custom_tool_call"}:
                tool_calls += 1
                tool = str(payload.get("name") or payload.get("tool_name") or "")
                raw = _tool_input(payload)
                signature = (self.ids.make(tool, _canonical_arguments(raw))
                             if tool.strip() and raw is not None else "")
                kind = SpanKind.HANDOFF if tool in self.handoff_tools else SpanKind.TOOL
                records.append(self._span(trace_id, sequence, kind,
                                          record, source_version, "agent", tool, signature))
            elif item_type in {"function_call_output", "custom_tool_call_output"}:
                tool_results += 1

        included = human_prompts > 0 and bool(records)
        if included:
            fields = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                      'output_tokens', 'reasoning_output_tokens')
            # Availability describes the last counter, not verified lifetime cost.
            complete = {field: type(usage.get(field)) is int and usage[field] >= 0
                        for field in fields}
            tokens = {field: usage[field] if complete[field] else 0 for field in fields}
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
                input_tokens=tokens['input_tokens'],
                cached_input_tokens=tokens['cached_input_tokens'],
                cache_write_input_tokens=tokens['cache_write_input_tokens'],
                output_tokens=tokens['output_tokens'],
                reasoning_output_tokens=tokens['reasoning_output_tokens'],
                duration_ms=duration_ms,
                attributes={"machine_message_origins": machine_origins,
                            "usage_complete": complete,
                            "usage_scope": "last_captured_cumulative_counter",
                            "usage_accounting_events": accounting_events},
            ))
        return AdapterResult(
            source=self.source,
            included=included,
            exclusion_reason="" if included else "no direct-human prompt",
            is_subagent=False,
            records=tuple(records) if included else (),
            human_prompt_count=human_prompts,
            machine_message_origins=tuple(machine_origins),
            tool_call_count=tool_calls,
            tool_result_count=tool_results,
        )

    def private_prompt_evidence(self, path: Path, root: Path, redactor):
        """Redacted direct-human prompts keyed by their PROMPT span id.

        Applies read()'s population and wrapper rules, so every key matches a
        normalized span. External annotation use only.
        """
        rows = list(iter_jsonl(path))
        meta = next((record.get("payload") for _, record in rows
                     if record.get("type") == "session_meta"
                     and isinstance(record.get("payload"), dict)), {})
        session_id = str(meta.get("session_id") or meta.get("id") or "")
        if (meta.get("thread_source") != "user"
                or session_id.lower() in self.excluded_session_ids):
            return {}
        trace_id = self.trace_id(path, root)
        evidence = {}
        context = []
        for sequence, (_, record) in enumerate(rows):
            payload = record.get("payload")
            if record.get("type") != "response_item" or not isinstance(payload, dict) \
                    or payload.get("type") != "message":
                continue
            body = _message_text(payload)
            if payload.get("role") == "assistant":
                if body:
                    context.append(body)
            elif payload.get("role") == "user" and body \
                    and not codex_machine_prompt_origin(payload, body, self.machine_prompt_openers):
                span_id = self.ids.make(trace_id, sequence, SpanKind.PROMPT.value)
                answer, questions = codex_question_reply(body)
                evidence[span_id] = prompt_evidence(redactor, answer, context + questions)
                context = []
        return evidence

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
        call_details = {}
        last_assistant_context = ""
        for sequence, (_, record) in enumerate(rows):
            if record.get("type") != "response_item":
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            item_type = payload.get("type")
            if item_type == "message" and payload.get("role") == "assistant":
                shown = _message_text(payload)
                if shown:
                    last_assistant_context = evidence_excerpt(redactor(shown), 1200, tail_only=True)
                continue
            if item_type in {"function_call_output", "custom_tool_call_output"}:
                call_id = str(payload.get("call_id") or payload.get("id") or "")
                details = call_details.get(call_id)
                if details is not None:
                    raw = payload.get("output") or payload.get("result") or ""
                    shown = (json.dumps(raw, sort_keys=True, default=str)
                             if not isinstance(raw, str) else raw)
                    details["tool_result"] = evidence_excerpt(redactor(shown), 1600)
                continue
            if item_type not in {"function_call", "custom_tool_call"}:
                continue
            tool = str(payload.get("name") or payload.get("tool_name") or "")
            kind = SpanKind.HANDOFF if tool in self.handoff_tools else SpanKind.TOOL
            raw = _tool_input(payload)
            span_id = self.ids.make(trace_id, sequence, kind.value)
            details = {"tool_kind": tool}
            if raw is not None:
                shown = (json.dumps(raw, sort_keys=True, default=str)
                         if not isinstance(raw, str) else raw)
                details["tool_input"] = evidence_excerpt(redactor(shown), 1200)
            if last_assistant_context:
                details["intent_context"] = last_assistant_context
            evidence[span_id] = details
            call_id = str(payload.get("call_id") or payload.get("id") or "")
            if call_id:
                call_details[call_id] = details
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
