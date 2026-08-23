import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.adapters.base import AdapterBase, AdapterResult
from retro_eval.adapters.registry import AdapterRegistration, AdapterRegistry
from retro_eval.dataset import DatasetPolicy, stable_split
from retro_eval.pipeline import EvaluationPipeline
from retro_eval.proposals import (Proposal, ProposalPolicy, proposal_review,
                                  rank_proposals)
from retro_eval.schema import SCHEMA_VERSION, TraceRecord


class FakeAdapter(AdapterBase):
    source = "fixture"
    capabilities = {}

    def read(self, path, root):
        return AdapterResult(
            source=self.source,
            included=True,
            exclusion_reason="",
            is_subagent=False,
            records=(),
            human_prompt_count=1,
        )


class RecordingStore:
    def __init__(self):
        self.records = None

    def write(self, records):
        self.records = list(records)


def proposal(proposal_id, *, sessions=2, evidence=("a", "b"), confidence=.5,
             cost=1.0, evidence_unit="session", independent_evidence_count=0):
    return Proposal(
        proposal_id=proposal_id, target_kind="skill", target_ref="sample",
        population=10, session_count=sessions, evidence_refs=evidence,
        observed_rate=.2, uncertainty="Wilson 95% interval",
        expected_impact="reduce repeat work", exact_change="change config",
        experiment="replay held-out traces", success_threshold="precision >= .8",
        rollback="restore prior config", confidence=confidence,
        avoidable_cost=cost,
        evidence_unit=evidence_unit,
        independent_evidence_count=independent_evidence_count,
    )


class ModularityTests(unittest.TestCase):
    def test_pipeline_accepts_registered_adapter_and_store_without_callsite_edit(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as work:
            source = Path(raw)
            (source / "one.fixture").write_text("{}\n", encoding="utf-8")
            registry = AdapterRegistry((AdapterRegistration(
                name="fixture",
                factory=lambda salt, options: FakeAdapter(salt),
                discover=lambda root: root.rglob("*.fixture"),
            ),))
            store = RecordingStore()
            summary = EvaluationPipeline(
                Path(work), registry=registry, trace_store=store,
            ).extract(roots={"fixture": source})
            self.assertEqual(1, summary.included_traces)
            self.assertEqual([], store.records)

    def test_unknown_namespaced_span_kind_round_trips(self):
        payload = {
            "schema_version": SCHEMA_VERSION, "trace_id": "t", "span_id": "s",
            "parent_span_id": None, "source": "fixture", "adapter_version": 1,
            "source_version": "1", "span_kind": "vendor.custom_event",
            "started_at": None, "ended_at": None, "sequence": 1,
        }
        record = TraceRecord.from_dict(payload)
        self.assertEqual("vendor.custom_event", record.span_kind)
        self.assertEqual("vendor.custom_event", record.to_dict()["span_kind"])

    def test_dataset_split_has_explicit_versioned_policy(self):
        policy = DatasetPolicy(schema_version=1, calibration_share=55)
        self.assertEqual(
            stable_split("trace", b"salt", policy),
            stable_split("trace", b"salt", policy),
        )
        self.assertIn(stable_split("trace", b"salt"), {"calibration", "test"})

    def test_proposal_thresholds_and_rank_order_are_policy(self):
        strict = ProposalPolicy(
            schema_version=1, minimum_sessions=3, minimum_evidence_refs=3,
            rank_by=(("avoidable_cost", "desc"), ("confidence", "desc")),
        )
        self.assertEqual("insufficient sessions", proposal("a").suppression_reason(strict))
        eligible = [
            proposal("confidence", sessions=3, evidence=("a", "b", "c"),
                     confidence=.9, cost=1),
            proposal("cost", sessions=3, evidence=("a", "b", "c"),
                     confidence=.5, cost=9),
        ]
        self.assertEqual("cost", rank_proposals(eligible, strict)[0].proposal_id)

    def test_profile_loader_rejects_unknown_factory_options(self):
        profile = {
            "schema_version": 1,
            "sources": [{
                "name": "fixture", "module": "test_eval_modularity",
                "class": "FakeAdapter", "glob": "*.fixture",
                "options": {"made_up": True},
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            registry = AdapterRegistry.from_profile(path)
            registration = registry.get("fixture")
            with self.assertRaises(ValueError):
                registration.create(b"salt")

    def test_non_session_proposals_use_declared_independent_evidence_units(self):
        policy = ProposalPolicy(
            schema_version=1, minimum_sessions=2, minimum_evidence_refs=2,
            rank_by=(("confidence", "desc"),), minimum_independent_units=2,
        )
        item = proposal(
            "rules", sessions=0, evidence=("source-a", "source-b", "source-c"),
            evidence_unit="rule_source", independent_evidence_count=3,
        )
        self.assertEqual("", item.suppression_reason(policy))

    def test_review_keeps_suppressed_candidates_visible_but_unranked(self):
        policy = ProposalPolicy(
            schema_version=1, minimum_sessions=2, minimum_evidence_refs=2,
            rank_by=(("confidence", "desc"),),
        )
        review = proposal_review([proposal("kept"), proposal("weak", sessions=1)],
                                 policy)
        self.assertEqual("kept", review["ranked"][0]["proposal_id"])
        self.assertEqual("insufficient sessions",
                         review["suppressed"][0]["suppression_reason"])

    def test_resolved_proposals_are_visible_but_no_longer_ranked(self):
        item = proposal("done")
        item.status = "implemented"
        review = proposal_review([item])
        self.assertEqual([], review["ranked"])
        self.assertEqual("implemented", review["resolved"][0]["status"])


if __name__ == "__main__":
    unittest.main()
