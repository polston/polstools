import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "p"
SKILL_ROOT = PLUGIN_ROOT / "skills" / "adequacy-review"
CONTRACT_PATH = SKILL_ROOT / "contract-v1.json"
HELPER_PATH = SKILL_ROOT / "scripts" / "adequacy_review.py"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "adequacy-review-v1.json"
VALIDATOR_PATH = PLUGIN_ROOT / "bin" / "p_validate.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("adequacy_review", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_validator():
    spec = importlib.util.spec_from_file_location("p_validate", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AdequacyReviewContractTests(unittest.TestCase):
    def test_contract_preserves_versioned_review_semantics(self):
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(1, contract["contract_version"])
        self.assertEqual(4, contract["defaults"]["reviewers"])
        self.assertEqual(2, contract["distillation"]["agreement_threshold"])
        self.assertEqual(5, contract["distillation"]["stable_cap"])
        self.assertEqual(
            ["critical", "important", "suggestion"],
            contract["distillation"]["severity_order"],
        )
        self.assertEqual(
            ["verdict", "findings"],
            contract["reviewer_schema"]["required"],
        )
        finding = contract["reviewer_schema"]["properties"]["findings"]["items"]
        self.assertEqual(["issue", "severity"], finding["required"])
        policy = json.dumps(contract, sort_keys=True)
        for phrase in (
            "BLINDED",
            "GROUND",
            "TRACE",
            "sandbox",
            "unchecked",
            "contested",
        ):
            self.assertIn(phrase.lower(), policy.lower())

    def test_skill_routes_to_exactly_one_native_adapter(self):
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/claude-code.md", skill)
        self.assertIn("references/codex.md", skill)
        self.assertIn("Read exactly one adapter", skill)
        self.assertIn("contract-v1.json", skill)
        self.assertNotIn("workflows/adequacy-review.js", skill)

    def test_native_adapters_contain_transport_not_review_policy(self):
        adapters = {
            "claude-code": SKILL_ROOT / "references" / "claude-code.md",
            "codex": SKILL_ROOT / "references" / "codex.md",
        }
        forbidden = ("agreement_threshold", "stable_cap", "GROUND", "TRACE")
        for harness, path in adapters.items():
            with self.subTest(harness=harness):
                text = path.read_text(encoding="utf-8")
                self.assertIn("scripts/adequacy_review.py", text)
                self.assertIn("contract-v1.json", text)
                self.assertIn("distiller-packet", text)
                self.assertTrue(all(token not in text for token in forbidden))
        self.assertIn("Agent", adapters["claude-code"].read_text(encoding="utf-8"))
        self.assertIn("spawn_agent", adapters["codex"].read_text(encoding="utf-8"))

    def test_legacy_claude_only_workflow_is_removed(self):
        self.assertFalse((PLUGIN_ROOT / "workflows" / "adequacy-review.js").exists())


class AdequacyReviewParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_adapters_receive_equivalent_canonical_packets(self):
        invocation = self.fixture["invocation"]
        claude = self.helper.build_packet("claude-code", **invocation)
        codex = self.helper.build_packet("codex", **invocation)
        self.assertNotEqual(claude["transport"], codex["transport"])
        self.assertEqual(claude["canonical"], codex["canonical"])

    def test_packet_embeds_schema_for_schema_less_native_transports(self):
        packet = self.helper.build_packet("codex", **self.fixture["invocation"])
        request = packet["canonical"]["review_requests"][0]
        self.assertIn("RESPONSE SCHEMA", request["prompt"])
        self.assertIn(json.dumps(request["schema"], sort_keys=True), request["prompt"])
        self.assertIn("unchecked", request["prompt"])
        self.assertNotIn("model", packet["transport"])
        self.assertNotIn("parallel", packet["transport"])

    def test_distiller_packet_embeds_schema_and_reviews(self):
        request = self.helper.build_distiller_request(self.fixture["reviews"])
        self.assertIn("DISTILLER RESPONSE SCHEMA", request["prompt"])
        self.assertIn(json.dumps(request["schema"], sort_keys=True), request["prompt"])
        self.assertIn("Cache entries survive invalidation", request["prompt"])

    def test_fake_reviewers_produce_equivalent_stable_and_contested_results(self):
        results = []
        for harness in ("claude-code", "codex"):
            results.append(
                self.helper.run_fixture(
                    harness,
                    self.fixture["invocation"],
                    self.fixture["reviews"],
                    self.fixture["clusters"],
                    self.fixture["unchecked"],
                )
            )
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.fixture["expected"], results[0])

    def test_semantic_clusters_preserve_paraphrases_multibyte_and_unchecked(self):
        reviews = [
            {
                "verdict": "has-issues",
                "unchecked": ["Runtime integration was not executed."],
                "findings": [
                    {
                        "issue": "Cache entries survive invalidation",
                        "severity": "important",
                        "agreement_key": "stale-cache",
                    },
                    {"issue": "缓存失效", "severity": "suggestion"},
                ],
            },
            {
                "verdict": "has-issues",
                "unchecked": [],
                "findings": [
                    {
                        "issue": "Invalidation leaves stale cache entries",
                        "severity": "important",
                        "agreement_key": "cache-invalidation-stale",
                    }
                ],
            },
        ]
        clusters = {
            "clusters": [
                {
                    "finding_refs": [
                        {"review": 1, "finding": 1},
                        {"review": 2, "finding": 1},
                    ]
                },
                {"finding_refs": [{"review": 1, "finding": 2}]},
            ]
        }
        result = self.helper.distill_reviews(reviews, clusters)
        self.assertEqual("2/2", result["stable"][0]["agreement"])
        self.assertEqual("缓存失效", result["contested"][0]["issue"])
        self.assertEqual(["Runtime integration was not executed."], result["unchecked"])


class AdequacyReviewInstalledCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_validator()

    def test_helper_self_check_runs_from_an_installed_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "p"
            shutil.copytree(PLUGIN_ROOT, plugin)
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(
                        plugin
                        / "skills"
                        / "adequacy-review"
                        / "scripts"
                        / "adequacy_review.py"
                    ),
                    "self-check",
                ],
                text=True,
                encoding="utf-8",
                capture_output=True,
            )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('"contract_version": 1', result.stdout)
        self.assertIn('"claude-code"', result.stdout)
        self.assertIn('"codex"', result.stdout)

    def test_validator_flags_adapter_contract_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "p"
            shutil.copytree(PLUGIN_ROOT, plugin)
            adapter = (
                plugin
                / "skills"
                / "adequacy-review"
                / "references"
                / "codex.md"
            )
            adapter.write_text(
                adapter.read_text(encoding="utf-8").replace("contract-v1.json", "contract.json"),
                encoding="utf-8",
            )
            errors = self.validator.validate_package(plugin)
        self.assertIn("Codex adequacy-review adapter does not load contract-v1.json", errors)

    def test_validator_flags_changed_contract_invariant(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "p"
            shutil.copytree(PLUGIN_ROOT, plugin)
            contract_path = plugin / "skills" / "adequacy-review" / "contract-v1.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["distillation"]["agreement_threshold"] = 3
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            errors = self.validator.validate_package(plugin)
        self.assertIn("adequacy-review contract self-check failed", errors)

    def test_validator_flags_empty_reviewer_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "p"
            shutil.copytree(PLUGIN_ROOT, plugin)
            contract_path = plugin / "skills" / "adequacy-review" / "contract-v1.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["reviewer_schema"] = {}
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            errors = self.validator.validate_package(plugin)
        self.assertIn("adequacy-review contract self-check failed", errors)


if __name__ == "__main__":
    unittest.main()
