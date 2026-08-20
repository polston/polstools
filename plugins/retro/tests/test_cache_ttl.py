"""Tests for cache_ttl. Stdlib unittest; no third-party runner."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plugins" / "retro" / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures  # noqa: E402

T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestFixtures(unittest.TestCase):
    def test_build_corpus_writes_main_and_subagent_transcripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.build_corpus(root, [
                {"project": "proj-a", "session": "s1", "rows": [
                    fixtures.usage_row("r1", "claude-opus-5", 100, 10, 0, 5, T0),
                ]},
                {"project": "proj-a", "session": "s1", "subagent": True, "rows": [
                    fixtures.usage_row("r2", "claude-sonnet-5", 200, 0, 20, 5, T0),
                ]},
            ])
            main = root / "proj-a" / "s1.jsonl"
            sub = root / "proj-a" / "s1" / "subagents" / "agent-0.jsonl"
            self.assertTrue(main.is_file())
            self.assertTrue(sub.is_file())
            rec = json.loads(main.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(rec["requestId"], "r1")
            self.assertEqual(rec["message"]["usage"]["cache_read_input_tokens"], 100)


if __name__ == "__main__":
    unittest.main()
