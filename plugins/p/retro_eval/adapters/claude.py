"""Claude Code JSONL adapter."""

from __future__ import annotations

import json
from pathlib import Path

from .base import AdapterBase, AdapterResult, iter_jsonl, parse_timestamp
from ..schema import SCHEMA_VERSION, SpanKind, TraceRecord
from ..taxonomies import classify_failure_evidence


def _direct_human(record, message, direct_prompt_sources) -> bool:
    origin = record.get("origin")
    origin_kind = origin.get("kind") if isinstance(origin, dict) else None
    return bool(
        record.get("type") == "user"
        and message.get("role") == "user"
        and record.get("isMeta") is not True
        and record.get("promptSource") != "system"
        and record.get("sourceToolAssistantUUID") is None
        and record.get("toolUseResult") is None
        and (record.get("promptSource") in direct_prompt_sources
             or origin_kind == "human")
    )


def _skill_outcome(record):
    value = str(record.get("attributionSkillOutcome") or "")
    return value if value in {"success", "failure", "cancelled"} else ""


def _integer_or_zero(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class ClaudeAdapter(AdapterBase):
    source = "claude"
    adapter_version = 3

    def __init__(self, id_salt: bytes, direct_prompt_sources=None, capabilities=None):
        super().__init__(id_salt)
        if direct_prompt_sources is None or capabilities is None:
            from .registry import default_options_for
            defaults = default_options_for(self.source)
            direct_prompt_sources = (
                defaults["direct_prompt_sources"] if direct_prompt_sources is None
                else direct_prompt_sources
            )
            capabilities = defaults["capabilities"] if capabilities is None else capabilities
        self.direct_prompt_sources = frozenset(direct_prompt_sources)
        self.capabilities = self.parse_capabilities(capabilities)

    def read(self, path: Path, root: Path) -> AdapterResult:
        trace_id = self.trace_id(path, root)
        is_subagent = "subagents" in {part.lower() for part in path.relative_to(root).parts}
        mode = "subagent" if is_subagent else "main"
        records = []
        human_prompts = tool_calls = tool_results = 0
        skills = []
        source_version = ""
        conversation = 0
        input_tokens = cached_tokens = output_tokens = 0
        first_at = last_at = None
        answered = False
        tool_names = {}
        tool_inputs = {}
        active_skill = ""
        active_invocation = ""
        active_skill_id = ""
        active_outcome = ""

        for sequence, (_, record) in enumerate(iter_jsonl(path)):
            stamp = parse_timestamp(record.get("timestamp"))
            first_at = first_at or stamp
            last_at = stamp or last_at
            source_version = source_version or str(record.get("version") or "")
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            record_type = record.get("type")
            attributed_skill = str(record.get("attributionSkill") or "")
            if record_type == "assistant" and active_skill != attributed_skill:
                if active_skill:
                    records.append(self._span(
                        trace_id, sequence, "retro.skill.end", record,
                        source_version, mode, "skill", status="observed",
                        attributes={
                            "skill_id": active_skill_id,
                            "invocation_id": active_invocation,
                            "end_basis": "attribution_boundary",
                            "outcome": active_outcome or "not_observable",
                        }))
                    active_skill = active_invocation = active_skill_id = ""
                    active_outcome = ""
                if attributed_skill:
                    active_skill = attributed_skill
                    active_outcome = _skill_outcome(record)
                    active_skill_id = self.ids.make("skill", attributed_skill)
                    active_invocation = self.ids.make(
                        trace_id, "skill-invocation", sequence, attributed_skill)
                    attributes = {
                        "skill_id": active_skill_id,
                        "invocation_id": active_invocation,
                        "start_basis": "explicit_attribution",
                    }
                    chain_id = str(record.get("attributionSkillChainId") or "")
                    if chain_id:
                        attributes["chain_id"] = self.ids.make("skill-chain", chain_id)
                        attributes["chain_position"] = _integer_or_zero(
                            record.get("attributionSkillChainPosition"))
                    records.append(self._span(
                        trace_id, sequence, "retro.skill.start", record,
                        source_version, mode, "skill", status="observed",
                        attributes=attributes))
                    if chain_id:
                        records.append(self._span(
                            trace_id, sequence, "retro.skill.chain_step", record,
                            source_version, mode, "skill", status="observed",
                            attributes=attributes))
            elif record_type == "assistant" and active_skill:
                active_outcome = _skill_outcome(record) or active_outcome
            if record_type in {"user", "assistant"}:
                conversation += 1
            skill = record.get("attributionSkill")
            if skill and str(skill) not in skills:
                skills.append(str(skill))
            if _direct_human(record, message, self.direct_prompt_sources):
                human_prompts += 1
                records.append(self._span(trace_id, sequence, SpanKind.PROMPT,
                                          record, source_version, mode, "human"))
            if record_type == "assistant":
                usage = message.get("usage")
                if isinstance(usage, dict):
                    input_tokens += int(usage.get("input_tokens") or 0)
                    cached_tokens += int(usage.get("cache_read_input_tokens") or 0)
                    output_tokens += int(usage.get("output_tokens") or 0)
                content = message.get("content")
                answered = answered or bool(
                    isinstance(content, str) and content.strip()
                    or isinstance(content, list) and any(
                        isinstance(block, dict) and block.get("type") == "text"
                        and str(block.get("text") or "").strip()
                        for block in content))
                records.append(self._span(trace_id, sequence, SpanKind.LLM,
                                          record, source_version, mode, "agent"))
                if isinstance(content, list):
                    for index, block in enumerate(content):
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        tool_calls += 1
                        tool = str(block.get("name") or "")
                        if block.get("id"):
                            tool_names[str(block["id"])] = tool
                            tool_inputs[str(block["id"])] = json.dumps(
                                block.get("input"), sort_keys=True, default=str)
                        signature = self.ids.make(
                            tool, json.dumps(block.get("input"), sort_keys=True, default=str))
                        records.append(self._span(
                            trace_id, sequence * 1000 + index, SpanKind.TOOL,
                            record, source_version, mode, "agent", tool, signature))
            if record_type == "user":
                content = message.get("content")
                if isinstance(content, list):
                    for index, block in enumerate(content):
                        if not isinstance(block, dict) or block.get("type") != "tool_result":
                            continue
                        tool_results += 1
                        tool_id = str(block.get("tool_use_id") or "")
                        tool = tool_names.get(tool_id, "")
                        attributes = {"event": "result"}
                        if block.get("is_error"):
                            diagnosis = classify_failure_evidence(
                                tool_inputs.get(tool_id, ""),
                                json.dumps(block.get("content"), sort_keys=True,
                                           default=str))
                            attributes.update({
                                "error_kind": diagnosis.kind,
                                "error_reason": diagnosis.reason_code,
                                "failure_taxonomy_version": 3,
                            })
                        records.append(self._span(
                            trace_id, sequence * 1000 + index, "retro.tool_result",
                            record, source_version, mode, "tool", tool,
                            status="error" if block.get("is_error") else "ok",
                            attributes=attributes,
                        ))

        included = bool(conversation and (human_prompts or is_subagent))
        reason = "" if included else "no direct-human prompt"
        if included:
            root_id = self.ids.make(trace_id, "root")
            records.insert(0, TraceRecord(
                schema_version=SCHEMA_VERSION,
                trace_id=trace_id,
                span_id=root_id,
                parent_span_id=None,
                source=self.source,
                adapter_version=self.adapter_version,
                source_version=source_version,
                span_kind=SpanKind.TRACE,
                started_at=first_at,
                ended_at=last_at,
                sequence=0,
                status="complete" if answered else "open",
                main_or_subagent=mode,
                input_tokens=input_tokens,
                cached_input_tokens=cached_tokens,
                output_tokens=output_tokens,
                duration_ms=(int((last_at - first_at).total_seconds() * 1000)
                             if first_at and last_at else 0),
                attributes={"skill_count": len(skills)},
            ))
        return AdapterResult(
            source=self.source,
            included=included,
            exclusion_reason=reason,
            is_subagent=is_subagent,
            records=tuple(records) if included else (),
            human_prompt_count=human_prompts,
            tool_call_count=tool_calls,
            tool_result_count=tool_results,
            skills=tuple(skills),
        )

    def private_tool_evidence(self, path: Path, root: Path, redactor):
        """Return redacted tool evidence for external annotation only."""
        trace_id = self.trace_id(path, root)
        tool_details = {}
        evidence = {}
        for sequence, (_, record) in enumerate(iter_jsonl(path)):
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for index, block in enumerate(content):
                if not isinstance(block, dict):
                    continue
                if (record.get("type") == "assistant"
                        and block.get("type") == "tool_use"):
                    tool_id = str(block.get("id") or "")
                    details = {
                        "tool_kind": str(block.get("name") or ""),
                        "tool_input": redactor(json.dumps(
                            block.get("input"), sort_keys=True,
                            default=str))[:800],
                    }
                    if tool_id:
                        tool_details[tool_id] = details
                    span_id = self.ids.make(
                        trace_id, sequence * 1000 + index, SpanKind.TOOL.value)
                    evidence[span_id] = details
                elif (record.get("type") == "user"
                      and block.get("type") == "tool_result"):
                    tool_id = str(block.get("tool_use_id") or "")
                    details = dict(tool_details.get(tool_id) or {})
                    details["tool_result"] = redactor(json.dumps(
                        block.get("content"), sort_keys=True,
                        default=str))[:1200]
                    span_id = self.ids.make(
                        trace_id, sequence * 1000 + index, "retro.tool_result")
                    evidence[span_id] = details
        return evidence

    def _span(self, trace_id, sequence, kind, record, source_version, mode,
              actor, tool="", signature="", status="ok", attributes=None):
        kind_value = kind.value if isinstance(kind, SpanKind) else str(kind)
        return TraceRecord(
            schema_version=SCHEMA_VERSION,
            trace_id=trace_id,
            span_id=self.ids.make(trace_id, sequence, kind_value),
            parent_span_id=self.ids.make(trace_id, "root"),
            source=self.source,
            adapter_version=self.adapter_version,
            source_version=source_version,
            span_kind=kind,
            started_at=parse_timestamp(record.get("timestamp")),
            sequence=sequence,
            status=status,
            actor_kind=actor,
            main_or_subagent=mode,
            tool_kind=tool,
            call_signature=signature,
            attributes=dict(attributes or {}),
        )
