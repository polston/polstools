"""Auditable scorer outputs shared by deterministic and judge scorers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ScoreResult:
    scorer_id: str
    scorer_version: int
    scope: str
    value: float | None
    label: str
    abstained: bool
    reason: str
    evidence_refs: tuple[str, ...]
    population: int
    eligible_population: int
    latency_ms: int
    estimated_cost: float
    limitations: tuple[str, ...]
    numerator: int = 0
    interval_low: float | None = None
    interval_high: float | None = None
    uncertainty_method: str = ""
    details: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.abstained and self.value is not None:
            raise ValueError("an abstention cannot carry a numeric score")
        if self.eligible_population > self.population:
            raise ValueError("eligible population cannot exceed population")
        if not self.abstained and self.eligible_population <= 0:
            raise ValueError("a score requires an eligible population")

    def to_dict(self):
        return asdict(self)
