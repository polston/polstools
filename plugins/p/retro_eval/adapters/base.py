"""Shared adapter result types and content-free helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..schema import Capability, CapabilityState, LocalIdFactory, TraceRecord, SpanKind


_LEGACY_BLOCK_STRIDE = 1000


@dataclass(frozen=True)
class AdapterResult:
    source: str
    included: bool
    exclusion_reason: str
    is_subagent: bool
    records: tuple[TraceRecord, ...]
    human_prompt_count: int = 0
    tool_call_count: int = 0
    tool_result_count: int = 0
    skills: tuple[str, ...] = ()
    # False when the source has no authoritative main/subagent marker.
    population_observable: bool = True
    # None means no origin scan; an empty tuple means scanned, none recognized.
    machine_message_origins: tuple[dict[str, object], ...] | None = None


class SourceUnreadable(OSError):
    """A source file could not be read; distinct from one holding no records."""


def iter_jsonl(path: Path):
    """Yield (line number, record) for dict records, skipping partial lines.

    Raises SourceUnreadable when the file cannot be opened or read, so a read
    failure is never reported as a file without a direct-human prompt.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_no, line in enumerate(handle, 1):
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(record, dict):
                    yield line_no, record
    except OSError as exc:
        raise SourceUnreadable(str(path)) from exc


PROMPT_EVIDENCE_CHARS = 1200


def prompt_evidence(redactor, text: str, context_parts) -> dict[str, object]:
    """Redacted annotation evidence for one direct-human prompt.

    ``context_parts`` is every assistant text since the previous direct-human
    prompt; its summed length is the prior-turn size the legacy rule reads,
    as bin/retro.py accumulates it. Excerpts are redacted and bounded.
    External annotation use only.
    """
    context = "\n".join(context_parts)
    return {
        "user_turn": redactor(text)[:PROMPT_EVIDENCE_CHARS],
        "user_turn_chars": len(text),
        "context": redactor(context)[-PROMPT_EVIDENCE_CHARS:] if context else "",
        "context_chars": sum(len(part) for part in context_parts),
    }


def parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 source timestamp; naive values have no known zone."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


class AdapterBase:
    source = ""
    adapter_version = 1

    def __init__(self, id_salt: bytes):
        self.ids = LocalIdFactory(id_salt)

    def indexed_span_id(self, trace_id, source_index, block_index, kind):
        """Preserve ordinary IDs; encode overflow without adjacent-record aliasing."""
        kind_value = kind.value if isinstance(kind, SpanKind) else str(kind)
        if block_index < _LEGACY_BLOCK_STRIDE:
            return self.ids.make(trace_id,
                                 source_index * _LEGACY_BLOCK_STRIDE + block_index,
                                 kind_value)
        return self.ids.make(trace_id, "indexed_block", source_index, block_index,
                             kind_value)

    @staticmethod
    def indexed_sequence(previous, source_index, block_index):
        """Keep streaming encounter order when a batch exceeds the legacy stride."""
        return max(source_index * _LEGACY_BLOCK_STRIDE + block_index, previous + 1)

    @staticmethod
    def parse_capabilities(raw) -> dict[str, Capability]:
        return {
            str(name): Capability(
                CapabilityState(str(value["state"])),
                reason=str(value.get("reason") or ""),
                version_floor=str(value.get("version_floor") or ""),
            )
            for name, value in (raw or {}).items()
        }

    def trace_id(self, path: Path, root: Path) -> str:
        try:
            relative = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            relative = path.name
        return self.ids.make(self.source, relative)
