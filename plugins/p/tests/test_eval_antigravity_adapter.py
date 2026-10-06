"""Antigravity evaluation adapter over synthetic step exports."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.adapters.registry import default_registry  # noqa: E402
from retro_eval.pipeline import EvaluationPipeline  # noqa: E402
from retro_eval.reporting import run_deterministic_report  # noqa: E402
from retro_eval.schema import CapabilityState, SpanKind  # noqa: E402

STAMP = "2026-08-01T12:00:00Z"


def step(index, kind, content="", source=None, **fields):
    row = {"step_index": index, "type": kind, "content": content,
           "source": source or ("USER_EXPLICIT" if kind == "USER_INPUT" else "MODEL"),
           "status": "DONE", "created_at": STAMP}
    row.update(fields)
    return row


def write_export(root, session, rows, name="transcript.jsonl"):
    logs = root / session / ".system_generated" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / name
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


CALL = {"name": "read_file", "args": {"path": "example.txt"}}


class AntigravityAdapterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / "brain"
        self.registration = default_registry().get("antigravity")

    def adapter(self, **options):
        return self.registration.create(b"local-salt", options)

    def test_discovery_prefers_full_export_and_skips_history_files(self):
        write_export(self.root, "one", [step(0, "USER_INPUT", "a")])
        full = write_export(self.root, "one", [step(0, "USER_INPUT", "a")],
                            "transcript_full.jsonl")
        short_only = write_export(self.root, "two", [step(0, "USER_INPUT", "b")])
        (self.root / "history.jsonl").write_text("{}\n", encoding="utf-8")
        (self.root / "one" / "transcript.jsonl").write_text("{}\n", encoding="utf-8")
        found = sorted(self.registration.discover(self.root))
        self.assertEqual(sorted([full, short_only]), found)

    def test_steps_normalize_to_prompt_llm_and_tool_spans(self):
        path = write_export(self.root, "one", [
            step(0, "USER_INPUT", "Please inspect the example."),
            step(1, "PLANNER_RESPONSE", "Reading.", tool_calls=[CALL]),
            step(2, "GENERIC", "tool output is not an authoritative result"),
            step(3, "USER_INPUT", "injected", source="SYSTEM"),
            step(4, "SYSTEM_MESSAGE", "setup", source="SYSTEM"),
            step(5, "ERROR_MESSAGE", "provider failure", source="SYSTEM"),
            step(6, "PLANNER_RESPONSE", "Done.", tool_calls=[CALL]),
        ])
        result = self.adapter().read(path, self.root)
        self.assertTrue(result.included)
        kinds = [record.span_kind for record in result.records]
        self.assertEqual(SpanKind.TRACE, kinds[0])
        self.assertEqual(1, kinds.count(SpanKind.PROMPT))
        self.assertEqual(2, kinds.count(SpanKind.LLM))
        self.assertEqual(2, kinds.count(SpanKind.TOOL))
        self.assertNotIn("retro.tool_result", kinds)
        self.assertEqual((1, 2, 0), (result.human_prompt_count,
                                     result.tool_call_count,
                                     result.tool_result_count))
        tools = [r for r in result.records if r.span_kind == SpanKind.TOOL]
        self.assertEqual(tools[0].call_signature, tools[1].call_signature)
        self.assertEqual("read_file", tools[0].tool_kind)
        errors = [r for r in result.records if r.span_kind == "retro.source_error"]
        self.assertEqual(1, len(errors))
        self.assertEqual("error", errors[0].status)

    def test_population_tokens_and_completion_are_reported_honestly(self):
        path = write_export(self.root, "one", [
            step(0, "USER_INPUT", "inspect"),
            step(1, "PLANNER_RESPONSE", "", tool_calls=[CALL]),
        ])
        result = self.adapter().read(path, self.root)
        root_span = result.records[0]
        self.assertEqual("unknown", root_span.main_or_subagent)
        self.assertFalse(result.is_subagent)
        self.assertEqual(0, root_span.input_tokens)
        self.assertEqual("not_observable", root_span.attributes["usage"])
        self.assertEqual("not_observable", root_span.attributes["population"])
        self.assertEqual("open", root_span.status)
        capabilities = self.adapter().capabilities
        for name in ("usage", "cache_usage", "subagent_identity",
                     "tool_result_status", "handoffs"):
            self.assertEqual(CapabilityState.UNAVAILABLE, capabilities[name].state)
        for name in ("human_provenance", "tool_trajectory", "source_completion"):
            self.assertEqual(CapabilityState.AVAILABLE, capabilities[name].state)

    def test_latest_snapshot_per_step_wins_and_malformed_rows_are_ignored(self):
        path = write_export(self.root, "one", [
            step(0, "USER_INPUT", "inspect"),
            step(1, "PLANNER_RESPONSE", "", status="RUNNING", tool_calls=[CALL]),
            step(1, "PLANNER_RESPONSE", "Final response."),
            {"step_index": True, "type": "USER_INPUT", "source": "USER_EXPLICIT"},
            {"step_index": 0}, None,
        ])
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"step_index":')
        result = self.adapter().read(path, self.root)
        self.assertEqual(0, result.tool_call_count)
        self.assertEqual(1, result.human_prompt_count)
        self.assertEqual("complete", result.records[0].status)

    def test_session_without_direct_human_prompt_is_excluded_with_reason(self):
        path = write_export(self.root, "one", [
            step(0, "USER_INPUT", "injected", source="SYSTEM"),
            step(1, "PLANNER_RESPONSE", "reply"),
        ])
        result = self.adapter().read(path, self.root)
        self.assertFalse(result.included)
        self.assertEqual("no direct-human prompt", result.exclusion_reason)
        self.assertEqual((), result.records)

    def test_excluded_session_id_is_the_session_directory_name(self):
        path = write_export(self.root, "Active-One", [step(0, "USER_INPUT", "a")])
        result = self.adapter(excluded_session_ids=["active-one"]).read(path, self.root)
        self.assertFalse(result.included)
        self.assertEqual("active_or_explicitly_excluded", result.exclusion_reason)

    def test_pipeline_routes_exclusions_and_report_keeps_population_unknown(self):
        write_export(self.root, "keep", [step(0, "USER_INPUT", "a"),
                                         step(1, "PLANNER_RESPONSE", "b")])
        write_export(self.root, "drop", [step(0, "USER_INPUT", "a")])
        work = self.base / "work"
        summary = EvaluationPipeline(work).extract(
            roots={"antigravity": self.root}, exclude_session_ids=["drop"])
        self.assertEqual((1, 1), (summary.included_traces, summary.excluded_traces))
        self.assertEqual({"active_or_explicitly_excluded": 1},
                         summary.exclusion_reasons)
        counts = summary.sources["antigravity"]
        self.assertEqual((0, 0, 1), (counts["main"], counts["subagents"],
                                     counts["unclassified_population"]))
        report = run_deterministic_report(work, created_commit="a" * 40)
        population = report["source_populations"]["antigravity"]
        self.assertEqual((1, 0, 0, 1), (
            population["included_traces"], population["main_traces"],
            population["subagent_traces"], population["unclassified_traces"]))
        states = {item["metric_id"]: item["status"]
                  for item in report["coverage"]["antigravity"]}
        missing = {item["metric_id"]: item["missing_capabilities"]
                   for item in report["coverage"]["antigravity"]}
        self.assertIn("usage", missing["input_tokens_per_source_completion"])
        self.assertNotEqual("measured", states["input_tokens_per_source_completion"])
        self.assertNotEqual("measured", states["cache_read_share"])
        # Tool trajectory is observable: one trace is scored, just below minimum n.
        self.assertEqual("insufficient_evidence", states["repeated_call_rate"])

    def test_extract_cli_ingests_antigravity_root(self):
        write_export(self.root, "keep", [step(0, "USER_INPUT", "a"),
                                         step(1, "PLANNER_RESPONSE", "b")])
        work = self.base / "work"
        env = dict(os.environ, RETRO_HOME=str(self.base / "retro-state"))
        completed = subprocess.run(
            ["sh", str(PLUGIN_ROOT / "bin" / "python-launcher"), "-B",
             str(PLUGIN_ROOT / "bin" / "retro-eval-extract"),
             "--work-dir", str(work), "--root", "antigravity=%s" % self.root],
            capture_output=True, text=True, env=env, check=False)
        self.assertEqual(0, completed.returncode, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(1, payload["sources"]["antigravity"]["included"])
        self.assertEqual("unavailable", payload["sources"]["antigravity"]["usage"]["state"])


if __name__ == "__main__":
    unittest.main()
