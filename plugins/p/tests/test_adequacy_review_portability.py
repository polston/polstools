import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "p"
SKILL_ROOT = PLUGIN_ROOT / "skills" / "adequacy-review"
CONTRACT_PATH = SKILL_ROOT / "contract-v1.json"
HELPER_PATH = SKILL_ROOT / "scripts" / "adequacy_review.py"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "adequacy-review-v1.json"
VALIDATOR_PATH = PLUGIN_ROOT / "bin" / "p_validate.py"
SEALED_SPEC_PATH = (
    REPO_ROOT / "docs" / "plans" / "2026-08-25-portable-adequacy-review-spec.md"
)


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
            {"target", "spec", "repo", "exclusions", "reviewers"},
            set(contract["inputs"]),
        )
        self.assertTrue(
            all(
                isinstance(description, str) and description.strip()
                for description in contract["inputs"].values()
            )
        )
        self.assertEqual(
            ["request_id", "verdict", "findings", "unchecked"],
            contract["reviewer_schema"]["required"],
        )
        self.assertEqual(
            ["clusters", "reviews_sha256"],
            contract["distiller_schema"]["required"],
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

    def test_skill_requires_author_context_exclusions_from_target_and_grounding(self):
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("target itself", skill)
        self.assertIn("grounding", skill)
        for adapter in ("claude-code", "codex"):
            text = (SKILL_ROOT / "references" / (adapter + ".md")).read_text(
                encoding="utf-8"
            )
            self.assertIn("--exclude", text)
            self.assertIn("--packet-file", text)
            self.assertIn("canonical request order", text)

    def test_render_policy_is_not_duplicated_in_helper(self):
        helper = HELPER_PATH.read_text(encoding="utf-8")
        for policy_literal in (
            '"## "',
            '"None."',
            '"None disclosed."',
            '("critical", "important")',
            '"TARGET: "',
            '"Two non-negotiable steps:"',
            '"REVIEWS:"',
            '"RESPONSE SCHEMA (return JSON only):"',
            '"DISTILLER RESPONSE SCHEMA (return JSON only):"',
        ):
            self.assertNotIn(policy_literal, helper)

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

    def test_sealed_spec_contains_requirements_without_review_history(self):
        spec = SEALED_SPEC_PATH.read_text(encoding="utf-8")
        self.assertIn("## Requirements", spec)
        self.assertIn("## Acceptance evidence", spec)
        self.assertIn("## Protected scope", spec)
        for leaked_history in ("Progress log", "Codex review", "review-fix", "commit `"):
            self.assertNotIn(leaked_history, spec)


class AdequacyReviewParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def _bound_reviews(self, reviews, packet):
        bound = copy.deepcopy(reviews)
        requests = packet["canonical"]["review_requests"]
        for review, request in zip(bound, requests):
            review["request_id"] = request["request_id"]
        return bound

    @staticmethod
    def _bound_clusters(clusters, distiller_request):
        bound = copy.deepcopy(clusters)
        bound["reviews_sha256"] = distiller_request["reviews_sha256"]
        return bound

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
        self.assertIn("EXCLUDED AUTHOR CONTEXT", request["prompt"])
        self.assertIn("docs/plans/progress.md", request["prompt"])
        request_ids = [
            item["request_id"] for item in packet["canonical"]["review_requests"]
        ]
        self.assertEqual(4, len(set(request_ids)))
        self.assertEqual(
            request_ids[0], request["schema"]["properties"]["request_id"]["const"]
        )
        self.assertIn(request_ids[0], request["prompt"])
        self.assertEqual(
            ["docs/plans/progress.md"],
            packet["canonical"]["input"]["exclusions"],
        )
        self.assertNotIn("model", packet["transport"])
        self.assertNotIn("parallel", packet["transport"])

    def test_no_spec_prompt_does_not_require_an_unreportable_inference(self):
        packet = self.helper.build_packet(
            "codex", "example.py", repo=".", reviewers=1
        )
        prompt = packet["canonical"]["review_requests"][0]["prompt"]
        self.assertNotIn("state what you inferred", prompt)

    def test_distiller_packet_embeds_schema_and_reviews(self):
        packet = self.helper.build_packet(
            "codex", **self.fixture["invocation"]
        )
        reviews = self._bound_reviews(self.fixture["reviews"], packet)
        request = self.helper.build_distiller_request(
            reviews, packet
        )
        self.assertIn("DISTILLER RESPONSE SCHEMA", request["prompt"])
        self.assertIn(json.dumps(request["schema"], sort_keys=True), request["prompt"])
        self.assertIn("Cache entries survive invalidation", request["prompt"])
        self.assertEqual(
            request["reviews_sha256"],
            request["schema"]["properties"]["reviews_sha256"]["const"],
        )

    def test_review_without_unchecked_disclosure_is_rejected(self):
        review = {"verdict": "good", "findings": []}
        packet = self.helper.build_packet("codex", "example.py", reviewers=1)
        review["request_id"] = packet["canonical"]["review_requests"][0][
            "request_id"
        ]
        with self.assertRaisesRegex(self.helper.ContractError, "unchecked"):
            self.helper.build_distiller_request([review], packet)

    def test_reviewer_results_reject_excess_fields_and_blank_disclosures(self):
        packet = self.helper.build_packet("codex", "example.py", reviewers=1)
        base = self._bound_reviews(
            [{"verdict": "good", "findings": [], "unchecked": []}], packet
        )[0]
        mutations = {
            "review": lambda value: value.update(extra=True),
            "finding": lambda value: value.update(
                verdict="has-issues",
                findings=[{"issue": "Issue", "severity": "important", "extra": True}],
            ),
            "blank unchecked": lambda value: value.update(unchecked=[""]),
            "non-string severity": lambda value: value.update(
                verdict="has-issues",
                findings=[{"issue": "Issue", "severity": []}],
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                review = copy.deepcopy(base)
                mutate(review)
                with self.assertRaises(self.helper.ContractError):
                    self.helper.build_distiller_request([review], packet)

    def test_distiller_results_reject_excess_fields(self):
        packet = self.helper.build_packet("codex", "example.py", reviewers=1)
        reviews = self._bound_reviews(
            [
                {
                    "verdict": "has-issues",
                    "findings": [{"issue": "Issue", "severity": "important"}],
                    "unchecked": [],
                }
            ],
            packet,
        )
        request = self.helper.build_distiller_request(reviews, packet)
        base = self._bound_clusters(
            {"clusters": [{"finding_refs": [{"review": 1, "finding": 1}]}]},
            request,
        )
        mutations = {
            "result": lambda value: value.update(extra=True),
            "cluster": lambda value: value["clusters"][0].update(extra=True),
            "reference": lambda value: value["clusters"][0]["finding_refs"][0].update(
                extra=True
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                clusters = copy.deepcopy(base)
                mutate(clusters)
                with self.assertRaises(self.helper.ContractError):
                    self.helper.distill_reviews(reviews, clusters, packet=packet)

    def test_supported_schema_constraints_are_enforced_at_runtime(self):
        contract = self.helper.load_contract()
        contract = copy.deepcopy(contract)
        contract["reviewer_schema"]["properties"]["findings"]["items"][
            "properties"
        ]["issue"]["enum"] = ["Allowed"]
        with mock.patch.object(self.helper, "load_contract", return_value=contract):
            packet = self.helper.build_packet("codex", "example.py", reviewers=1)
            review = self._bound_reviews(
                [
                    {
                        "verdict": "has-issues",
                        "findings": [
                            {"issue": "Not allowed", "severity": "important"}
                        ],
                        "unchecked": [],
                    }
                ],
                packet,
            )
            with self.assertRaises(self.helper.ContractError):
                self.helper.build_distiller_request(review, packet)

    def test_contract_verdicts_and_step_labels_drive_runtime_behavior(self):
        contract = copy.deepcopy(self.helper.load_contract())
        contract["reviewer_schema"]["properties"]["verdict"]["enum"] = [
            "accepted",
            "rejected",
        ]
        contract["review"]["required_steps"][0]["label"] = "SEARCH"
        with mock.patch.object(self.helper, "load_contract", return_value=contract):
            packet = self.helper.build_packet("codex", "example.py", reviewers=1)
            self.assertIn("1. SEARCH -", packet["canonical"]["review_requests"][0]["prompt"])
            review = self._bound_reviews(
                [{"verdict": "accepted", "findings": [], "unchecked": []}], packet
            )
            request = self.helper.build_distiller_request(review, packet)
        self.assertEqual(64, len(request["reviews_sha256"]))

    def test_contract_ranking_policy_drives_result_order(self):
        contract = copy.deepcopy(self.helper.load_contract())
        contract["distillation"]["ranking_tiebreakers"] = [
            "agreement_desc",
            "severity",
            "first_seen",
        ]
        with mock.patch.object(self.helper, "load_contract", return_value=contract):
            packet = self.helper.build_packet("codex", "example.py", reviewers=3)
            reviews = self._bound_reviews(
                [
                    {
                        "verdict": "has-issues",
                        "findings": [
                            {"issue": "Critical pair", "severity": "critical"},
                            {"issue": "Suggestion trio", "severity": "suggestion"},
                        ],
                        "unchecked": [],
                    },
                    {
                        "verdict": "has-issues",
                        "findings": [
                            {"issue": "Critical pair", "severity": "critical"},
                            {"issue": "Suggestion trio", "severity": "suggestion"},
                        ],
                        "unchecked": [],
                    },
                    {
                        "verdict": "has-issues",
                        "findings": [
                            {"issue": "Suggestion trio", "severity": "suggestion"}
                        ],
                        "unchecked": [],
                    },
                ],
                packet,
            )
            request = self.helper.build_distiller_request(reviews, packet)
            clusters = self._bound_clusters(
                {
                    "clusters": [
                        {
                            "finding_refs": [
                                {"review": 1, "finding": 1},
                                {"review": 2, "finding": 1},
                            ]
                        },
                        {
                            "finding_refs": [
                                {"review": 1, "finding": 2},
                                {"review": 2, "finding": 2},
                                {"review": 3, "finding": 1},
                            ]
                        },
                    ]
                },
                request,
            )
            result = self.helper.distill_reviews(reviews, clusters, packet=packet)
        self.assertEqual("Suggestion trio", result["stable"][0]["issue"])

    def test_distillation_requires_the_packet_reviewer_count(self):
        packet = self.helper.build_packet(
            "codex", **self.fixture["invocation"]
        )
        reviews = self._bound_reviews(self.fixture["reviews"], packet)
        with self.assertRaisesRegex(self.helper.ContractError, "expected 4 reviewer"):
            self.helper.build_distiller_request(reviews[:3], packet)
        with self.assertRaisesRegex(self.helper.ContractError, "expected 4 reviewer"):
            self.helper.distill_reviews(
                reviews[:3],
                {"clusters": []},
                packet=packet,
            )

    def test_distillation_rejects_a_packet_from_another_contract(self):
        packet = self.helper.build_packet(
            "codex", **self.fixture["invocation"]
        )
        packet = copy.deepcopy(packet)
        packet["canonical"]["contract_sha256"] = "0" * 64
        with self.assertRaisesRegex(self.helper.ContractError, "packet contract"):
            self.helper.build_distiller_request(self.fixture["reviews"], packet)

    def test_packet_prompt_and_stale_review_results_are_rejected(self):
        packet_a = self.helper.build_packet("codex", "a.py", reviewers=1)
        packet_b = self.helper.build_packet("codex", "b.py", reviewers=1)
        reviews_a = self._bound_reviews(
            [{"verdict": "good", "findings": [], "unchecked": []}], packet_a
        )
        with self.assertRaisesRegex(self.helper.ContractError, "request identity"):
            self.helper.build_distiller_request(reviews_a, packet_b)
        changed = copy.deepcopy(packet_a)
        changed["canonical"]["review_requests"][0]["prompt"] = "altered"
        with self.assertRaisesRegex(self.helper.ContractError, "canonical"):
            self.helper.build_distiller_request(reviews_a, changed)

    def test_stale_clusters_are_rejected_for_changed_reviews(self):
        packet = self.helper.build_packet("codex", "example.py", reviewers=1)
        reviews = self._bound_reviews(
            [
                {
                    "verdict": "has-issues",
                    "findings": [{"issue": "First", "severity": "important"}],
                    "unchecked": [],
                }
            ],
            packet,
        )
        request = self.helper.build_distiller_request(reviews, packet)
        clusters = self._bound_clusters(
            {"clusters": [{"finding_refs": [{"review": 1, "finding": 1}]}]},
            request,
        )
        changed_reviews = copy.deepcopy(reviews)
        changed_reviews[0]["findings"][0]["issue"] = "Changed"
        with self.assertRaisesRegex(self.helper.ContractError, "reviews digest"):
            self.helper.distill_reviews(
                changed_reviews, clusters, packet=packet
            )

    def test_multiline_reviewer_text_is_rejected_before_markdown_rendering(self):
        packet = self.helper.build_packet("codex", "example.py", reviewers=1)
        base = {
            "verdict": "has-issues",
            "unchecked": [],
            "findings": [{"issue": "Issue", "severity": "important"}],
        }
        mutations = {
            "issue": lambda review: review["findings"][0].update(issue="x\n## injected"),
            "where": lambda review: review["findings"][0].update(where="x.py:1\n2"),
            "agreement key": lambda review: review["findings"][0].update(
                agreement_key="key\rhidden"
            ),
            "unchecked": lambda review: review.update(unchecked=["x\n- injected"]),
            "unicode separator": lambda review: review["findings"][0].update(
                issue="x\u2028## injected"
            ),
            "next line": lambda review: review["findings"][0].update(
                where="x.py:1\u0085hidden"
            ),
            "vertical tab": lambda review: review.update(unchecked=["x\vhidden"]),
            "form feed": lambda review: review["findings"][0].update(
                agreement_key="x\fhidden"
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                review = copy.deepcopy(base)
                mutate(review)
                review = self._bound_reviews([review], packet)[0]
                with self.assertRaisesRegex(self.helper.ContractError, "single line"):
                    self.helper.build_distiller_request([review], packet)

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
        packet = self.helper.build_packet("codex", "example.py", reviewers=2)
        reviews = self._bound_reviews(reviews, packet)
        request = self.helper.build_distiller_request(reviews, packet)
        clusters = self._bound_clusters(clusters, request)
        result = self.helper.distill_reviews(reviews, clusters, packet=packet)
        self.assertEqual("2/2", result["stable"][0]["agreement"])
        self.assertEqual("缓存失效", result["contested"][0]["issue"])
        self.assertEqual(["Runtime integration was not executed."], result["unchecked"])

    def test_stable_cap_is_disclosed_in_rendered_output(self):
        reviews = []
        for reviewer in range(2):
            reviews.append(
                {
                    "verdict": "has-issues",
                    "unchecked": [],
                    "findings": [
                        {
                            "issue": "Reviewer %d issue %d" % (reviewer + 1, finding + 1),
                            "severity": "important",
                        }
                        for finding in range(6)
                    ],
                }
            )
        clusters = {
            "clusters": [
                {
                    "finding_refs": [
                        {"review": 1, "finding": finding},
                        {"review": 2, "finding": finding},
                    ]
                }
                for finding in range(1, 7)
            ]
        }
        packet = self.helper.build_packet("codex", "example.py", reviewers=2)
        reviews = self._bound_reviews(reviews, packet)
        request = self.helper.build_distiller_request(reviews, packet)
        clusters = self._bound_clusters(clusters, request)
        result = self.helper.distill_reviews(reviews, clusters, packet=packet)
        self.assertEqual(1, result["stable_omitted"])
        self.assertIn(
            "Additional ensemble-stable findings omitted by cap: 1.",
            result["distilled"],
        )


class AdequacyReviewInstalledCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()
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
            contract["distillation"]["agreement_threshold"] = 0
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

    def test_contract_rejects_nested_schema_contradictions(self):
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        mutations = {
            "finding issue type": lambda value: value["reviewer_schema"]["properties"][
                "findings"
            ]["items"]["properties"]["issue"].update(type="integer"),
            "clusters type": lambda value: value["distiller_schema"]["properties"][
                "clusters"
            ].update(type="string"),
            "defaults container": lambda value: value.update(defaults=[]),
            "finding schema container": lambda value: value["reviewer_schema"][
                "properties"
            ]["findings"].update(items=[]),
            "review step container": lambda value: value["review"].update(
                required_steps=[7, 8]
            ),
            "unsupported findings constraint": lambda value: value[
                "reviewer_schema"
            ]["properties"]["findings"].update(maxItems=0),
            "unsupported reference constraint": lambda value: value[
                "distiller_schema"
            ]["properties"]["clusters"]["items"]["properties"][
                "finding_refs"
            ]["items"]["properties"]["review"].update(maximum=1),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                changed = json.loads(json.dumps(contract))
                mutate(changed)
                path = Path(tmp) / "contract.json"
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(self.helper.ContractError):
                    self.helper.load_contract(path)

    def test_contract_rejects_excess_v1_fields(self):
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        mutations = {
            "root": lambda value: value.update(extra=True),
            "defaults": lambda value: value["defaults"].update(extra=True),
            "review": lambda value: value["review"].update(extra=True),
            "distillation": lambda value: value["distillation"].update(extra=True),
            "reviewer property": lambda value: value["reviewer_schema"][
                "properties"
            ].update(extra={"type": "string"}),
            "distiller property": lambda value: value["distiller_schema"][
                "properties"
            ].update(extra={"type": "string"}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                changed = copy.deepcopy(contract)
                mutate(changed)
                path = Path(tmp) / "contract.json"
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(self.helper.ContractError):
                    self.helper.load_contract(path)

    def test_contract_rejects_prompt_template_drift(self):
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        mutations = {
            "missing spec placeholder": lambda value: value["review"].update(
                spec_present="SEALED SPEC"
            ),
            "unknown repo placeholder": lambda value: value["review"].update(
                repo_present="REPO: {project}"
            ),
            "missing count placeholder": lambda value: value["distillation"].update(
                stable_omitted_line="Additional findings omitted."
            ),
            "wrong heading placeholder": lambda value: value["distillation"].update(
                heading_template="## {title}"
            ),
            "incomplete finding template": lambda value: value["distillation"].update(
                finding_line_template="{index}. {issue}"
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                changed = json.loads(json.dumps(contract))
                mutate(changed)
                path = Path(tmp) / "contract.json"
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(self.helper.ContractError):
                    self.helper.load_contract(path)


if __name__ == "__main__":
    unittest.main()
