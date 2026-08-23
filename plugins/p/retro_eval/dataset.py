"""Immutable dataset manifests and stable held-out splits."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class DatasetPolicy:
    schema_version: int
    calibration_share: int

    def __post_init__(self):
        if self.schema_version != 1:
            raise ValueError("unsupported dataset policy schema")
        if not 1 <= self.calibration_share <= 99:
            raise ValueError("calibration_share must be between 1 and 99")


def load_dataset_policy(path: Path | None = None) -> DatasetPolicy:
    path = path or Path(__file__).resolve().parents[1] / "rubrics" / "policy.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload["dataset"]
        return DatasetPolicy(int(payload["schema_version"]),
                             int(values["calibration_share"]))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid dataset policy") from exc


def stable_split(trace_id: str, salt: bytes,
                 policy: DatasetPolicy | None = None) -> str:
    calibration_share = (policy or load_dataset_policy()).calibration_share
    if not 1 <= calibration_share <= 99:
        raise ValueError("calibration_share must be between 1 and 99")
    digest = hmac.new(salt, trace_id.encode("utf-8"), hashlib.sha256).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    return "calibration" if bucket < calibration_share else "test"


@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    schema_versions: tuple[int, ...]
    adapter_versions: dict[str, int]
    start: datetime
    end: datetime | None
    seed: str
    inclusion_predicates: tuple[str, ...] = ()
    exclusion_predicates: tuple[str, ...] = ()
    population: int = 0
    excluded_population: int = 0
    rubric_versions: dict[str, int] = field(default_factory=dict)
    scorer_versions: dict[str, int] = field(default_factory=dict)
    source_fingerprints: tuple[str, ...] = ()
    content_policy: str = "content_free"
    created_commit: str = ""
    split_policy: dict[str, object] = field(default_factory=dict)
    instruction_manifest_sha256: str = ""
    instruction_manifest_coverage: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.end is None:
            raise ValueError("dataset window must be closed")
        if self.end <= self.start:
            raise ValueError("dataset end must be after start")
        if len(set(self.schema_versions)) != 1:
            raise ValueError("mixed schema versions are not comparable")
        if self.population < 0 or self.excluded_population < 0:
            raise ValueError("dataset populations cannot be negative")
        if any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
               for value in self.source_fingerprints):
            raise ValueError("source fingerprints must be lowercase SHA-256")
        if self.instruction_manifest_sha256 and (
                len(self.instruction_manifest_sha256) != 64
                or any(char not in "0123456789abcdef"
                       for char in self.instruction_manifest_sha256)):
            raise ValueError("instruction manifest must be lowercase SHA-256")
        if self.instruction_manifest_coverage is not None:
            coverage = self.instruction_manifest_coverage
            if (not self.instruction_manifest_sha256
                    or set(coverage) != {"population", "resolved", "unresolved"}
                    or int(coverage["population"]) != self.population
                    or int(coverage["resolved"]) != self.population
                    or int(coverage["unresolved"]) != 0):
                raise ValueError("dataset instruction coverage is incomplete")

    def to_dict(self):
        stamp = lambda value: value.isoformat().replace("+00:00", "Z")
        result = {
            "schema_version": (3 if self.instruction_manifest_coverage is not None
                               else 2 if self.instruction_manifest_sha256 else 1),
            "dataset_id": self.dataset_id,
            "trace_schema_versions": list(self.schema_versions),
            "adapter_versions": dict(sorted(self.adapter_versions.items())),
            "start": stamp(self.start), "end": stamp(self.end), "seed": self.seed,
            "inclusion_predicates": list(self.inclusion_predicates),
            "exclusion_predicates": list(self.exclusion_predicates),
            "population": self.population,
            "excluded_population": self.excluded_population,
            "rubric_versions": dict(sorted(self.rubric_versions.items())),
            "scorer_versions": dict(sorted(self.scorer_versions.items())),
            "source_fingerprints": list(self.source_fingerprints),
            "content_policy": self.content_policy,
            "created_commit": self.created_commit,
            "split_policy": dict(self.split_policy),
        }
        if self.instruction_manifest_sha256:
            result["instruction_manifest_sha256"] = self.instruction_manifest_sha256
        if self.instruction_manifest_coverage is not None:
            result["instruction_manifest_coverage"] = dict(
                self.instruction_manifest_coverage)
        return result
