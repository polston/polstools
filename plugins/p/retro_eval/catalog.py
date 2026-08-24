"""Strict loaders for versioned metric and rubric definitions."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MetricDefinition:
    id: str
    version: int
    domain: str
    unit: str
    direction: str
    numerator: str
    denominator: str
    eligible_population: str
    source_capabilities: tuple[str, ...]
    version_floor: str
    minimum_n: int
    uncertainty_method: str
    validation_dataset: str
    known_biases: tuple[str, ...]
    retirement_rule: str
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricCatalogue:
    schema_version: int
    metrics: tuple[MetricDefinition, ...]
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RubricDefinition:
    id: str
    version: int
    scope: str
    labels: tuple[str, ...]
    required_evidence: tuple[str, ...]
    abstain_when: tuple[str, ...]
    minimum_n: int
    target_precision: float
    target_recall: float
    target_agreement: float
    positive_examples: tuple[str, ...]
    negative_examples: tuple[str, ...]
    allowed_uses: tuple[str, ...]
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RubricCatalogue:
    schema_version: int
    rubrics: tuple[RubricDefinition, ...]
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnnotationProtocol:
    id: str
    version: int
    rubric_id: str
    rubric_version: int
    labels: tuple[str, ...]
    decision_order: tuple[str, ...]
    definitions: dict[str, str]
    human_instruction: str
    label_prompts: dict[str, dict[str, str]]
    tie_breaks: tuple[str, ...]
    prompt_template: str
    sha256: str
    extensions: dict[str, Any] = field(default_factory=dict)

    def render_prompt(self, *, context: str, user_turn: str) -> str:
        return self.prompt_template.format(context=context, user_turn=user_turn)


@dataclass(frozen=True)
class AnnotationProtocolCatalogue:
    schema_version: int
    protocols: tuple[AnnotationProtocol, ...]
    extensions: dict[str, Any] = field(default_factory=dict)

    def get(self, protocol_id: str, version: int) -> AnnotationProtocol:
        try:
            return next(item for item in self.protocols
                        if item.id == protocol_id and item.version == version)
        except StopIteration as exc:
            raise ValueError("annotation protocol is absent from catalogue") from exc


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid catalogue: %s" % path.name) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported catalogue schema")
    return payload


def _unique(items, label: str) -> None:
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate %s id" % label)


def load_metric_catalogue(path: Path) -> MetricCatalogue:
    payload = _load(path)
    definitions = []
    known = {"id", "version", "domain", "unit", "direction", "numerator",
             "denominator", "eligible_population", "source_capabilities",
             "version_floor", "minimum_n", "uncertainty_method",
             "validation_dataset", "known_biases", "retirement_rule"}
    for raw in payload.get("metrics") or []:
        definitions.append(MetricDefinition(
            id=str(raw["id"]), version=int(raw["version"]),
            domain=str(raw["domain"]), unit=str(raw["unit"]),
            direction=str(raw["direction"]), numerator=str(raw["numerator"]),
            denominator=str(raw["denominator"]),
            eligible_population=str(raw["eligible_population"]),
            source_capabilities=tuple(raw.get("source_capabilities") or ()),
            version_floor=str(raw.get("version_floor") or "none"),
            minimum_n=int(raw["minimum_n"]),
            uncertainty_method=str(raw["uncertainty_method"]),
            validation_dataset=str(raw["validation_dataset"]),
            known_biases=tuple(raw.get("known_biases") or ()),
            retirement_rule=str(raw["retirement_rule"]),
            extensions={key: value for key, value in raw.items() if key not in known},
        ))
    _unique(definitions, "metric")
    return MetricCatalogue(
        schema_version=1, metrics=tuple(definitions),
        extensions={key: value for key, value in payload.items()
                    if key not in {"schema_version", "metrics"}},
    )


def load_rubric_catalogue(path: Path) -> RubricCatalogue:
    payload = _load(path)
    definitions = []
    defaults = payload.get("rubric_defaults") or {}
    default_allowed_uses = tuple(str(value)
                                 for value in defaults.get("allowed_uses") or ())
    known = {"id", "version", "scope", "labels", "required_evidence",
             "abstain_when", "minimum_n", "targets", "positive_examples",
             "negative_examples", "allowed_uses"}
    for raw in payload.get("rubrics") or []:
        targets = raw.get("targets") or {}
        definitions.append(RubricDefinition(
            id=str(raw["id"]), version=int(raw["version"]),
            scope=str(raw["scope"]), labels=tuple(raw["labels"]),
            required_evidence=tuple(raw.get("required_evidence") or ()),
            abstain_when=tuple(raw.get("abstain_when") or ()),
            minimum_n=int(raw["minimum_n"]),
            target_precision=float(targets["precision"]),
            target_recall=float(targets["recall"]),
            target_agreement=float(targets["agreement"]),
            positive_examples=tuple(raw.get("positive_examples") or ()),
            negative_examples=tuple(raw.get("negative_examples") or ()),
            allowed_uses=tuple(
                str(value) for value in raw.get("allowed_uses") or
                default_allowed_uses),
            extensions={key: value for key, value in raw.items() if key not in known},
        ))
        if not definitions[-1].allowed_uses:
            raise ValueError("rubric requires explicit allowed uses")
    _unique(definitions, "rubric")
    return RubricCatalogue(
        schema_version=1, rubrics=tuple(definitions),
        extensions={key: value for key, value in payload.items()
                    if key not in {"schema_version", "rubrics"}},
    )


def ensure_rubric_use(rubric: RubricDefinition, use: str) -> None:
    """Refuse an output role the versioned rubric has not earned."""
    if use not in rubric.allowed_uses:
        raise ValueError("rubric %s does not allow %s" % (rubric.id, use))


def load_annotation_protocol_catalogue(
        path: Path, rubrics: RubricCatalogue) -> AnnotationProtocolCatalogue:
    payload = _load(path)
    definitions = []
    known = {"id", "version", "rubric_id", "rubric_version", "labels",
             "decision_order", "definitions", "human_instruction",
             "label_prompts", "tie_breaks", "prompt_template"}
    rubric_index = {(item.id, item.version): item for item in rubrics.rubrics}
    for raw in payload.get("protocols") or []:
        labels = tuple(str(value) for value in raw.get("labels") or ())
        decision_order = tuple(str(value) for value in raw.get("decision_order") or ())
        label_definitions = {
            str(key): str(value) for key, value in (raw.get("definitions") or {}).items()
        }
        human_instruction = str(raw.get("human_instruction") or
                                "Choose the dominant intent of the user turn.")
        label_prompts = {
            str(key): {"action": str(value.get("action") or ""),
                       "detail": str(value.get("detail") or "")}
            for key, value in (raw.get("label_prompts") or {}).items()
        }
        rubric_key = (str(raw["rubric_id"]), int(raw["rubric_version"]))
        rubric = rubric_index.get(rubric_key)
        if rubric is None:
            raise ValueError("annotation protocol references an absent rubric")
        if set(labels) != set(rubric.labels):
            raise ValueError("annotation protocol labels differ from rubric")
        if len(decision_order) != len(labels) or set(decision_order) != set(labels):
            raise ValueError("annotation protocol decision order must cover every label once")
        if set(label_definitions) != set(labels) or not all(label_definitions.values()):
            raise ValueError("annotation protocol requires every label definition")
        if int(raw["version"]) >= 2:
            if set(label_prompts) != set(labels) or not all(
                    value["action"] and value["detail"]
                    for value in label_prompts.values()):
                raise ValueError("annotation protocol requires every human label prompt")
            if not human_instruction:
                raise ValueError("annotation protocol requires human instruction")
        elif not label_prompts:
            label_prompts = {
                label: {"action": label.replace("_", " ").title(),
                        "detail": label_definitions[label]} for label in labels
            }
        template = str(raw.get("prompt_template") or "")
        if "{context}" not in template or "{user_turn}" not in template:
            raise ValueError("annotation protocol prompt must include context and user turn")
        canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"))
        definitions.append(AnnotationProtocol(
            id=str(raw["id"]), version=int(raw["version"]),
            rubric_id=rubric_key[0], rubric_version=rubric_key[1],
            labels=labels, decision_order=decision_order,
            definitions=label_definitions,
            human_instruction=human_instruction,
            label_prompts=label_prompts,
            tie_breaks=tuple(str(value) for value in raw.get("tie_breaks") or ()),
            prompt_template=template,
            sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            extensions={key: value for key, value in raw.items() if key not in known},
        ))
    keys = [(item.id, item.version) for item in definitions]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate annotation protocol id and version")
    return AnnotationProtocolCatalogue(
        schema_version=1, protocols=tuple(definitions),
        extensions={key: value for key, value in payload.items()
                    if key not in {"schema_version", "protocols"}},
    )
