import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.schema import SCHEMA_VERSION, SpanKind, TraceRecord  # noqa: E402
from retro_eval.semconv import SemanticConventionProfile  # noqa: E402


class SemanticConventionTests(unittest.TestCase):
    def record(self):
        return TraceRecord(
            schema_version=SCHEMA_VERSION, trace_id="a", span_id="b",
            parent_span_id=None, source="fixture", adapter_version=1,
            source_version="1", span_kind=SpanKind.LLM, started_at=None,
            sequence=0, input_tokens=12, output_tokens=3,
        )

    def test_default_profile_maps_openinference_and_otel_attributes(self):
        profile = SemanticConventionProfile.load(
            PLUGIN_ROOT / "profiles" / "semconv.json")
        attributes = profile.attributes(self.record())
        self.assertEqual("LLM", attributes["openinference.span.kind"])
        self.assertEqual("chat", attributes["gen_ai.operation.name"])
        self.assertEqual(12, attributes["gen_ai.usage.input_tokens"])
        self.assertEqual(3, attributes["gen_ai.usage.output_tokens"])

    def test_mapping_is_data_and_unknown_extension_can_be_added(self):
        profile = {
            "schema_version": 1,
            "profile_id": "fixture",
            "field_mappings": {"source": "vendor.source"},
            "value_mappings": {},
            "kind_attributes": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "semconv.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            attributes = SemanticConventionProfile.load(path).attributes(self.record())
        self.assertEqual({"vendor.source": "fixture"}, attributes)


if __name__ == "__main__":
    unittest.main()
