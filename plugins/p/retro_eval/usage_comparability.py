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


def load_usage_accounting_profile(path: Path | None = None):
    path = path or (Path(__file__).resolve().parents[1]
                    / "profiles" / "usage-accounting.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
        profile = UsageAccountingProfile(
            schema_version=int(raw["schema_version"]),
            profile_version=int(raw["profile_version"]),
            sources={str(key): dict(value)
                     for key, value in raw["sources"].items()},
            required_case_fields=tuple(str(value)
                                       for value in raw["required_case_fields"]),
            sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest())
    except (OSError, KeyError, TypeError, ValueError,
            json.JSONDecodeError) as exc:
        raise ValueError("invalid usage accounting profile") from exc
    if profile.schema_version != 1 or profile.profile_version < 1:
        raise ValueError("unsupported usage accounting profile")
    required = {"task_family", "difficulty", "cache_treatment",
                "accounting_version", "accounting_profile_version",
                "paired_case_id"}
    if set(profile.required_case_fields) != required:
        raise ValueError("usage accounting profile has incomplete gate fields")
    if len(profile.sources) < 2:
        raise ValueError("usage accounting profile requires multiple sources")
    return profile


def validate_usage_comparison(rows, profile: UsageAccountingProfile | None = None):
    """Reject an unmatched comparison before any cross-source statistic runs."""
    profile = profile or load_usage_accounting_profile()
    rows = tuple(dict(row) for row in rows)
    if not rows:
        raise ValueError("usage comparison has no cases")
    grouped = defaultdict(list)
    for row in rows:
        for field in profile.required_case_fields:
            if row.get(field) in {None, ""}:
                raise ValueError("usage comparison requires %s" % field)
        source = str(row.get("source") or "")
        if source not in profile.sources:
            raise ValueError("usage comparison source is not profiled")
        expected_version = str(profile.sources[source]["accounting_version"])
        if row["accounting_version"] != expected_version:
            raise ValueError("usage accounting version mismatch")
        if int(row["accounting_profile_version"]) != profile.profile_version:
            raise ValueError("usage accounting profile version mismatch")
        for field in ("input_tokens", "cached_input_tokens", "output_tokens"):
            if int(row.get(field) or 0) < 0:
                raise ValueError("usage token counts cannot be negative")
        grouped[str(row["paired_case_id"])].append(row)
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
    return {
        "schema_version": 1,
        "accounting_profile_version": profile.profile_version,
        "accounting_profile_sha256": profile.sha256,
        "comparison_allowed": True,
        "paired_cases": len(grouped),
        "sources": sorted(expected_sources),
        "accounting_versions": {
            source: profile.sources[source]["accounting_version"]
            for source in sorted(expected_sources)},
        "paired_experiment": "not_executed",
        "limitations": [
            "the gate establishes comparability metadata, not task success",
            "no paired cross-harness experiment was authorized or executed",
        ],
    }
