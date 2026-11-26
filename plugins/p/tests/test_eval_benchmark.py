import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from retro_eval.benchmark import (BenchmarkBackend, BenchmarkRegistry,
                                  benchmark_storage)  # noqa: E402
from retro_eval.storage import DuckdbParquetCache  # noqa: E402
from eval_optional import requires  # noqa: E402


class StorageBenchmarkTests(unittest.TestCase):
    def test_jsonl_benchmark_is_reproducible_and_content_free(self):
        rows = [
            {"schema_version": 1, "trace_id": "a", "span_id": "1",
             "source": "one", "span_kind": "trace", "started_at": "2026-01-01T00:00:00Z",
             "input_tokens": 3, "output_tokens": 2},
            {"schema_version": 1, "trace_id": "a", "span_id": "2",
             "source": "one", "span_kind": "tool", "started_at": "2026-01-02T00:00:00Z",
             "input_tokens": 0, "output_tokens": 0},
            {"schema_version": 1, "trace_id": "b", "span_id": "3",
             "source": "two", "span_kind": "trace", "started_at": "2025-01-01T00:00:00Z",
             "input_tokens": 5, "output_tokens": 1},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "traces.jsonl"
            source.write_text("".join(json.dumps(row) + "\n" for row in rows),
                              encoding="utf-8")
            result = benchmark_storage(source, "jsonl", root / "work", runs=2)
        self.assertEqual(3, result["population"]["spans"])
        self.assertEqual(2, result["population"]["traces"])
        self.assertEqual(2, result["runs"])
        self.assertEqual(
            {"full_ingest", "incremental_append", "30_day_filter",
             "grouped_aggregation", "trace_reconstruction", "dataset_split",
             "report_generation"},
            set(result["workloads"]),
        )
        self.assertNotIn("trace_id", json.dumps(result))
        for measurement in result["workloads"].values():
            self.assertIn("median_seconds", measurement)
            self.assertIn("p95_seconds", measurement)

    def assert_thirty_day_window(self, backend):
        samples = (
            (["2024-03-31T00:00:00Z", "2024-03-01T00:00:00Z",
              "2024-02-29T23:59:59.999999Z", "2024-03-01T01:00:00+01:00",
              "2024-03-31T01:00:00+02:00", "2023-10-01T00:00:00Z", None], 4),
            ([None, None], 0),
            (["0001-01-01T00:00:00Z", None], 1),
            (["0001-01-01T00:00:00+01:00", None], 1),
            (["9999-12-31T23:59:59-01:00", None], 1),
        )
        for timestamps, expected in samples:
            with self.subTest(backend=backend, expected=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                rows = [{"trace_id": "sample", "span_id": str(i), "source": "authored",
                         "span_kind": "trace", "started_at": value,
                         "input_tokens": 0, "output_tokens": 0}
                        for i, value in enumerate(timestamps)]
                source = root / 'traces.jsonl'
                source.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                result = benchmark_storage(source, backend, root / 'work', runs=1)
                self.assertEqual(result['answers']['30_day_filter'], expected)
                self.assertEqual(result['workload_version'], 2)

    def test_stdlib_filter_is_thirty_days_with_offsets_and_unknown_time(self):
        self.assert_thirty_day_window('jsonl')

    @requires("duckdb")
    def test_duckdb_filters_meet_independent_thirty_day_oracle(self):
        for backend in ('duckdb-json', 'duckdb-parquet'):
            self.assert_thirty_day_window(backend)

    def test_benchmark_rejects_unknown_timestamp_zone_before_measurement(self):
        for value in ("2026-01-01T00:00:00", "invalid", "", 42):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / 'traces.jsonl'
                source.write_text(json.dumps({'trace_id': 'sample', 'started_at': value}) + '\n')
                with self.assertRaisesRegex(ValueError, 'known timezone'):
                    benchmark_storage(source, 'jsonl', root / 'work', runs=1)

    @requires("duckdb")
    def test_backends_reject_noncanonical_timestamp_syntax_consistently(self):
        for value in ("20260101T00:00:00+00:00", "2026-W01-4T00:00:00+00:00",
                      "2026-01-01🐈00:00:00+00:00",
                      "2026-01-01T00:00:00+00:00:00.5",
                      "2026-01-01T00:00:00+01:99"):
            for backend in ('jsonl', 'duckdb-json', 'duckdb-parquet'):
                with self.subTest(value=value, backend=backend), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    source = root / 'traces.jsonl'
                    source.write_text(json.dumps({'trace_id': 'sample', 'started_at': value}) + '\n')
                    with self.assertRaisesRegex(ValueError, 'canonical'):
                        benchmark_storage(source, backend, root / 'work', runs=1)

    def write_reference_traces(self, root):
        rows = [
            {"schema_version": 1, "trace_id": "a", "span_id": "1",
             "source": "one", "span_kind": "trace",
             "started_at": "2026-01-01T00:00:00Z", "input_tokens": 3,
             "output_tokens": 2},
            {"schema_version": 1, "trace_id": "a", "span_id": "2",
             "source": "one", "span_kind": "tool",
             "started_at": "2026-01-02T00:00:00Z", "input_tokens": 0,
             "output_tokens": 0},
            {"schema_version": 1, "trace_id": "b", "span_id": "3",
             "source": "two", "span_kind": "trace",
             "started_at": "2025-01-01T00:00:00Z", "input_tokens": 5,
             "output_tokens": 1},
        ]
        source = root / "traces.jsonl"
        source.write_text("".join(json.dumps(row) + "\n" for row in rows),
                          encoding="utf-8")
        return source

    def test_benchmark_records_the_answer_of_every_workload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = benchmark_storage(self.write_reference_traces(root), "jsonl",
                                       root / "work", runs=1)
        self.assertEqual({
            "full_ingest": 3, "incremental_append": 3, "30_day_filter": 2,
            "grouped_aggregation": 3, "trace_reconstruction": 2,
            "dataset_split": 2, "report_generation": 2,
        }, result["answers"])

    def write_discriminating_traces(self, root):
        """Sixty traces whose spans are interleaved, over three sources and
        two years, so a workload that hashes, filters, or picks its target
        trace differently from the stdlib reference gives a different count."""
        rows = []
        for span in range(2):
            for index in range(60):
                rows.append({
                    "schema_version": 1, "trace_id": "trace-%02d" % index,
                    "span_id": "%02d-%d" % (index, span),
                    "source": ("one", "two", "three")[index % 3],
                    "span_kind": ("trace", "tool")[span],
                    "started_at": "%d-%02d-%02dT%02d:00:00Z" % (
                        2024 + index % 3, 1 + index % 12, 1 + index % 28,
                        index % 24),
                    "input_tokens": index, "output_tokens": span})
        source = root / "traces.jsonl"
        source.write_text("".join(json.dumps(row) + "\n" for row in rows),
                          encoding="utf-8")
        return source

    @requires("duckdb")
    def test_duckdb_backends_answer_like_the_stdlib_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write_discriminating_traces(root)
            reference = benchmark_storage(source, "jsonl", root / "jsonl", runs=1)
            for backend in ("duckdb-json", "duckdb-parquet"):
                with self.subTest(backend=backend):
                    result = benchmark_storage(source, backend, root / backend, runs=1)
                    self.assertEqual(reference["answers"], result["answers"])

    def test_work_directory_inside_repository_is_rejected(self):
        with self.assertRaises(ValueError):
            benchmark_storage(Path("missing.jsonl"), "jsonl",
                              PLUGIN_ROOT / "benchmark-work", runs=1)

    def test_backend_registry_accepts_an_additive_backend_without_dispatch_edits(self):
        def factory(path, work_dir):
            actions = {name: (lambda: 1) for name in (
                "full_ingest", "incremental_append", "30_day_filter",
                "grouped_aggregation", "trace_reconstruction", "dataset_split",
                "report_generation",
            )}
            return actions, None, None

        registry = BenchmarkRegistry((BenchmarkBackend("custom", factory),))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "traces.jsonl"
            source.write_text(json.dumps({
                "trace_id": "a", "started_at": "2026-01-01T00:00:00Z"
            }) + "\n", encoding="utf-8")
            result = benchmark_storage(
                source, "custom", root / "work", runs=1, registry=registry)
        self.assertEqual("custom", result["backend"])

    def test_cache_unavailable_backend_does_not_capture_or_create_output(self):
        class UnavailableCache(DuckdbParquetCache):
            def _duckdb(self):
                raise RuntimeError("optional backend unavailable")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = UnavailableCache(root / "output" / "cache.parquet")
            with self.assertRaisesRegex(RuntimeError, "optional backend unavailable"):
                cache.refresh(root / "missing.jsonl")
            self.assertEqual(list(root.iterdir()), [])

    @requires("duckdb")
    def test_cache_binds_count_conversion_and_hash_to_one_input(self):
        import duckdb
        for trigger in ("after_count", "after_copy"):
            with self.subTest(trigger=trigger), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = self.write_reference_traces(root)
                original = source.read_bytes()

                class Connection:
                    def __init__(self):
                        self.real = duckdb.connect()
                        self.fired = False

                    def execute(self, sql, *args):
                        result = self.real.execute(sql, *args)
                        match = (sql.startswith("SELECT count(*)") if trigger == "after_count"
                                 else sql.startswith("COPY "))
                        if match and not self.fired:
                            self.fired = True
                            replacement = root / "replacement.jsonl"
                            replacement.write_bytes(original.splitlines()[0] + b"\n")
                            replacement.replace(source)
                        return result

                    def close(self):
                        self.real.close()

                class Module:
                    connect = Connection

                class Cache(DuckdbParquetCache):
                    def _duckdb(self):
                        return Module

                cache = Cache(root / "cache.parquet")
                manifest = cache.refresh(source)
                with duckdb.connect() as connection:
                    count = connection.execute("SELECT count(*) FROM read_parquet(?)",
                                               [str(cache.path)]).fetchone()[0]
                self.assertEqual(count, 3)
                self.assertEqual(manifest["spans"], count)
                self.assertEqual(manifest["source_sha256"], hashlib.sha256(original).hexdigest())
                self.assertEqual(manifest["source_bytes"], len(original))
                self.assertNotEqual(source.read_bytes(), original)
                self.assertEqual({p.name for p in root.iterdir()},
                                 {"traces.jsonl", "cache.parquet", "cache.parquet.manifest.json"})

    @requires("duckdb")
    def test_failed_cache_capture_or_conversion_preserves_prior_files(self):
        import duckdb
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write_reference_traces(root)
            cache = DuckdbParquetCache(root / "cache.parquet")
            cache.refresh(source)
            manifest_path = root / "cache.parquet.manifest.json"
            before = (cache.path.read_bytes(), manifest_path.read_bytes())
            malformed = root / "malformed.jsonl"
            malformed.write_text("{invalid}\n")
            for candidate in (root / "missing.jsonl", malformed):
                with self.subTest(candidate=candidate.name):
                    names = {p.name for p in root.iterdir()}
                    with self.assertRaises((OSError, duckdb.Error)):
                        cache.refresh(candidate)
                    self.assertEqual(before, (cache.path.read_bytes(), manifest_path.read_bytes()))
                    self.assertEqual(names, {p.name for p in root.iterdir()})

    @requires("duckdb")
    def test_parquet_cache_is_regenerable_and_query_fields_are_validated(self):
        rows = [
            {"schema_version": 1, "trace_id": "a", "span_id": "1",
             "parent_span_id": None, "source": "one", "adapter_version": 1,
             "source_version": "1", "span_kind": "trace", "started_at": None,
             "ended_at": None, "sequence": 0},
            {"schema_version": 1, "trace_id": "b", "span_id": "2",
             "parent_span_id": None, "source": "two", "adapter_version": 1,
             "source_version": "1", "span_kind": "trace", "started_at": None,
             "ended_at": None, "sequence": 0},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "traces.jsonl"
            source.write_text("".join(json.dumps(row) + "\n" for row in rows),
                              encoding="utf-8")
            cache = DuckdbParquetCache(root / "traces.parquet")
            manifest = cache.refresh(source)
            self.assertEqual(2, manifest["spans"])
            self.assertEqual({("one",): 1, ("two",): 1},
                             cache.group_counts(("source",)))
            with self.assertRaises(ValueError):
                cache.group_counts(("source; DROP TABLE traces",))


if __name__ == "__main__":
    unittest.main()
