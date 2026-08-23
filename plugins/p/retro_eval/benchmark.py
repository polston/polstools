"""Reproducible, content-free storage benchmark for normalized traces."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .pipeline import _inside_repository


WORKLOADS = (
    "full_ingest", "incremental_append", "30_day_filter",
    "grouped_aggregation", "trace_reconstruction", "dataset_split",
    "report_generation",
)


@dataclass(frozen=True)
class BenchmarkBackend:
    name: str
    factory: Callable[[Path, Path], tuple[dict, Callable[[], None] | None,
                                         Path | None]]


class BenchmarkRegistry:
    def __init__(self, backends=()):
        backends = tuple(backends)
        self._items = {item.name: item for item in backends}
        if len(self._items) != len(backends):
            raise ValueError("duplicate benchmark backend")

    @property
    def names(self):
        return tuple(self._items)

    def create(self, name: str, path: Path, work_dir: Path):
        try:
            backend = self._items[name]
        except KeyError as exc:
            raise ValueError("unsupported benchmark backend") from exc
        return backend.factory(path, work_dir)


def default_benchmark_registry():
    return BenchmarkRegistry((
        BenchmarkBackend(
            "jsonl", lambda path, work_dir:
            (_stdlib_actions(path, work_dir), None, None)),
        BenchmarkBackend(
            "duckdb-json", lambda path, work_dir:
            _duckdb_actions(path, work_dir, "duckdb-json")),
        BenchmarkBackend(
            "duckdb-parquet", lambda path, work_dir:
            _duckdb_actions(path, work_dir, "duckdb-parquet")),
    ))


def _rows(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _stdlib_actions(path: Path, work_dir: Path):
    first = next(_rows(path))
    target = str(first["trace_id"])
    all_rows = list(_rows(path))
    latest = max(str(row.get("started_at") or "") for row in all_rows)
    cutoff = str(int(latest[:4]) - 1) + latest[4:] if latest else ""
    sample = all_rows[-min(1000, len(all_rows)):]

    def full_ingest():
        return sum(1 for _ in _rows(path))

    def incremental_append():
        output = work_dir / "append.jsonl"
        with output.open("w", encoding="utf-8", newline="\n") as handle:
            for row in sample:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        return len(sample)

    def recent_filter():
        return sum(str(row.get("started_at") or "") >= cutoff for row in _rows(path))

    def grouped():
        return len(Counter((row.get("source"), row.get("span_kind"))
                           for row in _rows(path)))

    def reconstruct():
        return sum(str(row.get("trace_id")) == target for row in _rows(path))

    def split():
        traces = {str(row.get("trace_id")) for row in _rows(path)}
        return sum(int(hashlib.sha256(value.encode()).hexdigest()[:8], 16) % 100 < 70
                   for value in traces)

    def report():
        totals = Counter()
        for row in _rows(path):
            totals[str(row.get("source") or "unknown")] += (
                int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0)
            )
        return len(totals)

    return dict(zip(WORKLOADS, (
        full_ingest, incremental_append, recent_filter, grouped, reconstruct,
        split, report,
    )))


def _duckdb_actions(path: Path, work_dir: Path, backend: str):
    try:
        import duckdb
    except ImportError as exc:
        raise ValueError("DuckDB backend requested but duckdb is not installed") from exc
    connection = duckdb.connect(str(work_dir / "benchmark.duckdb"))
    quoted = str(path).replace("'", "''")
    json_source = "read_json_auto('%s', format='newline_delimited')" % quoted
    parquet = work_dir / "traces.parquet"
    parquet_quoted = str(parquet).replace("'", "''")
    if backend == "duckdb-parquet":
        connection.execute("COPY (SELECT * FROM %s) TO '%s' (FORMAT PARQUET)" %
                           (json_source, parquet_quoted))
        source = "read_parquet('%s')" % parquet_quoted
    else:
        source = json_source
    target, latest = connection.execute(
        "SELECT trace_id, max(started_at::VARCHAR) OVER () FROM %s LIMIT 1" % source
    ).fetchone()
    cutoff = str(int(str(latest)[:4]) - 1) + str(latest)[4:] if latest else ""

    def scalar(sql):
        return connection.execute(sql).fetchone()[0]

    def full_ingest():
        if backend == "duckdb-parquet":
            candidate = work_dir / "ingest.parquet"
            q = str(candidate).replace("'", "''")
            result = scalar("SELECT count(*) FROM %s" % json_source)
            connection.execute("COPY (SELECT * FROM %s) TO '%s' (FORMAT PARQUET)" %
                               (json_source, q))
            candidate.unlink(missing_ok=True)
            return result
        return scalar("SELECT count(*) FROM %s" % source)

    def incremental_append():
        connection.execute("CREATE OR REPLACE TEMP TABLE append_sample AS SELECT * FROM %s LIMIT 0" % source)
        connection.execute("INSERT INTO append_sample SELECT * FROM %s LIMIT 1000" % source)
        return scalar("SELECT count(*) FROM append_sample")

    escaped_target = str(target).replace("'", "''")
    escaped_cutoff = cutoff.replace("'", "''")
    actions = {
        "full_ingest": full_ingest,
        "incremental_append": incremental_append,
        "30_day_filter": lambda: scalar(
            "SELECT count(*) FROM %s WHERE started_at::VARCHAR >= '%s'" %
            (source, escaped_cutoff)),
        "grouped_aggregation": lambda: scalar(
            "SELECT count(*) FROM (SELECT source, span_kind FROM %s GROUP BY ALL)" % source),
        "trace_reconstruction": lambda: scalar(
            "SELECT count(*) FROM %s WHERE trace_id = '%s'" % (source, escaped_target)),
        "dataset_split": lambda: scalar(
            "SELECT count(*) FROM (SELECT DISTINCT trace_id FROM %s) WHERE hash(trace_id) %% 100 < 70" % source),
        "report_generation": lambda: scalar(
            "SELECT count(*) FROM (SELECT source, sum(coalesce(input_tokens,0) + coalesce(output_tokens,0)) FROM %s GROUP BY source)" % source),
    }
    return actions, connection.close, parquet


def _measurement(values):
    ordered = sorted(values)
    return {
        "median_seconds": statistics.median(ordered),
        "p95_seconds": ordered[max(0, math.ceil(len(ordered) * .95) - 1)],
        "samples_seconds": ordered,
    }


def benchmark_storage(path: Path, backend: str, work_dir: Path, *, runs: int = 5,
                      registry: BenchmarkRegistry | None = None):
    path = path.resolve()
    work_dir = work_dir.resolve()
    if _inside_repository(work_dir):
        raise ValueError("benchmark work directory must be outside a repository")
    if runs < 1:
        raise ValueError("runs must be positive")
    work_dir.mkdir(parents=True, exist_ok=True)
    registry = registry or default_benchmark_registry()
    actions, cleanup, stored_artifact = registry.create(backend, path, work_dir)
    timings = {name: [] for name in WORKLOADS}
    try:
        for action in actions.values():
            action()
        for _ in range(runs):
            for name, action in actions.items():
                started = time.perf_counter()
                action()
                timings[name].append(time.perf_counter() - started)
    finally:
        if cleanup is not None:
            cleanup()
    trace_ids = {str(row["trace_id"]) for row in _rows(path)}
    result = {
        "schema_version": 1,
        "backend": backend,
        "runs": runs,
        "population": {"spans": sum(1 for _ in _rows(path)), "traces": len(trace_ids)},
        "input_bytes": path.stat().st_size,
        "stored_bytes": (stored_artifact.stat().st_size
                         if stored_artifact and stored_artifact.exists()
                         else path.stat().st_size),
        "workloads": {name: _measurement(values) for name, values in timings.items()},
    }
    return result


def main(argv=None):
    registry = default_benchmark_registry()
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--backend", choices=registry.names, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args(argv)
    result = benchmark_storage(
        args.input, args.backend, args.work_dir, runs=args.runs, registry=registry)
    if _inside_repository(args.output.parent.resolve()):
        raise ValueError("benchmark output must be outside a repository")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"backend": result["backend"], **result["population"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
