"""Privacy-safe aggregates for the local evidence dashboard."""

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from retro_eval.annotation import _packet_fingerprint  # noqa: E402
from retro_eval.review_dashboard import build_review_dashboard  # noqa: E402
from retro_eval.taxonomy_packets import TAXONOMY_FIELDS  # noqa: E402


class ReviewDashboardTests(unittest.TestCase):
    def test_recommendations_follow_captured_human_labels_not_assessments(self):
        from retro_eval.catalog import load_rubric_catalogue
        rubrics = {r.id: r for r in load_rubric_catalogue(
            PLUGIN_ROOT / "rubrics" / "rubrics.json").rubrics}
        for stem, rubric_id, label in (("duplicate-work", "duplicate_work", "wasteful_duplicate"),
                                      ("tool-failure", "tool_failure_kind", "execution_failure")):
            self.packet(stem + "-test-round1", rubric=rubric_id,
                        version=rubrics[rubric_id].version, split="test", rows=[
                            {"proposed_label": label, "human_label": label},
                            {"proposed_label": label, "assessment": "unsure"},
                        ])
        dashboard = build_review_dashboard(self.review_dir, repo_root=self.repo)
        recommendations = {r["proposal"]: r for r in dashboard["recommendations"]}
        for proposal in ("P1", "P4"):
            self.assertEqual(1, dashboard["taxonomies"][proposal]["human_labeled"])
            self.assertIn("1 of 2", recommendations[proposal]["evidence_basis"])
            self.assertNotIn("zero human", recommendations[proposal]["evidence_basis"])
            self.assertFalse(recommendations[proposal]["decision_support"])
            self.assertIn("scoped validation", recommendations[proposal]["revisit_when"])

    def test_controlled_coverage_requires_explicit_consistent_nonempty_counts(self):
        coverage_path = self.retro_home / "current-base" / "p3-controlled" / "report.json"
        cases = [
            ({}, "not_observed", False),
            ({"population": 0, "resolved": 0, "unresolved": 0}, "not_observed", False),
            ({"population": 2, "resolved": 2, "unresolved": 0}, "controlled_pass", True),
            ({"population": 2, "resolved": 1, "unresolved": 1}, "controlled_incomplete", False),
            ({"population": 2, "resolved": 2}, "incomplete_evidence", False),
            ({"population": 2, "resolved": 2, "unresolved": 1}, "inconsistent_evidence", False),
            ({"population": True, "resolved": True, "unresolved": False}, "incomplete_evidence", False),
            ({"population": 2, "resolved": -1, "unresolved": 3}, "incomplete_evidence", False),
        ]
        for coverage, state, passed in cases:
            with self.subTest(coverage=coverage):
                coverage_path.write_text(json.dumps({"dataset_manifest": {
                    "instruction_manifest_coverage": coverage}}))
                dashboard = build_review_dashboard(self.review_dir, repo_root=self.repo)
                self.assertEqual(state, dashboard["proposals"]["P3"]["state"])
                recommendation = next(r for r in dashboard["recommendations"]
                                      if r["proposal"] == "P3")
                self.assertEqual(passed, recommendation["decision_support"])
                self.assertEqual("adopt" if passed else "hold", recommendation["decision"])
                if "unresolved" not in coverage:
                    self.assertIsNone(dashboard["proposals"]["P3"]["unresolved"])

    def test_metric_rows_preserve_reason_for_withheld_interval(self):
        from retro_eval.review_dashboard import _metric_rows
        uncertainty = {
            "status": "not_estimated",
            "reason": "sampling_design_not_established",
            "scope": "observed_records",
            "next_action": "Specify and evaluate a sampling design.",
        }
        rows = _metric_rows({"sources": {"fixture": [{
            "scorer_id": "tool_failure_rate", "value": .5,
            "numerator": 5, "eligible_population": 10,
            "details": {"uncertainty": uncertainty},
        }, {"scorer_id": "legacy_rate"}]}})
        self.assertEqual(uncertainty, rows[0]["uncertainty"])
        self.assertIsNone(rows[0]["interval_low"])
        self.assertEqual(.5, rows[0]["value"])
        self.assertEqual({}, rows[1]["uncertainty"])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.retro_home = Path(self.temporary.name)
        self.review_dir = (self.retro_home / "annotations" /
                           "proposal-taxonomies")
        self.review_dir.mkdir(parents=True)
        (self.retro_home / "cross-harness-v4").mkdir()
        (self.retro_home / "current-base" / "p3-controlled").mkdir(parents=True)
        (self.retro_home / "current-base" / "p7-controlled").mkdir(parents=True)
        self.repo = self.retro_home / "repo"
        self.repo.mkdir()
        self.git("init")
        self.git("config", "user.name", "Fixture Agent")
        self.git("config", "user.email", "fixture")
        (self.repo / "tracked.txt").write_text("before\n", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "fixture base")
        (self.repo / "tracked.txt").write_text("after\n", encoding="utf-8")
        (self.repo / "new.txt").write_text("new line\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args):
        env = dict(os.environ)
        env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True,
            capture_output=True, text=True, env=env)

    def packet(self, stem, *, rubric, version, split, rows):
        source = self.review_dir / (stem + ".csv")
        fields = TAXONOMY_FIELDS
        if rubric == "interpretation_grounding":
            fields += ("review_kind", "situation_summary", "interpretation",
                       "rationale", "expected_action")
        normalized = []
        for index, values in enumerate(rows):
            row = {field: "" for field in fields}
            row.update({"case_id": "%s-%d" % (stem, index),
                        "source": "fixture", "split": split,
                        "context": "redacted", "user_turn": "redacted"})
            row.update(values)
            normalized.append(row)
        with source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerows(normalized)
        manifest = {
            "schema_version": 1, "dataset_id": stem,
            "rubric_id": rubric, "rubric_version": version,
            "split": split, "sample_sha256": _packet_fingerprint(source),
            "review_round": 1,
            "annotation_protocol_id": "%s-protocol" % rubric,
            "annotation_protocol_version": version + 1,
            "adaptive_sampling": {
                "round": 1, "minimum_heldout_per_label": 20,
                "support_labels": sorted({
                    str(row.get("proposed_label") or "unknown") for row in rows}),
            },
        }
        (self.review_dir / (stem + "-manifest.json")).write_text(
            json.dumps(manifest), encoding="utf-8")

    def test_taxonomy_split_counts_include_every_review_round(self):
        from retro_eval.review_dashboard import _packets, _taxonomy_summary
        for round_number, count in ((1, 3), (2, 2)):
            stem = "duplicate-work-test-round%d" % round_number
            self.packet(stem, rubric="duplicate_work", version=5, split="test",
                rows=[{"proposed_label": "wasteful_duplicate", "assessment": "correct",
                       "human_label": "wasteful_duplicate"} for _ in range(count)])
            path = self.review_dir / (stem + "-manifest.json")
            manifest = json.loads(path.read_text())
            manifest["adaptive_sampling"]["round"] = round_number
            path.write_text(json.dumps(manifest))
        packets = _packets(self.review_dir)
        result = _taxonomy_summary(packets, "duplicate_work")
        split = result["splits"]["heldout"]
        self.assertEqual(5, split["sample_size"])
        self.assertEqual(5, split["human_labeled"])
        self.assertEqual(5, split["assessed"])
        self.assertEqual({"wasteful_duplicate": 5}, split["proposed_support"])
        self.assertEqual([1, 2], split["rounds"])
        self.assertEqual([1, 2], [b["adaptive_round"] for b in split["bindings"]])
        self.assertEqual(result, _taxonomy_summary(list(reversed(packets)), "duplicate_work"))
        self.assertEqual(5, result["sample_size"])
        self.assertFalse(result["validated"])
        packets[1][0]["annotation_protocol_version"] = 99
        packets[1][0]["adaptive_sampling"]["minimum_heldout_per_label"] = 30
        mixed = _taxonomy_summary(packets, "duplicate_work")["splits"]["heldout"]
        self.assertIsNone(mixed["protocol_version"])
        self.assertIsNone(mixed["minimum_heldout_per_label"])
        self.assertEqual([6, 99], [binding["protocol_version"] for binding in mixed["bindings"]])
        self.assertEqual(5, mixed["sample_size"])

    def reports(self):
        report = {
            "schema_version": 1,
            "dataset_manifest": {
                "dataset_id": "fixture-report", "population": 1863,
                "adapter_versions": {"claude": 3, "codex": 3},
                "scorer_versions": {"repeated_call_rate": 4},
                "rubric_versions": {"duplicate_work": 4},
                "trace_schema_versions": [3],
            },
            "coverage_summary": {
                "claude": {"measured": 4, "not_observable": 12,
                           "not_scored": 8, "dependency_unavailable": 1},
                "codex": {"measured": 2, "not_observable": 11,
                          "not_scored": 11, "dependency_unavailable": 1},
            },
            "sources": {
                "claude": [{"scorer_id": "repeated_call_rate",
                            "scorer_version": 4, "numerator": 423,
                            "eligible_population": 60284,
                            "value": 423 / 60284,
                            "interval_low": 0.0063,
                            "interval_high": 0.0078,
                            "uncertainty_method": "Wilson 95% interval",
                            "label": "measured", "abstained": False,
                            "limitations": ["candidate only"],
                            "details": {"decision_support": False}},
                           {"scorer_id": "skill_invocation_rate",
                            "scorer_version": 2, "label": "measured",
                            "numerator": 575, "eligible_population": 1799,
                            "value": 575 / 1799,
                            "details": {"starts": 756, "matched_terminals": 260,
                                        "ends": 260, "unmatched_starts": 496,
                                        "explicit_chain_steps": 0,
                                        "lifecycle_completion_rate": 260 / 756,
                                        "unmatched_start_rate": 496 / 756,
                                        "orphan_terminal_rate": 0.0,
                                        "missed_trigger_rate": "not_observable",
                                        "opportunity_rate": "not_observable"}}],
                "codex": [{"scorer_id": "repeated_call_rate",
                           "scorer_version": 4, "numerator": 5635,
                           "eligible_population": 33151,
                           "value": 5635 / 33151,
                           "interval_low": 0.166,
                           "interval_high": 0.1741,
                           "uncertainty_method": "Wilson 95% interval",
                           "label": "measured", "abstained": False,
                           "limitations": ["candidate only"],
                           "details": {"decision_support": False}}],
            },
        }
        (self.retro_home / "cross-harness-v4" / "report.json").write_text(
            json.dumps(report), encoding="utf-8")
        p3 = {"dataset_manifest": {"instruction_manifest_coverage": {
            "population": 2, "resolved": 2, "unresolved": 0}}}
        (self.retro_home / "current-base" / "p3-controlled" /
         "report.json").write_text(json.dumps(p3), encoding="utf-8")
        p7 = {"capture_precision": 1.0, "capture_recall": 1.0,
              "unmatched_terminal_rate": 0.0,
              "added_normalized_byte_share": 0.000010146,
              "coverage": "repository_owned_wrapped_hooks_only",
              "harness_wide_opportunity_coverage": "not_observable"}
        (self.retro_home / "current-base" / "p7-controlled" /
         "owned-hook-report.json").write_text(json.dumps(p7), encoding="utf-8")

    def test_generic_metrics_include_every_report_source_and_preserve_missingness(self):
        self.reports()
        report_path = self.retro_home / "cross-harness-v4" / "report.json"
        report = json.loads(report_path.read_text())
        for source, value in (("antigravity", 0.1), ("extension", None)):
            metric = dict(report["sources"]["codex"][0])
            metric.update(value=value, numerator=None, eligible_population=0,
                          population=None, interval_low=None, interval_high=None)
            report["sources"][source] = [metric]
        report_path.write_text(json.dumps(report))
        dashboard = build_review_dashboard(self.review_dir, repo_root=self.repo)
        for taxonomy in dashboard["taxonomies"].values():
            self.assertEqual(taxonomy["splits"], {})
        self.assertEqual({m["harness"] for m in dashboard["metrics"]}, set(report["sources"]))
        self.assertEqual({m["harness"] for m in dashboard["corpus_candidates"]}, set(report["sources"]))
        candidate = next(m for m in dashboard["corpus_candidates"] if m["harness"] == "extension")
        self.assertIsNone(candidate["rate"])
        self.assertIsNone(candidate["count"])
        self.assertEqual(candidate["eligible"], 0)
        metric = next(m for m in dashboard["metrics"] if m["harness"] == "extension")
        for key in ("value", "numerator", "population"):
            self.assertIsNone(metric[key])
        report["sources"]["antigravity"][0]["scorer_version"] = 99
        report_path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "mixed repeated-call versions"):
            build_review_dashboard(self.review_dir, repo_root=self.repo)

    def test_disagreement_actions_do_not_invent_causes_from_counts(self):
        self.packet("unrelated-calibration", rubric="interpretation_grounding",
                    version=1, split="calibration", rows=[
                        {"assessment": "wrong", "review_kind": "agent_judgment",
                         "situation_summary": "An arithmetic calculation returned the wrong sum."},
                        {"assessment": "partly_accurate", "review_kind": "user_understanding",
                         "situation_summary": "The requested file type was misunderstood."},
                        {"assessment": "not_enough_context", "review_kind": "agent_judgment",
                         "situation_summary": "The cited source is unavailable."},
                    ])
        dashboard = build_review_dashboard(self.review_dir, repo_root=self.repo)
        self.assertEqual(dashboard["calibration"]["completed"], 3)
        self.assertEqual(len(dashboard["changes"]), 3)
        rendered = json.dumps(dashboard["changes"]).lower()
        for unsupported in ("polling", "standing preferences", "brevity",
                            "two adjacent waits", "repeat history"):
            self.assertNotIn(unsupported, rendered)
        self.assertIn("review", rendered)
        self.assertIn("context", rendered)
        self.assertNotIn("arithmetic", rendered)
        self.assertNotIn("file type", rendered)
        self.assertNotIn("cited source", rendered)
        self.packet("unrelated-calibration", rubric="interpretation_grounding",
                    version=1, split="calibration", rows=[
                        {"assessment": "accurate", "review_kind": "agent_judgment"}])
        self.assertEqual(build_review_dashboard(self.review_dir, repo_root=self.repo)["changes"], [])

    def test_builds_calibration_taxonomy_and_owned_observability_summary(self):
        self.packet("mixed-interpretation-calibration-round1",
                    rubric="interpretation_grounding", version=1,
                    split="calibration", rows=[
                        {"review_kind": "user_understanding",
                         "assessment": "accurate"},
                        {"review_kind": "agent_judgment",
                         "assessment": "partly_accurate"},
                        {"review_kind": "agent_judgment",
                         "assessment": "wrong"},
                        {"review_kind": "user_understanding",
                         "assessment": "not_enough_context"},
                    ])
        self.packet("duplicate-work-calibration-round1",
                    rubric="duplicate_work", version=4,
                    split="calibration", rows=[
                        {"proposed_label": "wasteful_duplicate"},
                        {"proposed_label": "ambiguous"},
                    ])
        self.packet("tool-failure-test-round1", rubric="tool_failure_kind",
                    version=2, split="test", rows=[
                        {"proposed_label": "execution_failure"}])
        self.reports()

        dashboard = build_review_dashboard(self.review_dir,
                                             repo_root=self.repo)

        self.assertEqual(4, dashboard["schema_version"])
        self.assertEqual(2, dashboard["changeset"]["file_count"])
        self.assertEqual(2, dashboard["changeset"]["additions"])
        self.assertEqual(1, dashboard["changeset"]["deletions"])
        changed = {item["path"]: item
                   for item in dashboard["changeset"]["files"]}
        self.assertEqual("modified", changed["tracked.txt"]["status"])
        self.assertEqual("added", changed["new.txt"]["status"])
        self.assertIn("-before", changed["tracked.txt"]["patch"])
        self.assertIn("+after", changed["tracked.txt"]["patch"])
        self.assertIn("--- /dev/null", changed["new.txt"]["patch"])
        self.assertEqual(
            ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"],
            [item["proposal"] for item in dashboard["recommendations"]])
        recommendations = {
            item["proposal"]: item for item in dashboard["recommendations"]}
        self.assertEqual("hold", recommendations["P1"]["decision"])
        self.assertFalse(recommendations["P1"]["decision_support"])
        self.assertIn("candidate generation",
                      recommendations["P1"]["recommended_action"])
        self.assertEqual("adopt", recommendations["P3"]["decision"])
        self.assertEqual("adopt_gate_only",
                         recommendations["P5"]["decision"])
        self.assertIn("fixed task protocol",
                      recommendations["P5"]["revisit_when"])
        self.assertEqual("retired",
                         recommendations["P7"]["decision"])
        self.assertEqual({"accurate": 1, "partly_accurate": 1, "wrong": 1,
                          "not_enough_context": 1},
                         dashboard["calibration"]["assessment_counts"])
        self.assertEqual(2, dashboard["calibration"]["by_review_kind"]
                         ["agent_judgment"]["total"])
        self.assertFalse(dashboard["taxonomies"]["P1"]["validated"])
        self.assertEqual(0, dashboard["taxonomies"]["P1"]["labeled"])
        self.assertEqual(5635, dashboard["corpus_candidates"][1]["count"])
        self.assertFalse(dashboard["corpus_candidates"][1]["decision_support"])
        self.assertEqual("not_observable",
                         dashboard["proposals"]["P2"]["missed_trigger_rate"])
        self.assertEqual(2, dashboard["proposals"]["P3"]["resolved"])
        self.assertFalse(recommendations["P7"]["decision_support"])
        self.assertEqual("retired", dashboard["proposals"]["P7"]["state"])
        self.assertTrue(dashboard["proposals"]["P7"]["historical_only"])
        self.assertEqual("repository_owned_wrapped_hooks_only",
                         dashboard["proposals"]["P7"]["coverage"])
        self.assertEqual("fixture-report", dashboard["run"]["dataset_id"])
        self.assertEqual(3, dashboard["run"]["adapter_versions"]["claude"])
        self.assertEqual(4, dashboard["coverage"]["claude"]["measured"])
        repeated = next(item for item in dashboard["metrics"]
                        if item["harness"] == "codex"
                        and item["metric_id"] == "repeated_call_rate")
        self.assertEqual(0.166, repeated["interval_low"])
        self.assertFalse(repeated["decision_support"])
        p1_split = dashboard["taxonomies"]["P1"]["splits"]["calibration"]
        self.assertEqual({"ambiguous": 1, "wasteful_duplicate": 1},
                         p1_split["proposed_support"])
        self.assertEqual(20, p1_split["minimum_heldout_per_label"])
        self.assertEqual(2, dashboard["proposals"]["P2"]["scorer_version"])
        self.assertEqual(496, dashboard["proposals"]["P2"]["unmatched_starts"])
        self.assertEqual("not_observable",
                         dashboard["proposals"]["P2"]["opportunity_rate"])
        serialized = json.dumps(dashboard)
        self.assertNotIn(str(self.retro_home), serialized)
        self.assertNotIn("redacted", serialized)

    def test_rejects_mixed_repeated_call_scorer_versions(self):
        self.packet("mixed-interpretation-calibration-round1",
                    rubric="interpretation_grounding", version=1,
                    split="calibration", rows=[
                        {"review_kind": "agent_judgment",
                         "assessment": "accurate"}])
        self.reports()
        report_path = self.retro_home / "cross-harness-v4" / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["sources"]["codex"][0]["scorer_version"] = 3
        report_path.write_text(json.dumps(report), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "mixed repeated-call versions"):
            build_review_dashboard(self.review_dir, repo_root=self.repo)


if __name__ == "__main__":
    unittest.main()
