"""The documented label chain runs from shipped commands alone.

retro-eval-extract -> retro-eval-labels sample -> predict-annotations ->
import-annotations -> compare, on synthetic Claude, Codex, and Antigravity
sessions.
"""

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PLUGIN_ROOT / "bin" / "python-launcher"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import (build_corpus, claude_assistant, claude_user,  # noqa: E402
                      rollout_assistant, rollout_meta, rollout_user)

FULL_COMMIT = "0123456789abcdef0123456789abcdef01234567"
LONG_ANSWER = "A long explanation of the change. " * 12


def labels(env, *args):
    return subprocess.run(
        ["sh", str(LAUNCHER), "-B", str(PLUGIN_ROOT / "bin" / "retro-eval-labels"),
         *map(str, args)], capture_output=True, text=True, timeout=120, env=env)


def write_sources(base: Path):
    start = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    sessions = []
    for index in range(3):
        when = start + timedelta(hours=index)
        sessions.append({"project": "proj", "session": "s%d" % index, "rows": [
            claude_user("Please inspect example %d." % index, when),
            claude_assistant(LONG_ANSWER, when + timedelta(seconds=5)),
            claude_user("No, use key %s instead." % ("k" * 40),
                        when + timedelta(seconds=30)),
            claude_assistant("Done.", when + timedelta(seconds=40)),
        ]})
    build_corpus(base / "claude", sessions)
    codex = base / "codex" / "2026" / "09" / "01"
    codex.mkdir(parents=True)
    stamp = start.isoformat().replace("+00:00", "Z")
    rows = [rollout_meta(stamp),
            rollout_user("<environment_context>machine</environment_context>", stamp),
            rollout_user("hello there", stamp), rollout_assistant(LONG_ANSWER, stamp),
            rollout_user("looks good", stamp)]
    (codex / "rollout-1.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    logs = base / "agy" / "session-a" / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    steps = [
        {"step_index": 0, "type": "USER_INPUT", "source": "USER_EXPLICIT",
         "status": "DONE", "created_at": stamp, "content": "inspect the example"},
        {"step_index": 1, "type": "PLANNER_RESPONSE", "source": "MODEL",
         "status": "DONE", "created_at": stamp, "content": LONG_ANSWER},
        {"step_index": 2, "type": "USER_INPUT", "source": "USER_EXPLICIT",
         "status": "DONE", "created_at": stamp, "content": "which one?"},
    ]
    (logs / "transcript.jsonl").write_text(
        "".join(json.dumps(step) + "\n" for step in steps), encoding="utf-8")


class LabelChainTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        write_sources(self.base)
        self.env = dict(os.environ, RETRO_HOME=str(self.base / "retro-state"))
        self.work = self.base / "work"
        self.roots = (("claude", self.base / "claude"), ("codex", self.base / "codex"),
                      ("antigravity", self.base / "agy"))
        root_args = []
        for name, path in self.roots:
            root_args += ["--root", "%s=%s" % (name, path)]
        completed = subprocess.run(
            ["sh", str(LAUNCHER), "-B",
             str(PLUGIN_ROOT / "bin" / "retro-eval-extract"),
             "--work-dir", str(self.work), *root_args],
            capture_output=True, text=True, timeout=120, env=self.env)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.out = self.base / "labels"

    def labels(self, *args):
        return labels(self.env, *args)

    def sample(self, *extra):
        source_args = []
        for name, path in self.roots:
            source_args += ["--source-root", "%s=%s" % (name, path)]
        return self.labels("sample", "--traces", self.work / "traces.jsonl",
                           "--id-salt", self.work / "id-salt.bin", *source_args,
                           "--output", self.out / "heldout.csv",
                           "--manifest", self.out / "heldout-manifest.json",
                           "--per-source", 4, *extra)

    def test_sample_from_extracted_traces_feeds_the_whole_chain(self):
        completed = self.sample()
        self.assertEqual(0, completed.returncode, completed.stderr)
        manifest = json.loads((self.out / "heldout-manifest.json").read_text(
            encoding="utf-8"))
        self.assertEqual({"antigravity": 2, "claude": 4, "codex": 2},
                         manifest["source_counts"])
        with (self.out / "heldout.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(8, len(rows))
        self.assertTrue(all(row["user_turn"] for row in rows))
        self.assertNotIn("environment_context", "".join(r["user_turn"] for r in rows))
        corrections = [row for row in rows if row["user_turn"].startswith("No,")]
        self.assertTrue(corrections)
        self.assertIn("<long-token>", corrections[0]["user_turn"])
        self.assertEqual(str(len(LONG_ANSWER)), corrections[0]["context_chars"])
        traces = (self.work / "traces.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("inspect", traces)

        completed = self.labels(
            "predict-annotations", "--source", self.out / "heldout.csv",
            "--manifest", self.out / "heldout-manifest.json",
            "--predictions", self.out / "rule-test.jsonl",
            "--prediction-manifest", self.out / "rule-test-manifest.json",
            "--created-commit", FULL_COMMIT)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(8, json.loads(completed.stdout)["predicted"])
        predicted = {json.loads(line)["case_id"]: json.loads(line)["label"]
                     for line in (self.out / "rule-test.jsonl").read_text(
                         encoding="utf-8").splitlines()}
        for row in rows:
            row["human_label"] = predicted[row["case_id"]]
        with (self.out / "heldout.csv").open("w", encoding="utf-8",
                                             newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        completed = self.labels(
            "import-annotations", "--source", self.out / "heldout.csv",
            "--manifest", self.out / "heldout-manifest.json",
            "--labels", self.out / "human-test.jsonl")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(8, json.loads(completed.stdout)["imported"])
        completed = self.labels(
            "compare", "--labels", self.out / "human-test.jsonl",
            "--sample-manifest", self.out / "heldout-manifest.json",
            "--predictions", "rule=%s" % (self.out / "rule-test.jsonl"),
            "--prediction-manifest", "rule=%s" % (self.out / "rule-test-manifest.json"),
            "--output", self.out / "comparison.json", "--split", "test")
        self.assertEqual(0, completed.returncode, completed.stderr)
        comparison = json.loads((self.out / "comparison.json").read_text(
            encoding="utf-8"))
        self.assertEqual(1.0, comparison["predictors"]["rule"]["test"]["agreement"])

    def test_sample_is_deterministic_for_one_extraction(self):
        self.assertEqual(0, self.sample().returncode)
        first = (self.out / "heldout.csv").read_bytes()
        (self.out / "heldout.csv").unlink()
        self.assertEqual(0, self.sample().returncode)
        self.assertEqual(first, (self.out / "heldout.csv").read_bytes())

    def test_sample_refuses_inputs_that_yield_no_cases(self):
        for extract in (self.work / "traces.jsonl", self.work / "extraction.json"):
            with self.subTest(extract=extract.name):
                completed = self.labels("sample", "--extract", extract,
                                        "--output", self.out / "empty.csv",
                                        "--manifest", self.out / "empty-manifest.json")
                self.assertEqual(2, completed.returncode, completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)
                self.assertFalse((self.out / "empty.csv").exists())
        wrong_salt = self.base / "wrong-salt.bin"
        wrong_salt.write_bytes(b"x" * 32)
        completed = self.labels(
            "sample", "--traces", self.work / "traces.jsonl", "--id-salt", wrong_salt,
            "--source-root", "claude=%s" % (self.base / "claude"),
            "--output", self.out / "empty.csv",
            "--manifest", self.out / "empty-manifest.json")
        self.assertEqual(2, completed.returncode, completed.stderr)
        self.assertFalse((self.out / "empty.csv").exists())

    def test_trace_sampling_requires_roots_and_salt(self):
        completed = self.labels("sample", "--traces", self.work / "traces.jsonl",
                                "--output", self.out / "x.csv",
                                "--manifest", self.out / "x-manifest.json")
        self.assertEqual(2, completed.returncode, completed.stderr)


if __name__ == "__main__":
    unittest.main()
