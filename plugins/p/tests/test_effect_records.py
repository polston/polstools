import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CHECKER = (PLUGIN_ROOT / "skills" / "reviewing-improvement-effects" /
           "scripts" / "check_record.py")
spec = importlib.util.spec_from_file_location("effect_record_checker", CHECKER)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def synthetic_bundle():
    """A bounded synthetic comparison with explicit contracts and observations."""
    proposal = {
        "proposal_id": "P-parent-use", "target_kind": "prompt",
        "target_ref": "parent consumption brief", "population": 6,
        "session_count": 2, "evidence_refs": ["origin"],
        "observed_rate": None, "uncertainty": "small local pilot",
        "expected_impact": "use correct child results without repeating the search",
        "exact_change": "consume sufficient references unless verification is justified",
        "experiment": "replay six matched source-search tasks with unchanged inputs",
        "success_threshold": "zero unjustified repeats with all results correct",
        "rollback": "restore the previous brief if correctness or boundaries regress",
        "confidence": 0.5, "avoidable_cost": 0, "status": "implemented",
    }
    cohort = {
        "task_family": "bounded source-reference search",
        "source_population": ["synthetic/main-parent-and-read-only-child"],
        "selection_rule": "all six prespecified tasks, including an empty result",
        "observation_method": "direct execution checks against fixed expected references",
        "unit": "task", "count": 6, "window": "closed synthetic replay",
        "exclusions": [], "activation_version": "v1",
        "facts": {
            "response_completion": 6, "verified_delivery": 6, "later_use": 4,
            "reopened_work": 0, "parent_rework": 2, "user_interventions": 2,
        },
        "quality": "all six results correct; outside writes refused; required verification retained",
        "followup_horizon": "through each accepted parent continuation",
    }
    followup = copy.deepcopy(cohort)
    followup["activation_version"] = "v2"
    followup["facts"].update(later_use=6, parent_rework=0, user_interventions=0)
    return {
        "proposal": proposal,
        "origin": {"request": "evaluate the existing parent-consumption proposal"},
        "activation": {"version": "v2", "observed": "every candidate dispatch loaded v2"},
        "baseline": cohort, "followup": followup,
        "comparison": {
            "design": "same six input tasks, counterbalanced order, fixed expected answers",
            "benefit": "unjustified repeats declined from two to zero",
            "quality": "all six deliveries correct; boundary probes refused in both arms",
            "limits": "small synthetic pilot; no claim about other task families",
        },
    }


def synthetic_record(bundle):
    record = json.loads((CHECKER.parents[1] / "references" /
                         "record-template.json").read_text(encoding="utf-8"))
    record.update(
        proposal_id="P-parent-use", proposal_ref="proposal", owner="pilot agent",
        task_family=bundle["baseline"]["task_family"],
        intended_benefit="remove unjustified repeat searches while preserving correctness",
        quality_constraints=["all expected references correct", "read-only boundary retained"],
        review_trigger="after the six prespecified synthetic tasks",
        rollback=bundle["proposal"]["rollback"],
        activation={"version": "v2", "evidence_ref": "activation"},
        baseline_ref="baseline", followup_ref="followup",
        comparison={"status": "matched", "basis_ref": "comparison",
                    "limitations": bundle["comparison"]["limits"]},
        decision_signals=["verified_delivery", "later_use", "parent_rework"],
        benefit={"status": "supported", "refs": ["comparison"]},
        quality={"status": "preserved", "refs": ["comparison"]},
        disposition="keep", rationale="local pilot meets the prespecified threshold",
        next_action={"owner": "pilot agent", "action": "retain this scoped brief",
                     "prerequisites": "current matched checks remain valid",
                     "acceptance": "correct results, used by parent, no extra authority"},
    )
    for signal in checker.SIGNALS:
        for phase in ("baseline", "followup"):
            record["observations"][signal][phase] = {
                "value": bundle[phase]["facts"][signal], "refs": [phase]}
    return record


def write_packet(root, bundle, record):
    artifact = root / "facts.json"
    artifact.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    index = {"schema_version": 1, "evidence": [
        {"ref": key, "artifact": artifact.name, "sha256": digest,
         "json_pointer": "/" + key} for key in bundle]}
    index_path = root / "evidence-index.json"
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    record_path = root / "effect-record.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record_path, index_path


class EffectRecordTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = synthetic_bundle()
        self.record = synthetic_record(self.bundle)

    def check(self):
        return checker.check_record(*write_packet(self.root, self.bundle, self.record))

    def test_complete_bound_comparison_checks_without_applying(self):
        result = self.check()
        self.assertEqual([], result["gaps"])
        self.assertFalse(result["auto_apply"])
        self.assertIn("origin", result["evidence_refs_checked"])
        self.assertEqual("keep", result["disposition"])
        self.assertEqual(64, len(result["record_sha256"]))

    def test_all_review_dispositions_remain_authored_not_auto_applied(self):
        for disposition in checker.DISPOSITIONS:
            with self.subTest(disposition=disposition):
                self.record["disposition"] = disposition
                result = self.check()
                self.assertEqual(disposition, result["disposition"])
                self.assertFalse(result["auto_apply"])

    def test_completion_does_not_supply_missing_delivery_or_use(self):
        for signal in ("verified_delivery", "later_use"):
            self.record["observations"][signal]["followup"] = {"value": None, "refs": []}
        result = self.check()
        self.assertIn("followup_verified_delivery_unknown", result["gaps"])
        self.assertIn("followup_later_use_unknown", result["gaps"])
        self.assertFalse(result["declared_prerequisites_present"])

    def test_changed_source_population_cannot_pass_as_matched(self):
        self.bundle["followup"]["source_population"] = ["another-synthetic-harness/child"]
        self.assertIn("cohort_metadata_not_matched", self.check()["gaps"])

    def test_different_denominator_units_cannot_pass_as_matched(self):
        self.bundle["followup"]["unit"] = "session"
        self.assertIn("cohort_metadata_not_matched", self.check()["gaps"])

    def test_existing_proposal_claim_bindings_must_resolve_and_match(self):
        proposal = self.bundle["proposal"]
        proposal["evidence_refs"].append("baseline")
        proposal["evidence_binding"] = {
            "fields": {"population": {"ref": "baseline", "pointer": "/count"}}}
        self.assertEqual([], self.check()["gaps"])
        proposal["population"] = 99
        with self.assertRaisesRegex(ValueError, "claim mismatch"):
            self.check()
        proposal["population"] = 6
        proposal["evidence_binding"]["fields"]["population"]["pointer"] = "/absent"
        with self.assertRaisesRegex(ValueError, "cannot be resolved"):
            self.check()

    def test_absent_activation_and_followup_are_gaps_not_zero_effect(self):
        self.record.update(activation=None, followup_ref=None,
                           disposition="insufficient_evidence")
        result = self.check()
        self.assertIn("activation_unknown", result["gaps"])
        self.assertIn("followup_missing", result["gaps"])

    def test_followup_version_must_match_observed_activation(self):
        self.bundle["followup"]["activation_version"] = "v1"
        self.assertIn("followup_activation_not_bound", self.check()["gaps"])

    def test_missing_population_bounds_or_empty_population_is_a_gap(self):
        self.bundle["followup"].update(count=0, window="")
        self.assertIn("followup_population_or_bounds_missing", self.check()["gaps"])

    def test_quality_regression_cannot_receive_a_clean_keep(self):
        self.record["quality"]["status"] = "regressed"
        self.assertIn("keep_conflicts_with_benefit_or_quality", self.check()["gaps"])
        self.record["disposition"] = "revert"
        self.assertNotIn("keep_conflicts_with_benefit_or_quality", self.check()["gaps"])

    def test_changed_artifact_fingerprint_is_rejected(self):
        paths = write_packet(self.root, self.bundle, self.record)
        (self.root / "facts.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            checker.check_record(*paths)

    def test_unresolved_and_unhashed_references_are_rejected(self):
        self.record["followup_ref"] = "missing"
        paths = write_packet(self.root, self.bundle, self.record)
        payload = json.loads(paths[1].read_text(encoding="utf-8"))
        payload["evidence_refs"] = ["missing"]
        paths[1].write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fingerprinted artifact"):
            checker.check_record(*paths)

    def test_proposal_identity_must_resolve_to_existing_object(self):
        self.record["proposal_id"] = "unrelated"
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.check()

    def test_observed_values_require_evidence_and_signals_cannot_disappear(self):
        self.record["observations"]["verified_delivery"]["followup"]["refs"] = []
        with self.assertRaisesRegex(ValueError, "require evidence"):
            self.check()
        del self.record["observations"]["verified_delivery"]
        with self.assertRaisesRegex(ValueError, "distinct observation"):
            self.check()

    def test_unvalidated_rubric_is_a_gap_even_on_implemented_proposal(self):
        self.bundle["proposal"]["evidence_rubric_ids"] = ["turn_friction_legacy"]
        self.assertIn("rubric_not_decision_eligible:turn_friction_legacy",
                      self.check()["gaps"])

    def test_repository_record_and_artifact_are_rejected(self):
        paths = write_packet(self.root, self.bundle, self.record)
        (self.root / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "outside repositories"):
            checker.check_record(*paths)

    def test_repository_artifact_is_rejected_before_loading(self):
        paths = write_packet(self.root, self.bundle, self.record)
        repo = self.root / "repo"
        repo.mkdir()
        (repo / ".git").write_text("gitdir: external", encoding="utf-8")
        (self.root / "facts.json").replace(repo / "facts.json")
        index = json.loads(paths[1].read_text(encoding="utf-8"))
        for item in index["evidence"]:
            item["artifact"] = "repo/facts.json"
        paths[1].write_text(json.dumps(index), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outside repositories"):
            checker.check_record(*paths)

    def test_symlink_to_repository_artifact_cannot_cross_external_boundary(self):
        paths = write_packet(self.root, self.bundle, self.record)
        repo = self.root / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        artifact = self.root / "facts.json"
        artifact.replace(repo / "facts.json")
        try:
            artifact.symlink_to(repo / "facts.json")
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaisesRegex(ValueError, "outside repositories"):
            checker.check_record(*paths)

    def test_instruction_manifest_reference_uses_existing_fingerprint_validation(self):
        from retro_eval.instruction_manifest import (InstructionSource,
                                                     write_instruction_manifest)
        from datetime import datetime, timezone
        manifest_path = self.root / "instructions.json"
        write_instruction_manifest(
            manifest_path, [InstructionSource("skill", "a" * 64, version="v2")],
            activated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.record["activation"]["instruction_manifest_ref"] = "instructions"
        paths = write_packet(self.root, self.bundle, self.record)
        index = json.loads(paths[1].read_text(encoding="utf-8"))

        def bind_manifest():
            entry = {"ref": "instructions", "artifact": manifest_path.name,
                     "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                     "json_pointer": ""}
            index["evidence"] = [x for x in index["evidence"] if x["ref"] != "instructions"]
            index["evidence"].append(entry)
            paths[1].write_text(json.dumps(index), encoding="utf-8")

        bind_manifest()
        self.assertEqual([], checker.check_record(*paths)["gaps"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sources"][0]["version"] = "tampered"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        bind_manifest()
        with self.assertRaisesRegex(ValueError, "manifest fingerprint mismatch"):
            checker.check_record(*paths)

    def test_cli_exit_codes_distinguish_checked_gaps_and_invalid_input(self):
        paths = write_packet(self.root, self.bundle, self.record)
        command = [sys.executable, "-B", str(CHECKER), "--record", str(paths[0]),
                   "--evidence-index", str(paths[1])]
        self.assertEqual(0, subprocess.run(command, capture_output=True).returncode)
        self.record["activation"] = None
        write_packet(self.root, self.bundle, self.record)
        self.assertEqual(1, subprocess.run(command, capture_output=True).returncode)
        paths[0].write_text("[]", encoding="utf-8")
        self.assertEqual(2, subprocess.run(command, capture_output=True).returncode)


if __name__ == "__main__":
    unittest.main()
