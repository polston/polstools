"""Every retro-eval entry point follows the shared exit-code convention.

0 ran clean and flagged nothing, 1 ran clean and flagged something, 2 could not
run. Each test runs the real entry point through the plugin's launcher.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PLUGIN_ROOT / "bin" / "python-launcher"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import (build_corpus, claude_assistant, claude_user,  # noqa: E402
                      rollout_assistant, rollout_meta, rollout_user)

FULL_COMMIT = "0123456789abcdef0123456789abcdef01234567"


def run_cli(name, *args):
    # The launcher honours POLSTOOLS_PYTHON, so each entry point runs under the
    # interpreter running the tests; optional-dependency expectations hold.
    env = dict(os.environ, POLSTOOLS_PYTHON=sys.executable)
    return subprocess.run(
        ["sh", str(LAUNCHER), "-B", str(PLUGIN_ROOT / "bin" / name), *map(str, args)],
        capture_output=True, text=True, env=env, timeout=120)


def write_corpus(base: Path):
    start = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    sessions = []
    for index in range(2):
        when = start + timedelta(hours=index)
        sessions.append({"project": "proj", "session": "s%d" % index, "rows": [
            claude_user("Please inspect example %d." % index, when),
            claude_assistant("A long explanation. " * 20, when + timedelta(seconds=5),
                             tools=[("Read", {"file_path": "example.txt"})]),
            claude_user("No, inspect the other example.", when + timedelta(seconds=30)),
            claude_assistant("Done.", when + timedelta(seconds=40)),
        ]})
    build_corpus(base / "claude", sessions)
    codex = base / "codex" / "2026" / "09" / "01"
    codex.mkdir(parents=True)
    stamp = start.isoformat().replace("+00:00", "Z")
    rows = [rollout_meta(stamp), rollout_user("hello there", stamp),
            rollout_assistant("hi", stamp)]
    (codex / "rollout-1.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class CliContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        write_corpus(self.base)
        self.work = self.base / "work"
        # Each entry point runs against its own state, never the caller's.
        isolated = self.base / "isolated"
        isolated.mkdir()
        patcher = mock.patch.dict(os.environ, {
            "RETRO_HOME": str(isolated / "retro"),
            "CLAUDE_CONFIG_DIR": str(isolated / "claude-config"),
            "CODEX_HOME": str(isolated / "codex-config"),
            "RETRO_ANTIGRAVITY_HOME": str(isolated / "antigravity")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def assertCannotRun(self, completed):
        self.assertEqual(2, completed.returncode, completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertTrue(completed.stderr.strip())

    def extract(self, *extra):
        return run_cli("retro-eval-extract", "--work-dir", self.work,
                       "--root", "claude=%s" % (self.base / "claude"),
                       "--root", "codex=%s" % (self.base / "codex"), *extra)

    def test_extract_refuses_a_missing_root_without_creating_the_work_dir(self):
        completed = run_cli("retro-eval-extract", "--work-dir", self.work,
                            "--root", "claude=%s" % (self.base / "missing"))
        self.assertCannotRun(completed)
        self.assertFalse(self.work.exists())

    def test_extract_refuses_an_unregistered_source(self):
        completed = run_cli("retro-eval-extract", "--work-dir", self.work,
                            "--root", "bogus=%s" % (self.base / "claude"))
        self.assertCannotRun(completed)
        self.assertFalse(self.work.exists())

    def test_extract_refuses_a_root_without_a_name(self):
        self.assertCannotRun(run_cli("retro-eval-extract", "--work-dir", self.work,
                                     "--root", str(self.base / "claude")))

    def test_extract_then_report_succeeds_and_excludes_the_active_session(self):
        completed = self.extract("--exclude-session-id", "s1")
        self.assertEqual(0, completed.returncode, completed.stderr)
        summary = json.loads(completed.stdout)
        self.assertEqual(1, summary["sources"]["claude"]["included"])
        self.assertEqual({"active_or_explicitly_excluded": 1},
                         summary["exclusion_reasons"])
        report = self.base / "out" / "report.json"
        completed = run_cli("retro-eval-report", "--work-dir", self.work,
                            "--output", report, "--created-commit", FULL_COMMIT,
                            "--dataset-id", "fixture-v1")
        self.assertEqual(0, completed.returncode, completed.stderr)
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(FULL_COMMIT, payload["dataset_manifest"]["created_commit"])

    def test_abbreviated_commits_are_refused_by_both_commands(self):
        self.assertEqual(0, self.extract().returncode)
        completed = run_cli("retro-eval-report", "--work-dir", self.work,
                            "--output", self.base / "out" / "report.json",
                            "--created-commit", "abc123")
        self.assertCannotRun(completed)
        self.assertFalse((self.base / "out" / "report.json").exists())
        completed = run_cli(
            "retro-eval-labels", "predict-annotations",
            "--source", self.base / "packet.csv",
            "--manifest", self.base / "packet.json",
            "--predictions", self.base / "p.jsonl",
            "--prediction-manifest", self.base / "pm.json",
            "--created-commit", "abc")
        self.assertCannotRun(completed)

    def test_report_on_a_missing_work_dir_cannot_run(self):
        self.assertCannotRun(run_cli(
            "retro-eval-report", "--work-dir", self.base / "missing",
            "--output", self.base / "out" / "report.json",
            "--created-commit", FULL_COMMIT))

    def test_review_without_a_review_directory_cannot_run(self):
        self.assertCannotRun(run_cli(
            "retro-eval-review", "status", "--review-dir", self.base / "missing"))

    def test_benchmark_jsonl_runs_and_unavailable_inputs_cannot_run(self):
        self.assertEqual(0, self.extract().returncode)
        completed = run_cli(
            "retro-eval-benchmark", "--input", self.work / "traces.jsonl",
            "--backend", "jsonl", "--work-dir", self.base / "bench",
            "--output", self.base / "bench.json", "--runs", "1")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("jsonl", json.loads(completed.stdout)["backend"])
        self.assertCannotRun(run_cli(
            "retro-eval-benchmark", "--input", self.base / "missing.jsonl",
            "--backend", "jsonl", "--work-dir", self.base / "bench2",
            "--output", self.base / "bench2.json", "--runs", "1"))
        try:
            import duckdb  # noqa: F401
            expected = 0
        except ImportError:
            expected = 2
        completed = run_cli(
            "retro-eval-benchmark", "--input", self.work / "traces.jsonl",
            "--backend", "duckdb-json", "--work-dir", self.base / "bench3",
            "--output", self.base / "bench3.json", "--runs", "1")
        self.assertEqual(expected, completed.returncode, completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_proposals_with_invalid_candidates_cannot_run(self):
        candidates = self.base / "candidates.json"
        candidates.write_text("{not json", encoding="utf-8")
        self.assertCannotRun(run_cli(
            "retro-eval-proposals", "--candidates", candidates,
            "--json-output", self.base / "out" / "review.json",
            "--markdown-output", self.base / "out" / "review.md"))

    def test_taxonomies_sample_and_assess_follow_the_convention(self):
        self.assertEqual(0, self.extract().returncode)
        packets = self.base / "packets"
        completed = run_cli("retro-eval-taxonomies", "sample",
                            "--traces", self.work / "traces.jsonl",
                            "--output-dir", packets)
        self.assertEqual(1, completed.returncode, completed.stderr)
        self.assertEqual(4, len(json.loads(completed.stdout)["empty_packets"]))
        self.assertCannotRun(run_cli("retro-eval-review", "status",
                                     "--review-dir", packets, "--include-taxonomies"))
        manifest = sorted(packets.glob("*calibration*-manifest.json"))[0]
        source = manifest.with_name(manifest.name.replace("-manifest.json", ".csv"))
        completed = run_cli("retro-eval-taxonomies", "assess",
                            "--source", source, "--manifest", manifest)
        self.assertEqual(1, completed.returncode, completed.stderr)
        self.assertEqual("needs_more", json.loads(completed.stdout)["status"])
        self.assertCannotRun(run_cli("retro-eval-taxonomies", "sample",
                                     "--traces", self.base / "missing.jsonl",
                                     "--output-dir", self.base / "packets2"))

    def test_instructions_write_and_reject_an_invalid_timestamp(self):
        rules = self.base / "rules.md"
        rules.write_text("standing rule\n", encoding="utf-8")
        output = self.base / "instructions" / "v1.json"
        completed = run_cli("retro-eval-instructions", "write", "--output", output,
                            "--activated-at", "2026-09-01T00:00:00Z",
                            "--source", "standing_instructions=%s" % rules,
                            "--version", "standing_instructions=v1")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(1, json.loads(completed.stdout)["sources"])
        self.assertCannotRun(run_cli(
            "retro-eval-instructions", "write", "--output", self.base / "v2.json",
            "--activated-at", "yesterday",
            "--source", "standing_instructions=%s" % rules,
            "--version", "standing_instructions=v1"))

    def test_hook_events_measures_owned_lifecycles_and_rejects_content(self):
        events = self.base / "hooks" / "events.jsonl"
        events.parent.mkdir()
        common = {"schema_version": 1, "source": "polstools.format",
                  "hook_id": "format.session-start", "hook_version": "1",
                  "trigger_kind": "session_start", "invocation_id": "i1"}
        rows = [dict(common, event="opportunity", observed_at_ns=1),
                dict(common, event="start", observed_at_ns=2),
                dict(common, event="end", observed_at_ns=3, status="ok",
                     latency_ms=1, injected_bytes=10, content_sha256="a" * 64)]
        events.write_text("".join(json.dumps(row) + "\n" for row in rows),
                          encoding="utf-8")
        completed = run_cli("retro-eval-hook-events", "--events", events,
                            "--expected-invocations", "1",
                            "--baseline-normalized-bytes", "1000",
                            "--output", self.base / "hooks" / "report.json")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(1.0, json.loads(completed.stdout)["capture_recall"])
        rows[0]["message"] = "content must never be captured"
        events.write_text("".join(json.dumps(row) + "\n" for row in rows),
                          encoding="utf-8")
        self.assertCannotRun(run_cli(
            "retro-eval-hook-events", "--events", events,
            "--expected-invocations", "1", "--baseline-normalized-bytes", "1000",
            "--output", self.base / "hooks" / "report2.json"))

    def test_interpretations_reject_unreadable_cards(self):
        self.assertCannotRun(run_cli(
            "retro-eval-interpretations", "build",
            "--cards", self.base / "missing.json",
            "--output-dir", self.base / "cards"))

    def test_labels_reject_an_unknown_rubric(self):
        self.assertCannotRun(run_cli(
            "retro-eval-labels", "import-annotations",
            "--source", self.base / "packet.csv", "--manifest", self.base / "m.json",
            "--labels", self.base / "labels.jsonl", "--rubric-id", "no_such_rubric"))


if __name__ == "__main__":
    unittest.main()
