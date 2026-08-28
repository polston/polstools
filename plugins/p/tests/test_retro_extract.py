"""Root resolution, structural harness detection, and the extract walk."""

import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from fixtures import build_corpus, claude_assistant, claude_user

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def load_retro():
    spec = importlib.util.spec_from_file_location(
        "retro_extract_under_test", PLUGIN_ROOT / "bin" / "retro.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def minimal_rollout_lines():
    t = "2026-08-01T12:00:00.000Z"
    return [
        {"type": "session_meta", "timestamp": t, "payload": {
            "session_id": "sess-cx-1", "cwd": "/tmp/w", "cli_version": "1.0",
            "thread_source": "user", "git": {"branch": "main"}}},
        {"type": "response_item", "timestamp": t, "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "hello there"}]}},
        {"type": "response_item", "timestamp": t, "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "hi"}]}},
    ]


def write_rollout(root, rel, lines=None):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in (lines or minimal_rollout_lines()):
            fh.write(json.dumps(rec) + "\n")
    return path


class RootResolution(unittest.TestCase):
    def test_env_overrides_resolve_at_call_time(self):
        retro = load_retro()
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/cxhome",
                                          "CLAUDE_CONFIG_DIR": "/tmp/cchome"}):
            self.assertEqual(Path("/tmp/cxhome") / "sessions",
                             retro.codex_sessions_dir())
            self.assertEqual(Path("/tmp/cchome") / "projects",
                             retro.claude_projects_dir())
        roots = retro.transcript_roots()
        self.assertEqual(("claude", "codex"), tuple(h for h, _ in roots))


class HarnessDetection(unittest.TestCase):
    def setUp(self):
        self.retro = load_retro()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_rollout_detected_by_first_record(self):
        path = write_rollout(self.root, "2026/08/01/rollout-x.jsonl")
        self.assertTrue(self.retro.is_rollout(path))

    def test_claude_transcript_is_not_rollout(self):
        t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
        build_corpus(self.root, [{"project": "p", "session": "s", "rows": [
            claude_user("hi", t0), claude_assistant("yo", t0)]}])
        self.assertFalse(self.retro.is_rollout(self.root / "p" / "s.jsonl"))

    def test_junk_file_is_neither(self):
        path = self.root / "junk.jsonl"
        path.write_text('{"type": "something-else"}\n', encoding="utf-8")
        self.assertFalse(self.retro.is_rollout(path))
        outcome, row = self.retro.measure_outcome(path, "claude", self.root)
        self.assertEqual(self.retro.NOT_TRANSCRIPT, outcome)

    def test_rollout_without_session_meta_is_still_a_rollout(self):
        recs = minimal_rollout_lines()[1:]   # drop the session_meta
        path = write_rollout(self.root, "2026/08/01/no-meta.jsonl", recs)
        self.assertTrue(self.retro.is_rollout(path))

    def test_mixed_file_is_a_rollout(self):
        # Any rollout-set type inside the first 20 records decides; a
        # Claude-set record before it does not veto (spec D1.4).
        recs = [{"type": "user", "message": {"content": []}},
                {"type": "turn_context", "payload": {}}]
        path = write_rollout(self.root, "2026/08/01/mixed.jsonl", recs)
        self.assertTrue(self.retro.is_rollout(path))


class ExtractWalk(unittest.TestCase):
    def run_extract(self, claude_home, codex_home, work):
        env = {"CLAUDE_CONFIG_DIR": str(claude_home),
               "CODEX_HOME": str(codex_home), "RETRO_HOME": str(work)}
        with mock.patch.dict(os.environ, env):
            retro = load_retro()   # WORK_DIR reads RETRO_HOME at import
            args = mock.Mock(rebuild=False)
            try:
                code = retro.cmd_extract(args)
            except SystemExit as stop:
                return retro, stop.code
            return retro, code

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.claude_home = base / "cc"
        self.codex_home = base / "cx"
        self.work = base / "work"

    def test_both_roots_missing_exits_2(self):
        retro, code = self.run_extract(self.claude_home, self.codex_home,
                                       self.work)
        self.assertEqual(retro.EXIT_CANNOT_RUN, code)

    # Passes once measure_codex lands (Task 4 removes this decorator);
    # until then the stub measures no rollout and the ledger stays empty.
    @unittest.expectedFailure
    def test_single_root_is_enough(self):
        write_rollout(self.codex_home / "sessions", "2026/08/01/r.jsonl")
        retro, code = self.run_extract(self.claude_home, self.codex_home,
                                       self.work)
        self.assertIn(code, (retro.EXIT_CLEAN, retro.EXIT_FLAGGED))
        ledger = (self.work / "metrics.jsonl").read_text(encoding="utf-8")
        self.assertEqual(1, len(ledger.strip().splitlines()))


if __name__ == "__main__":
    unittest.main()
