import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.proposal_report import (ResolvedEvidence, build_proposal_review,
                                        load_evidence,
                                        load_evidence_refs)  # noqa: E402


class ProposalReportTests(unittest.TestCase):
    def test_proposal_launcher_exposes_strict_evidence_options(self):
        result = subprocess.run(
            [sys.executable, str(PLUGIN_ROOT / "bin" / "retro-eval-proposals"),
             "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--trace-evidence", result.stdout)
        self.assertIn("--evidence-index", result.stdout)

    def test_review_renders_required_fields_and_never_applies(self):
        candidate = {
            "proposal_id": "P1", "target_kind": "metric", "target_ref": "repeat",
            "population": 10, "session_count": 2, "evidence_refs": ["a", "b"],
            "observed_rate": .2, "uncertainty": "95% CI",
            "expected_impact": "fewer false positives", "exact_change": "split polling",
            "experiment": "label held-out cases", "success_threshold": "precision >= .9",
            "rollback": "restore v1", "confidence": .9, "avoidable_cost": 10,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidates.json"
            source.write_text(json.dumps({"proposals": [candidate]}), encoding="utf-8")
            review, markdown = build_proposal_review(source)
        self.assertFalse(review["auto_apply"])
        self.assertIn("Population", markdown)
        self.assertIn("Exact change", markdown)
        self.assertIn("Experiment", markdown)
        self.assertIn("Rollback", markdown)
        self.assertIn("Ask: approve, reject, or revise", markdown)
        self.assertIn("| Independent evidence | 2 session(s) |", markdown)
        self.assertNotIn("apply automatically", markdown.lower())

    def test_unobservable_rate_is_not_coerced_to_zero(self):
        candidate = {
            "proposal_id": "P2", "target_kind": "instrumentation", "target_ref": "hooks",
            "population": 2, "session_count": 0, "evidence_unit": "source",
            "independent_evidence_count": 2, "evidence_refs": ["a", "b"],
            "observed_rate": None, "uncertainty": "not observable",
            "expected_impact": "make hook outcomes measurable", "exact_change": "emit events",
            "experiment": "replay fixtures", "success_threshold": "coverage = 100%",
            "rollback": "disable events", "confidence": .9, "avoidable_cost": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.json"
            path.write_text(json.dumps({"proposals": [candidate]}), encoding="utf-8")
            _, markdown = build_proposal_review(path)
        self.assertIn("| Observed rate | not_observable |", markdown)

    def test_candidate_only_rubric_cannot_support_an_unrelated_proposal(self):
        candidate = {
            "proposal_id": "P-gated", "target_kind": "prompt",
            "target_ref": "system-prompt", "population": 40,
            "session_count": 0, "evidence_unit": "human_label",
            "independent_evidence_count": 40,
            "evidence_refs": ["labels:truth", "labels:prediction"],
            "evidence_rubric_ids": ["turn_friction_legacy"],
            "observed_rate": None, "uncertainty": "held-out",
            "expected_impact": "clearer prompts", "exact_change": "rewrite prompt",
            "experiment": "replay held-out", "success_threshold": "agreement >= .8",
            "rollback": "restore prompt", "confidence": .9, "avoidable_cost": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "candidates.json"
            scorer_check = dict(
                candidate, proposal_id="P-self-check", target_kind="scorer",
                target_ref="turn_friction_legacy")
            source.write_text(json.dumps({"proposals": [candidate, scorer_check]}),
                              encoding="utf-8")
            review, markdown = build_proposal_review(source)
        self.assertEqual("P-self-check", review["ranked"][0]["proposal_id"])
        self.assertIn("candidate-sampler-only rubric", markdown)

    def test_label_evidence_requires_rubric_provenance(self):
        candidate = {
            "proposal_id": "P-unbound", "target_kind": "prompt",
            "target_ref": "system-prompt", "population": 40,
            "session_count": 2, "evidence_refs": ["labels:truth", "other"],
            "observed_rate": None, "uncertainty": "held-out",
            "expected_impact": "clearer prompts", "exact_change": "rewrite prompt",
            "experiment": "replay held-out", "success_threshold": "agreement >= .8",
            "rollback": "restore prompt", "confidence": .9, "avoidable_cost": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "candidates.json"
            source.write_text(json.dumps({"proposals": [candidate]}),
                              encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "rubric provenance"):
                build_proposal_review(source)

    def test_population_unit_is_not_conflated_with_evidence_unit(self):
        candidate = {
            "proposal_id": "P3", "target_kind": "metric", "target_ref": "failures",
            "population": 100, "population_unit": "tool_result",
            "session_count": 2, "evidence_unit": "session",
            "evidence_refs": ["a", "b"], "observed_rate": .1,
            "uncertainty": "95% CI", "expected_impact": "classify failures",
            "exact_change": "add taxonomy", "experiment": "label results",
            "success_threshold": "precision >= .9", "rollback": "restore v1",
            "confidence": .9, "avoidable_cost": 10,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.json"
            path.write_text(json.dumps({"proposals": [candidate]}), encoding="utf-8")
            _, markdown = build_proposal_review(path)
        self.assertIn("100 tool_result(s); 2 independent session(s)", markdown)

    def test_strict_evidence_resolution_rejects_dangling_references(self):
        candidate = {
            "proposal_id": "P4", "target_kind": "metric", "target_ref": "failure",
            "population": 10, "session_count": 2,
            "evidence_refs": ["known", "missing"], "observed_rate": .1,
            "uncertainty": "95% CI", "expected_impact": "fewer failures",
            "exact_change": "add taxonomy", "experiment": "label results",
            "success_threshold": "precision >= .9", "rollback": "restore v1",
            "confidence": .9, "avoidable_cost": 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.json"
            path.write_text(json.dumps({"proposals": [candidate]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unresolved proposal evidence"):
                build_proposal_review(path, known_evidence_refs={"known"})

    def test_evidence_loader_combines_trace_and_named_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            traces = root / "traces.jsonl"
            traces.write_text(json.dumps({
                "trace_id": "trace-a", "span_id": "span-a"
            }) + "\n", encoding="utf-8")
            artifact = root / "aggregate.json"
            artifact.write_text('{"population": 2}\n', encoding="utf-8")
            index = root / "evidence.json"
            index.write_text(json.dumps({
                "schema_version": 1,
                "evidence": [{
                    "ref": "aggregate:a", "artifact": "aggregate.json",
                    "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                }],
            }), encoding="utf-8")
            refs = load_evidence_refs(traces, index)
            artifact.write_text('{"population": 3}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
                load_evidence_refs(traces, index)
        self.assertEqual({"trace-a", "span-a", "aggregate:a"}, refs)

    def test_evidence_loader_resolves_fingerprinted_json_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "report.json"
            artifact.write_text(json.dumps({
                "sources": {"one": [{"scorer_id": "rate", "value": .25,
                                      "eligible_population": 20}]}
            }), encoding="utf-8")
            index = root / "evidence.json"
            index.write_text(json.dumps({
                "schema_version": 1,
                "evidence": [{
                    "ref": "metric:one:rate", "artifact": "report.json",
                    "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    "json_pointer": "/sources/one/0",
                }],
            }), encoding="utf-8")
            resolved = load_evidence(index_path=index)
        self.assertEqual(.25, resolved.claims["metric:one:rate"]["value"])

    def test_strict_observed_proposal_requires_matching_claim_binding(self):
        candidate = {
            "proposal_id": "P5", "target_kind": "metric", "target_ref": "rate",
            "population": 20, "session_count": 2,
            "evidence_refs": ["metric:one:rate", "trace:a"], "observed_rate": .25,
            "uncertainty": "95% CI", "expected_impact": "reduce failures",
            "exact_change": "add taxonomy", "experiment": "label results",
            "success_threshold": "precision >= .9", "rollback": "restore v1",
            "confidence": .9, "avoidable_cost": 5,
        }
        resolved = ResolvedEvidence(
            refs=frozenset({"metric:one:rate", "trace:a"}),
            claims={"metric:one:rate": {"value": .25, "eligible_population": 20}},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.json"
            path.write_text(json.dumps({"proposals": [candidate]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "lacks evidence binding"):
                build_proposal_review(path, resolved_evidence=resolved)
            candidate["evidence_binding"] = {
                "fields": {
                    "population": {"ref": "metric:one:rate",
                                   "pointer": "/eligible_population"},
                    "observed_rate": {"ref": "metric:one:rate",
                                      "pointer": "/value"},
                },
            }
            path.write_text(json.dumps({"proposals": [candidate]}), encoding="utf-8")
            review, _ = build_proposal_review(path, resolved_evidence=resolved)
            candidate["population"] = 21
            path.write_text(json.dumps({"proposals": [candidate]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "claim mismatch"):
                build_proposal_review(path, resolved_evidence=resolved)
        self.assertEqual(20, review["ranked"][0]["population"])


if __name__ == "__main__":
    unittest.main()
