"""Versioned, injectable scorer registry."""

from __future__ import annotations

import importlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path

from .scoring import ScoreResult


def _observable(value) -> bool:
    if value is True:
        return True
    if isinstance(value, dict):
        return value.get("state") in {"available", "version_floor"}
    return bool(getattr(value, "observable", False))


@dataclass(frozen=True)
class ScorerRegistration:
    scorer_id: str
    module: str
    class_name: str
    options: dict[str, object]

    def create(self):
        scorer_class = getattr(importlib.import_module(self.module), self.class_name)
        values = {"scorer_id": self.scorer_id, **self.options}
        accepted = set(inspect.signature(scorer_class.__init__).parameters) - {"self"}
        unknown = set(values) - accepted
        if unknown:
            raise ValueError("unsupported scorer options: %s" % ", ".join(sorted(unknown)))
        return scorer_class(**values)


class ScorerRegistry:
    def __init__(self, registrations=()):
        registrations = tuple(registrations)
        self._items = {item.scorer_id: item for item in registrations}
        if len(self._items) != len(registrations):
            raise ValueError("duplicate scorer registration")

    @classmethod
    def from_profile(cls, path: Path):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid scorer profile") from exc
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported scorer profile schema")
        return cls(ScorerRegistration(
            scorer_id=str(raw["id"]), module=str(raw["module"]),
            class_name=str(raw["class"]), options=dict(raw.get("options") or {}),
        ) for raw in payload.get("scorers") or ())

    def score(self, records, *, capabilities) -> tuple[ScoreResult, ...]:
        records = tuple(records)
        results = []
        for registration in self._items.values():
            scorer = registration.create()
            missing = [name for name in scorer.required_capabilities
                       if not _observable(capabilities.get(name))]
            if missing:
                results.append(ScoreResult(
                    scorer_id=scorer.scorer_id, scorer_version=scorer.version,
                    scope=scorer.scope, value=None, label="not_observable",
                    abstained=True, reason="missing capabilities: %s" % ", ".join(missing),
                    evidence_refs=(), population=len(records), eligible_population=0,
                    latency_ms=0, estimated_cost=0.0,
                    limitations=("source capability unavailable",),
                ))
            else:
                results.append(scorer.score(records))
        return tuple(results)


def default_scorers() -> ScorerRegistry:
    return ScorerRegistry.from_profile(
        Path(__file__).resolve().parents[1] / "rubrics" / "scorers.json"
    )
