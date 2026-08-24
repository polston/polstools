"""Evidence thresholds and transparent proposal ranking."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ProposalPolicy:
    schema_version: int
    minimum_sessions: int
    minimum_evidence_refs: int
    rank_by: tuple[tuple[str, str], ...]
    minimum_independent_units: int = 2

    def __post_init__(self):
        allowed = {"confidence", "avoidable_cost", "observed_rate", "population"}
        if self.schema_version != 1 or self.minimum_sessions < 1:
            raise ValueError("invalid proposal policy")
        if self.minimum_evidence_refs < 1:
            raise ValueError("invalid proposal policy")
        if self.minimum_independent_units < 1:
            raise ValueError("invalid proposal policy")
        if not self.rank_by or any(field not in allowed or direction not in {"asc", "desc"}
                                   for field, direction in self.rank_by):
            raise ValueError("invalid proposal ranking policy")


def load_proposal_policy(path: Path | None = None) -> ProposalPolicy:
    path = path or Path(__file__).resolve().parents[1] / "rubrics" / "policy.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw = payload["proposals"]
        return ProposalPolicy(
            schema_version=int(payload["schema_version"]),
            minimum_sessions=int(raw["minimum_sessions"]),
            minimum_evidence_refs=int(raw["minimum_evidence_refs"]),
            minimum_independent_units=int(raw["minimum_independent_units"]),
            rank_by=tuple((str(field), str(direction))
                          for field, direction in raw["rank_by"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid proposal policy") from exc


@dataclass
class Proposal:
    proposal_id: str
    target_kind: str
    target_ref: str
    population: int
    session_count: int
    evidence_refs: tuple[str, ...]
    observed_rate: float | None
    uncertainty: str
    expected_impact: str
    exact_change: str
    experiment: str
    success_threshold: str
    rollback: str
    confidence: float
    avoidable_cost: float
    population_unit: str = "session"
    evidence_unit: str = "session"
    independent_evidence_count: int = 0
    window: str = ""
    comparison: str = ""
    effect_size: str = ""
    dependencies: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    evidence_binding: dict[str, object] = field(default_factory=dict)
    evidence_rubric_ids: tuple[str, ...] = ()
    status: str = "proposed"
    rank_components: dict[str, float] = field(default_factory=dict)
    policy_suppression_reason: str = field(default="", init=False)

    def __post_init__(self):
        if self.status not in {"proposed", "accepted", "implemented", "rejected",
                               "superseded"}:
            raise ValueError("invalid proposal status")
        if self.session_count < 0 or self.independent_evidence_count < 0:
            raise ValueError("proposal evidence counts cannot be negative")
        if self.evidence_unit == "session":
            if (self.independent_evidence_count
                    and self.independent_evidence_count != self.session_count):
                raise ValueError("session evidence count contradicts session population")
            self.independent_evidence_count = self.session_count

    def to_dict(self):
        payload = asdict(self)
        payload.pop("policy_suppression_reason", None)
        return payload

    def suppression_reason(self, policy: ProposalPolicy | None = None) -> str:
        policy = policy or load_proposal_policy()
        if self.policy_suppression_reason:
            return self.policy_suppression_reason
        evidence_count = self.independent_evidence_count
        minimum = (policy.minimum_sessions if self.evidence_unit == "session"
                   else policy.minimum_independent_units)
        if evidence_count < minimum:
            return ("insufficient sessions" if self.evidence_unit == "session"
                    else "insufficient independent evidence units")
        if len(set(self.evidence_refs)) < policy.minimum_evidence_refs:
            return "insufficient evidence references"
        if self.population <= 0:
            return "no eligible population"
        if not self.exact_change.strip():
            return "no exact change"
        if not self.experiment.strip() or not self.success_threshold.strip():
            return "no falsifiable experiment"
        if not self.rollback.strip():
            return "no rollback"
        return ""


def rank_proposals(proposals: list[Proposal],
                   policy: ProposalPolicy | None = None) -> list[Proposal]:
    policy = policy or load_proposal_policy()
    eligible = [proposal for proposal in proposals
                if proposal.status == "proposed"
                and not proposal.suppression_reason(policy)]
    for proposal in eligible:
        proposal.rank_components = {
            "confidence": proposal.confidence,
            "avoidable_cost": proposal.avoidable_cost,
        }
    def key(item):
        components = []
        for field_name, direction in policy.rank_by:
            value = getattr(item, field_name)
            components.append(-value if direction == "desc" else value)
        return (*components, item.proposal_id)

    return sorted(eligible, key=key)


def proposal_review(proposals, policy: ProposalPolicy | None = None):
    policy = policy or load_proposal_policy()
    proposals = list(proposals)
    ranked = []
    for rank, item in enumerate(rank_proposals(proposals, policy), 1):
        payload = item.to_dict()
        payload["rank"] = rank
        ranked.append(payload)
    suppressed = []
    for item in proposals:
        if item.status != "proposed":
            continue
        reason = item.suppression_reason(policy)
        if reason:
            payload = item.to_dict()
            payload["status"] = "suppressed"
            payload["suppression_reason"] = reason
            suppressed.append(payload)
    resolved = [item.to_dict() for item in proposals if item.status != "proposed"]
    return {"schema_version": 1, "policy": asdict(policy),
            "ranked": ranked, "suppressed": suppressed, "resolved": resolved,
            "auto_apply": False}
