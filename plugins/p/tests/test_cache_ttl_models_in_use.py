"""Model ids seen in current transcripts are priced, so their requests reach
the cost model instead of the unpriced bucket."""

import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures  # noqa: E402
import cache_ttl  # noqa: E402

T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
MODELS_IN_USE = ("claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5",
                 "claude-opus-4-6")


class ModelsInUse(unittest.TestCase):
    def test_each_model_in_use_gets_a_priced_verdict(self):
        for model in MODELS_IN_USE:
            with self.subTest(model=model), tempfile.TemporaryDirectory() as tmp:
                rows = [fixtures.usage_row("r%d" % i, model, 1000, 50, 0, 1,
                                           T0 + timedelta(seconds=600 * i))
                        for i in range(3)]
                fixtures.build_corpus(Path(tmp), [
                    {"project": "p", "session": "s", "rows": rows}])
                stream = io.StringIO()
                cache_ttl.report(Path(tmp), None, None, True, stream)
                body = json.loads(stream.getvalue())
                self.assertEqual({}, body["unpriced_requests"])
                self.assertIn(body["verdict"], ("keep", "switch"))


if __name__ == "__main__":
    unittest.main()
