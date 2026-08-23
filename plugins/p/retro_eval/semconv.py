"""Data-driven projection to external tracing semantic conventions."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

from .schema import SpanKind, TraceRecord


@dataclass(frozen=True)
class SemanticConventionProfile:
    schema_version: int
    profile_id: str
    field_mappings: dict[str, str]
    value_mappings: dict[str, dict[str, object]]
    kind_attributes: dict[str, dict[str, object]]

    @classmethod
    def load(cls, path: Path):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid semantic-convention profile") from exc
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported semantic-convention profile schema")
        known = {item.name for item in fields(TraceRecord)}
        mappings = dict(payload.get("field_mappings") or {})
        if any(field_name not in known for field_name in mappings):
            raise ValueError("semantic-convention profile maps an unknown trace field")
        return cls(
            schema_version=1, profile_id=str(payload["profile_id"]),
            field_mappings={str(key): str(value) for key, value in mappings.items()},
            value_mappings={str(key): dict(value) for key, value in
                            (payload.get("value_mappings") or {}).items()},
            kind_attributes={str(key): dict(value) for key, value in
                             (payload.get("kind_attributes") or {}).items()},
        )

    def attributes(self, record: TraceRecord) -> dict[str, object]:
        attributes = {}
        for field_name, attribute_name in self.field_mappings.items():
            value = getattr(record, field_name)
            if isinstance(value, SpanKind):
                value = value.value
            value = self.value_mappings.get(field_name, {}).get(str(value), value)
            if value not in (None, ""):
                attributes[attribute_name] = value
        kind = record.span_kind.value if isinstance(record.span_kind, SpanKind) else record.span_kind
        attributes.update(self.kind_attributes.get(str(kind), {}))
        return attributes
