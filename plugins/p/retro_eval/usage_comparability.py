"""Versioned gate for cross-source usage comparisons."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UsageAccountingProfile:
    schema_version: int
    profile_version: int
    sources: dict[str, dict[str, str]]
    required_case_fields: tuple[str, ...]
    sha256: str
    comparable_dimensions: tuple[str, ...] = ()


def _nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def load_usage_accounting_profile(path: Path | None = None):
    path = path or (Path(__file__).resolve().parents[1]
                    / "profiles" / "usage-accounting.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("profile must be an object")
        if (type(raw.get("schema_version")) is not int or raw["schema_version"] != 1
                or type(raw.get("profile_version")) is not int or raw["profile_version"] < 1):
            raise ValueError("unsupported usage accounting profile")
        sources = raw.get("sources")
        if not isinstance(sources, dict) or len(sources) < 2:
            raise ValueError("usage accounting profile requires multiple sources")
        if any(not _nonempty_text(key) or not isinstance(value, dict)
               or not _nonempty_text(value.get("accounting_version"))
               for key, value in sources.items()):
            raise ValueError("usage accounting source contract is incomplete")
        required = {"task_family", "difficulty", "cache_treatment",
                    "accounting_version", "accounting_profile_version", "paired_case_id"}
        fields = raw.get("required_case_fields")
        if (not isinstance(fields, list) or not all(_nonempty_text(v) for v in fields)
                or len(fields) != len(required) or set(fields) != required):
            raise ValueError("usage accounting profile has incomplete gate fields")
        dimensions = raw.get("comparable_dimensions", [])
        allowed_dimensions = {"input_tokens", "cached_input_tokens", "output_tokens"}
        if (not isinstance(dimensions, list)
                or not all(isinstance(v, str) and v in allowed_dimensions for v in dimensions)
                or len(set(dimensions)) != len(dimensions)):
            raise ValueError("invalid comparable dimensions")
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
        profile = UsageAccountingProfile(
            schema_version=raw["schema_version"], profile_version=raw["profile_version"],
            sources={key: dict(value) for key, value in sources.items()},
            required_case_fields=tuple(fields),
            sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            comparable_dimensions=tuple(dimensions))
    except (OSError, KeyError, TypeError, ValueError,
            json.JSONDecodeError) as exc:
        raise ValueError("invalid usage accounting profile") from exc
    return profile


def validate_usage_comparison(rows, profile: UsageAccountingProfile | None = None, *,
                              dimensions=("input_tokens", "cached_input_tokens", "output_tokens")):
    """Validate pairs for named dimensions; never normalize raw source counts.

    The default requests all counts and fails if any has incompatible semantics.
    A successful result authorizes only its comparison_dimensions, not other
    token fields, billing equivalence, or a causal experiment.
    """
    profile = profile or load_usage_accounting_profile()
    try:
        rows = tuple(dict(row) for row in rows)
    except (TypeError, ValueError) as exc:
        raise ValueError("usage comparison rows must be mappings") from exc
    if not rows:
        raise ValueError("usage comparison has no cases")
    grouped = defaultdict(list)
    for row in rows:
        for field in profile.required_case_fields:
            value = row.get(field)
            valid = (type(value) is int and value >= 1 if field == "accounting_profile_version"
                     else _nonempty_text(value))
            if not valid:
                raise ValueError("usage comparison requires valid %s" % field)
        source = row.get("source")
        if not _nonempty_text(source) or source not in profile.sources:
            raise ValueError("usage comparison source is not profiled")
        expected_version = str(profile.sources[source]["accounting_version"])
        if row["accounting_version"] != expected_version:
            raise ValueError("usage accounting version mismatch")
        if row["accounting_profile_version"] != profile.profile_version:
            raise ValueError("usage accounting profile version mismatch")
        for field in ("input_tokens", "cached_input_tokens", "output_tokens"):
            value = row.get(field)
            if type(value) is not int or value < 0:
                raise ValueError("usage comparison requires observed nonnegative integer %s" % field)
        if (source == "codex" and expected_version == "codex-v1"
                and row["cached_input_tokens"] > row["input_tokens"]):
            raise ValueError("Codex cached input must be a subset of input")
        grouped[row["paired_case_id"]].append(row)
    expected_sources = set(profile.sources)
    if len({str(row["source"]) for row in rows}) < 2:
        raise ValueError("paired source coverage is incomplete")
    for pair in grouped.values():
        if {str(row["source"]) for row in pair} != expected_sources:
            raise ValueError("paired source coverage is incomplete")
        if len(pair) != len(expected_sources):
            raise ValueError("paired case requires exactly one row per source")
        metadata = {(str(row["task_family"]), str(row["difficulty"]),
                     str(row["cache_treatment"])) for row in pair}
        if len(metadata) != 1:
            raise ValueError("pair metadata mismatch")
    if (not isinstance(dimensions, (list, tuple)) or not dimensions
            or not all(isinstance(v, str) and v in profile.comparable_dimensions
                       for v in dimensions)
            or len(set(dimensions)) != len(dimensions)):
        raise ValueError("comparison dimensions must be unique and explicitly compatible in the profile")
    return {
        "schema_version": 2,
        "accounting_profile_version": profile.profile_version,
        "accounting_profile_sha256": profile.sha256,
        "comparison_allowed": True,
        "comparison_dimensions": list(dimensions),
        "paired_cases": len(grouped),
        "sources": sorted(expected_sources),
        "accounting_versions": {
            source: profile.sources[source]["accounting_version"]
            for source in sorted(expected_sources)},
        "paired_experiment": "not_executed",
        "limitations": [
            "the gate establishes metadata for named dimensions, not task success",
            "source token counts are not billing or equal-work units",
            "count provenance and accounting completeness require separate evidence",
            "no paired cross-harness experiment was authorized or executed",
        ],
    }
