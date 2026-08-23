"""Storage seam with a dependency-free JSONL implementation."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
from dataclasses import fields
from pathlib import Path

from .schema import SCHEMA_VERSION, TraceRecord


class JsonlTraceStore:
    def __init__(self, path: Path):
        self.path = path

    def write(self, records) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                for record in records:
                    handle.write(json.dumps(record.to_dict(), sort_keys=True,
                                            separators=(",", ":")) + "\n")
            os.replace(temp_name, self.path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def read(self) -> list[TraceRecord]:
        records = []
        versions = set()
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError("trace store is unreadable") from exc
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                record = TraceRecord.from_dict(payload)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError("trace store contains an invalid record") from exc
            versions.add(record.schema_version)
            records.append(record)
        if versions and versions != {SCHEMA_VERSION}:
            raise ValueError("trace store contains mixed or stale schema versions")
        return records


class DuckdbParquetCache:
    """Optional regenerable analytical cache; JSONL remains canonical."""

    def __init__(self, path: Path):
        self.path = path.resolve()
        if any((parent / ".git").exists()
               for parent in (self.path.parent, *self.path.parent.parents)):
            raise ValueError("analytical cache must be outside a repository")

    def _duckdb(self):
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("Parquet cache requires optional duckdb dependency") from exc
        return duckdb

    def refresh(self, jsonl_path: Path) -> dict[str, object]:
        source = jsonl_path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        candidate = self.path.with_name(self.path.name + ".new")
        source_sql = str(source).replace("'", "''")
        candidate_sql = str(candidate).replace("'", "''")
        connection = self._duckdb().connect()
        try:
            spans = connection.execute(
                "SELECT count(*) FROM read_json_auto(?, format='newline_delimited')",
                [str(source)],
            ).fetchone()[0]
            connection.execute(
                "COPY (SELECT * FROM read_json_auto('%s', format='newline_delimited')) "
                "TO '%s' (FORMAT PARQUET)" % (source_sql, candidate_sql)
            )
        finally:
            connection.close()
        os.replace(candidate, self.path)
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        manifest = {
            "schema_version": 1,
            "cache_kind": "duckdb-parquet",
            "source_sha256": digest.hexdigest(),
            "source_bytes": source.stat().st_size,
            "stored_bytes": self.path.stat().st_size,
            "spans": int(spans),
        }
        manifest_path = self.path.with_suffix(self.path.suffix + ".manifest.json")
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return manifest

    def group_counts(self, field_names: tuple[str, ...]) -> dict[tuple[object, ...], int]:
        allowed = {item.name for item in fields(TraceRecord)}
        if not field_names or any(name not in allowed for name in field_names):
            raise ValueError("group fields must be normalized trace fields")
        selected = ", ".join('"%s"' % name for name in field_names)
        path_sql = str(self.path).replace("'", "''")
        connection = self._duckdb().connect()
        try:
            rows = connection.execute(
                "SELECT %s, count(*) FROM read_parquet('%s') GROUP BY %s" %
                (selected, path_sql, selected)
            ).fetchall()
        finally:
            connection.close()
        return {tuple(row[:-1]): int(row[-1]) for row in rows}
