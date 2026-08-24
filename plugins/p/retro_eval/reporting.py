"""Content-free deterministic evaluation reports."""

from __future__ import annotations

import hashlib
import argparse
import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from .catalog import load_metric_catalogue, load_rubric_catalogue
from .dataset import DatasetManifest, load_dataset_policy
from .scorers import default_scorers
from .storage import JsonlTraceStore


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside_repository(path: Path) -> bool:
    resolved = path.resolve()
    return any((parent / ".git").exists() for parent in (resolved, *resolved.parents))


def _capability_observable(value) -> bool:
    return isinstance(value, dict) and value.get("state") in {
        "available", "version_floor"
    }


def _coverage(catalogue, results, capabilities):
    by_id = {result.scorer_id: result for result in results}
    rows = []
    for metric in catalogue.metrics:
        missing = sorted(
            name for name in metric.source_capabilities
            if not _capability_observable(capabilities.get(name))
        )
        result = by_id.get(metric.id)
        if result is not None:
            status = result.label if result.abstained else "measured"
            reason = result.reason
        elif missing:
            status = "not_observable"
            reason = "missing source capabilities"
        else:
            status = "not_scored"
            reason = "no scorer registered for this metric version"
        rows.append({
            "metric_id": metric.id,
            "metric_version": metric.version,
            "domain": metric.domain,
            "status": status,
            "reason": reason,
            "required_capabilities": list(metric.source_capabilities),
            "missing_capabilities": missing,
            "minimum_n": metric.minimum_n,
            "validation_dataset": metric.validation_dataset,
        })
    return rows


def _dataset_manifest(work_dir, records, extraction, results_by_source,
                      rubric_catalogue, created_commit, dataset_id):
    timestamps = [value for record in records
                  for value in (record.started_at, record.ended_at)
                  if value is not None]
    source_rows = extraction.get("sources") or {}
    fingerprint_sources = sorted(
        source for source, values in source_rows.items()
        if isinstance(values, dict) and values.get("source_set_sha256")
    )
    fingerprint_failures = sum(
        int(values.get("source_fingerprint_failures") or 0)
        for values in source_rows.values() if isinstance(values, dict))
    salt_path = work_dir / "id-salt.bin"
    if (not timestamps or len(fingerprint_sources) != len(source_rows)
            or fingerprint_failures or not salt_path.exists()
            or not created_commit):
        return {
            "schema_version": 1,
            "status": "unavailable",
            "reason": "closed timestamps, fingerprints, split seed, or creation commit are unavailable",
        }
    start, end = min(timestamps), max(timestamps)
    if end <= start:
        end = start + timedelta(microseconds=1)
    scorer_versions = {}
    for results in results_by_source.values():
        for result in results:
            scorer_versions[result.scorer_id] = result.scorer_version
    policy = load_dataset_policy()
    seed_fingerprint = _sha256(salt_path)
    manifest = DatasetManifest(
        dataset_id=dataset_id,
        schema_versions=tuple(sorted({record.schema_version for record in records})),
        adapter_versions=dict(sorted({record.source: record.adapter_version
                                      for record in records}.items())),
        start=start,
        end=end,
        seed=seed_fingerprint,
        inclusion_predicates=("adapter_result.included=true",),
        exclusion_predicates=tuple(sorted(
            str(value) for value in (extraction.get("exclusion_reasons") or {}))),
        population=int(extraction.get("included_traces") or 0),
        excluded_population=int(extraction.get("excluded_traces") or 0),
        rubric_versions={item.id: item.version for item in rubric_catalogue.rubrics},
        scorer_versions=scorer_versions,
        source_fingerprints=tuple(
            str(source_rows[source]["source_set_sha256"])
            for source in fingerprint_sources),
        content_policy="content_free; source transcripts remain external",
        created_commit=created_commit,
        split_policy={
            "algorithm": "HMAC-SHA256",
            "calibration_share": policy.calibration_share,
            "fingerprint_source_order": fingerprint_sources,
            "seed_material": "external id-salt.bin",
        },
        instruction_manifest_sha256=str(
            extraction.get("instruction_manifest_sha256") or ""),
        instruction_manifest_coverage=(
            dict(extraction["instruction_manifest_coverage"])
            if extraction.get("instruction_manifest_coverage") is not None
            else None),
    )
    return manifest.to_dict()


def run_deterministic_report(work_dir: Path, *, registry=None,
                             metric_catalogue=None, rubric_catalogue=None,
                             created_commit="", dataset_id="cross-harness-v1"
                             ) -> dict[str, object]:
    trace_path = work_dir / "traces.jsonl"
    extraction_path = work_dir / "extraction.json"
    records = JsonlTraceStore(trace_path).read()
    try:
        extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("extraction summary is invalid") from exc
    extraction_schema = extraction.get("schema_version")
    if extraction_schema not in {1, 2, 3}:
        raise ValueError("unsupported extraction summary schema")
    instruction_manifest_sha256 = str(
        extraction.get("instruction_manifest_sha256") or "")
    coverage = extraction.get("instruction_manifest_coverage")
    if ((extraction_schema in {2, 3}) != bool(instruction_manifest_sha256)
            or (extraction_schema == 3) != (coverage is not None)):
        raise ValueError("extraction instruction provenance schema mismatch")
    by_source = defaultdict(list)
    for record in records:
        by_source[record.source].append(record)
    registry = registry or default_scorers()
    metric_catalogue = metric_catalogue or load_metric_catalogue(
        Path(__file__).resolve().parents[1] / "rubrics" / "metrics.json")
    rubric_catalogue = rubric_catalogue or load_rubric_catalogue(
        Path(__file__).resolve().parents[1] / "rubrics" / "rubrics.json")
    reports = {}
    raw_results = {}
    coverage = {}
    coverage_summary = {}
    for source, source_records in sorted(by_source.items()):
        raw_capabilities = extraction.get("sources", {}).get(source, {})
        capabilities = {
            key: value for key, value in raw_capabilities.items()
            if isinstance(value, dict) and "state" in value
        }
        results = registry.score(source_records, capabilities=capabilities)
        raw_results[source] = results
        reports[source] = [result.to_dict() for result in results]
        coverage[source] = _coverage(metric_catalogue, results, capabilities)
        coverage_summary[source] = dict(sorted(Counter(
            item["status"] for item in coverage[source]
        ).items()))
    report = {
        "schema_version": 1,
        "manifest": {
            "trace_sha256": _sha256(trace_path),
            "trace_schema_versions": sorted({record.schema_version for record in records}),
            "adapter_versions": dict(sorted({record.source: record.adapter_version
                                             for record in records}.items())),
            "spans": len(records),
            "traces": len({record.trace_id for record in records}),
        },
        "sources": reports,
        "coverage": coverage,
        "coverage_summary": coverage_summary,
    }
    if instruction_manifest_sha256:
        report["manifest"]["instruction_manifest_sha256"] = (
            instruction_manifest_sha256)
    report["dataset_manifest"] = _dataset_manifest(
        work_dir, records, extraction, raw_results, rubric_catalogue,
        created_commit, dataset_id)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--created-commit", required=True)
    parser.add_argument("--dataset-id", default="cross-harness-v1")
    args = parser.parse_args(argv)
    if _inside_repository(args.output.parent):
        raise ValueError("evaluation reports must be written outside a repository")
    report = run_deterministic_report(
        args.work_dir, created_commit=args.created_commit,
        dataset_id=args.dataset_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({"spans": report["manifest"]["spans"],
                      "traces": report["manifest"]["traces"],
                      "sources": sorted(report["sources"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
