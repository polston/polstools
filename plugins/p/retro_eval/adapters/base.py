"""Shared adapter result types and content-free helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..schema import Capability, CapabilityState, LocalIdFactory, TraceRecord


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
