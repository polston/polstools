"""Storage seam with a dependency-free JSONL implementation."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
from dataclasses import fields
from pathlib import Path

from .schema import SCHEMA_VERSION, TraceRecord


def _unique_observations(records):
    """Reject repeated identities without inferring statistical independence."""
    spans, roots, owners = set(), set(), {}
    for record in records:
        if record.span_id in spans:
            raise ValueError("trace store contains a duplicate span identity")
        spans.add(record.span_id)
        owner = owners.setdefault(record.trace_id, record.source)
        if owner != record.source:
            raise ValueError("trace identity belongs to multiple sources")
        if record.span_kind == "trace":
            if record.trace_id in roots:
                raise ValueError("trace store contains multiple roots for one trace")
            roots.add(record.trace_id)
        yield record


class JsonlTraceStore:
    def __init__(self, path: Path):
        self.path = path

    def write(self, records) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                for record in _unique_observations(records):
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
        return self.read_with_digest()[0]

    def read_with_digest(self) -> tuple[list[TraceRecord], str]:
        """Return records and the SHA-256 of the exact bytes parsed.

        Later replacement of the path cannot change this identity. This is not
        a transaction across multiple files or protection from in-place writes.
        """
        records = []
        versions = set()
        try:
            captured = self.path.read_bytes()
            lines = captured.decode("utf-8").splitlines()
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
        return list(_unique_observations(records)), hashlib.sha256(captured).hexdigest()


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
        backend = self._duckdb()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One captured input feeds every query and the manifest. Reading the
        # mutable source again could bind a later extraction to an older cache.
        with tempfile.TemporaryDirectory(prefix=".parquet-capture-",
                                         dir=self.path.parent) as directory:
            captured = Path(directory) / "input.jsonl"
            candidate = Path(directory) / "cache.parquet"
            digest = hashlib.sha256()
            source_bytes = 0
            with source.open("rb") as incoming, captured.open("wb") as outgoing:
                for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                    outgoing.write(chunk)
                    digest.update(chunk)
                    source_bytes += len(chunk)
            source_sql = str(captured).replace("'", "''")
            candidate_sql = str(candidate).replace("'", "''")
            connection = backend.connect()
            try:
                spans = connection.execute(
                    "SELECT count(*) FROM read_json_auto(?, format='newline_delimited')",
                    [str(captured)],
                ).fetchone()[0]
                connection.execute(
                    "COPY (SELECT * FROM read_json_auto('%s', format='newline_delimited')) "
                    "TO '%s' (FORMAT PARQUET)" % (source_sql, candidate_sql)
                )
            finally:
                connection.close()
            stored_bytes = candidate.stat().st_size
            os.replace(candidate, self.path)
            manifest = {
                "schema_version": 1,
                "cache_kind": "duckdb-parquet",
                "source_sha256": digest.hexdigest(),
                "source_bytes": source_bytes,
                "stored_bytes": stored_bytes,
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
