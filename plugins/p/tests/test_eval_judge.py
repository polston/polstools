import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.judge import JudgeCase, ModelJudge  # noqa: E402


class FakeProvider:
    provider_id = "fake-local"
    local = True

    def __init__(self):
        self.calls = []

    def judge(self, request):
        self.calls.append(request)
        return {"label": "correction", "reason": "explicit replacement"}


class ExternalProvider(FakeProvider):
    provider_id = "fake-external"
    local = False


class ModelJudgeTests(unittest.TestCase):
    def test_local_provider_is_versioned_audited_and_seeded(self):
        provider = FakeProvider()
        judge = ModelJudge(
            rubric_id="turn_intent", rubric_version=1,
            labels=("correction", "question", "ambiguous"),
            prompt_template="Classify under rubric {rubric_id}: {evidence}",
            provider=provider, model_config={"temperature": 0, "seed": 7},
        )
        execution = judge.evaluate(JudgeCase(
            case_id="case-a", evidence_ref="e-a", evidence="use the prior value",
            data_classification="private",
        ))
        self.assertEqual("correction", execution.label)
        self.assertEqual("fake-local", execution.provider_id)
        self.assertTrue(execution.prompt_hash)
        self.assertNotIn("use the prior value", execution.to_dict().values())
        self.assertEqual(7, provider.calls[0]["model_config"]["seed"])

    def test_private_evidence_cannot_reach_external_provider_without_authority(self):
        provider = ExternalProvider()
        judge = ModelJudge(
            rubric_id="turn_intent", rubric_version=1,
            labels=("correction", "ambiguous"), prompt_template="{evidence}",
            provider=provider, model_config={}, allow_private_external=False,
        )
        with self.assertRaises(PermissionError):
            judge.evaluate(JudgeCase("case-a", "e-a", "private text", "private"))
        self.assertEqual([], provider.calls)

    def test_unknown_label_becomes_abstention(self):
        class InvalidProvider(FakeProvider):
            def judge(self, request):
                return {"label": "invented", "reason": ""}

        execution = ModelJudge(
            rubric_id="turn_intent", rubric_version=1,
            labels=("correction", "ambiguous"), prompt_template="{evidence}",
            provider=InvalidProvider(), model_config={},
        ).evaluate(JudgeCase("case-a", "e-a", "text", "private"))
        self.assertTrue(execution.abstained)
        self.assertEqual("invalid_provider_label", execution.reason)

    def test_rubric_without_ambiguous_label_uses_configured_abstention_label(self):
        class InvalidProvider(FakeProvider):
            def judge(self, request):
                return {"label": "invented", "reason": ""}

        execution = ModelJudge(
            rubric_id="turn_friction_legacy", rubric_version=1,
            labels=("interrupt", "question", "approval", "correction", "none"),
            abstain_label="abstain", prompt_template="{evidence}",
            provider=InvalidProvider(), model_config={},
        ).evaluate(JudgeCase("case-a", "e-a", "text", "private"))
        self.assertTrue(execution.abstained)
        self.assertEqual("abstain", execution.label)

    def test_rubric_native_ambiguous_label_is_recorded_as_abstention(self):
        class AmbiguousProvider(FakeProvider):
            def judge(self, request):
                return {"label": "ambiguous", "reason": "insufficient context"}

        execution = ModelJudge(
            rubric_id="turn_intent", rubric_version=1,
            labels=("correction", "ambiguous"), prompt_template="{evidence}",
            provider=AmbiguousProvider(), model_config={},
        ).evaluate(JudgeCase("case-a", "e-a", "text", "private"))
        self.assertTrue(execution.abstained)
        self.assertEqual("insufficient context", execution.reason)


if __name__ == "__main__":
    unittest.main()
