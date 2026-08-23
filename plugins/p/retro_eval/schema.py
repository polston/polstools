"""Normalized, content-free trace contracts."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


SCHEMA_VERSION = 1


class SpanKind(str, Enum):
    TRACE = "trace"
    PROMPT = "prompt"
    LLM = "llm"
    TOOL = "tool"
    AGENT = "agent"
    HANDOFF = "handoff"
    GUARDRAIL = "guardrail"
    EVALUATOR = "evaluator"


class CapabilityState(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    VERSION_FLOOR = "version_floor"


@dataclass(frozen=True)
class Capability:
    state: CapabilityState
    reason: str = ""
    version_floor: str = ""

    @property
    def observable(self) -> bool:
        return self.state is not CapabilityState.UNAVAILABLE

    def to_dict(self) -> dict[str, str]:
        result = {"state": self.state.value}
        if self.reason:
            result["reason"] = self.reason
        if self.version_floor:
            result["version_floor"] = self.version_floor
        return result


class LocalIdFactory:
    """Installation-scoped identifiers that do not disclose their input."""

    def __init__(self, salt: bytes):
        if not isinstance(salt, bytes) or len(salt) < 4:
            raise ValueError("id salt must contain at least four bytes")
        self._salt = salt

    def make(self, *parts: object) -> str:
        body = "\x1f".join(str(part) for part in parts).encode(
            "utf-8", errors="replace")
        return hmac.new(self._salt, body, hashlib.sha256).hexdigest()[:24]


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


@dataclass(frozen=True)
class TraceRecord:
    schema_version: int
    trace_id: str
    span_id: str
    parent_span_id: str | None
    source: str
    adapter_version: int
    source_version: str
    span_kind: SpanKind | str
    started_at: datetime | None
    sequence: int
    ended_at: datetime | None = None
    status: str = "ok"
    actor_kind: str = "agent"
    main_or_subagent: str = "main"
    workflow_depth: int = 0
    tool_kind: str = ""
    call_signature: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    duration_ms: int = 0
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "source": self.source,
            "adapter_version": self.adapter_version,
            "source_version": self.source_version,
            "span_kind": (self.span_kind.value
                          if isinstance(self.span_kind, SpanKind)
                          else self.span_kind),
            "started_at": _timestamp(self.started_at),
            "ended_at": _timestamp(self.ended_at),
            "sequence": self.sequence,
            "status": self.status,
            "actor_kind": self.actor_kind,
            "main_or_subagent": self.main_or_subagent,
            "workflow_depth": self.workflow_depth,
            "tool_kind": self.tool_kind,
            "call_signature": self.call_signature,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
            "duration_ms": self.duration_ms,
            "attributes": dict(self.attributes),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TraceRecord":
        def parse(value: Any) -> datetime | None:
            if not value:
                return None
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

        raw_kind = str(payload["span_kind"])
        try:
            span_kind: SpanKind | str = SpanKind(raw_kind)
        except ValueError:
            if "." not in raw_kind:
                raise ValueError("extension span kinds must be namespaced")
            span_kind = raw_kind

        return cls(
            schema_version=int(payload["schema_version"]),
            trace_id=str(payload["trace_id"]),
            span_id=str(payload["span_id"]),
            parent_span_id=payload.get("parent_span_id"),
            source=str(payload["source"]),
            adapter_version=int(payload["adapter_version"]),
            source_version=str(payload.get("source_version") or ""),
            span_kind=span_kind,
            started_at=parse(payload.get("started_at")),
            ended_at=parse(payload.get("ended_at")),
            sequence=int(payload["sequence"]),
            status=str(payload.get("status") or "ok"),
            actor_kind=str(payload.get("actor_kind") or "agent"),
            main_or_subagent=str(payload.get("main_or_subagent") or "main"),
            workflow_depth=int(payload.get("workflow_depth") or 0),
            tool_kind=str(payload.get("tool_kind") or ""),
            call_signature=str(payload.get("call_signature") or ""),
            input_tokens=int(payload.get("input_tokens") or 0),
            cached_input_tokens=int(payload.get("cached_input_tokens") or 0),
            cache_write_input_tokens=int(payload.get("cache_write_input_tokens") or 0),
            output_tokens=int(payload.get("output_tokens") or 0),
            reasoning_output_tokens=int(payload.get("reasoning_output_tokens") or 0),
            duration_ms=int(payload.get("duration_ms") or 0),
            attributes=dict(payload.get("attributes") or {}),
        )
