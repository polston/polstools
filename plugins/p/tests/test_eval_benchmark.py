import json
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
