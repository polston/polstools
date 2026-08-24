"""Privacy-safe aggregates for the local evidence dashboard."""

import csv
import json
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
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True,
            capture_output=True, text=True)

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
        self.assertEqual("adopt_scoped",
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
