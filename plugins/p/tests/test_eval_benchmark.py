import json
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.benchmark import (BenchmarkBackend, BenchmarkRegistry,
                                  benchmark_storage)  # noqa: E402
from retro_eval.storage import DuckdbParquetCache  # noqa: E402


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

    @unittest.skipUnless(importlib.util.find_spec("duckdb"), "optional DuckDB not installed")
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
