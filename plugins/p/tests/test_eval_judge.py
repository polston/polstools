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

    def test_all_non_synthetic_classes_require_external_authority(self):
        for classification in ('private_redacted', 'private_metadata', 'private_raw',
                               'unknown', '', None, 'public', [], {}):
            with self.subTest(classification=classification):
                provider = ExternalProvider()
                judge = ModelJudge(rubric_id='example', rubric_version=1,
                    labels=('correction',), prompt_template='{evidence}',
                    provider=provider, model_config={})
                with self.assertRaises(PermissionError):
                    judge.evaluate(JudgeCase('case', 'ref', 'Authored example.', classification))
                self.assertEqual(provider.calls, [])

    def test_public_synthetic_and_explicit_external_authority_are_supported(self):
        for classification, authority in [('public_synthetic', False), ('private_redacted', True)]:
            with self.subTest(classification=classification):
                provider = ExternalProvider()
                judge = ModelJudge(rubric_id='example', rubric_version=1,
                    labels=('correction',), prompt_template='{evidence}',
                    provider=provider, model_config={}, allow_private_external=authority)
                judge.evaluate(JudgeCase('case', 'ref', 'Authored example.', classification))
                self.assertEqual(len(provider.calls), 1)

    def test_truthy_flags_do_not_establish_external_authority_or_locality(self):
        for flag in ('false', 'true', 0, 1, None, [], {}):
            with self.subTest(authority=flag):
                provider = ExternalProvider()
                with self.assertRaises(ValueError):
                    ModelJudge(rubric_id='example', rubric_version=1,
                        labels=('correction',), prompt_template='{evidence}',
                        provider=provider, model_config={}, allow_private_external=flag)
                self.assertEqual(provider.calls, [])
        for flag in ('false', 'true', 1, None):
            with self.subTest(local=flag):
                provider = ExternalProvider()
                setattr(provider, "local", flag)
                judge = ModelJudge(rubric_id='example', rubric_version=1,
                    labels=('correction',), prompt_template='{evidence}',
                    provider=provider, model_config={})
                with self.assertRaises(PermissionError):
                    judge.evaluate(JudgeCase('case', 'ref', 'Authored example.', 'private'))
                self.assertEqual(provider.calls, [])

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
