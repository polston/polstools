"""Provider-neutral model judge with explicit private-data authority."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class JudgeCase:
    case_id: str
    evidence_ref: str
    evidence: str
    data_classification: str


@dataclass(frozen=True)
class JudgeExecution:
    schema_version: int
    case_id: str
    evidence_ref: str
    rubric_id: str
    rubric_version: int
    provider_id: str
    prompt_hash: str
    config_hash: str
    label: str
    abstained: bool
    reason: str
    latency_ms: int
    estimated_cost: float

    def to_dict(self):
        return asdict(self)


class ModelJudge:
    def __init__(self, *, rubric_id, rubric_version, labels, prompt_template,
                 provider, model_config, allow_private_external=False,
                 abstain_label="ambiguous"):
        self.rubric_id = rubric_id
        self.rubric_version = rubric_version
        self.labels = frozenset(labels)
        self.prompt_template = prompt_template
        self.provider = provider
        self.model_config = dict(model_config)
        self.allow_private_external = allow_private_external
        self.abstain_label = str(abstain_label)
        if not self.abstain_label:
            raise ValueError("judge abstention label cannot be empty")

    def evaluate(self, case: JudgeCase) -> JudgeExecution:
        if (case.data_classification == "private" and not self.provider.local
                and not self.allow_private_external):
            raise PermissionError("private evidence requires explicit external-provider authority")
        prompt = self.prompt_template.format(
            rubric_id=self.rubric_id, evidence=case.evidence)
        prompt_hash = hashlib.sha256(self.prompt_template.encode()).hexdigest()
        config_hash = hashlib.sha256(json.dumps(
            self.model_config, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        request = {
            "rubric_id": self.rubric_id,
            "rubric_version": self.rubric_version,
            "labels": sorted(self.labels),
            "prompt": prompt,
            "model_config": dict(self.model_config),
        }
        started = time.perf_counter()
        try:
            response = self.provider.judge(request)
            label = str(response.get("label") or "")
            reason = str(response.get("reason") or "")
            abstained = label == self.abstain_label
            if label not in self.labels:
                if label == self.abstain_label:
                    reason = reason or "judge_abstained"
                else:
                    label = self.abstain_label
                    reason = "invalid_provider_label"
                abstained = True
            elif abstained:
                reason = reason or "judge_abstained"
            estimated_cost = float(response.get("estimated_cost") or 0.0)
        except Exception as exc:
            label = self.abstain_label
            reason = "provider_error:%s" % type(exc).__name__
            abstained = True
            estimated_cost = 0.0
        return JudgeExecution(
            schema_version=1, case_id=case.case_id, evidence_ref=case.evidence_ref,
            rubric_id=self.rubric_id, rubric_version=self.rubric_version,
            provider_id=str(self.provider.provider_id), prompt_hash=prompt_hash,
            config_hash=config_hash, label=label, abstained=abstained, reason=reason,
            latency_ms=int((time.perf_counter() - started) * 1000),
            estimated_cost=estimated_cost,
        )
