"""Build privacy-safe, version-bound aggregates for the review UI."""

from __future__ import annotations

import csv
import difflib
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from .annotation import _load_packet_manifest


_ASSESSMENT_ORDER = ("accurate", "partly_accurate", "wrong",
                     "not_enough_context")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("dashboard evidence report is unreadable") from exc


def _read_packet(source: Path, manifest_path: Path):
    manifest = _read_json(manifest_path)
    try:
        rubric_id = str(manifest["rubric_id"])
        rubric_version = int(manifest["rubric_version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("dashboard packet manifest is invalid") from exc
    _load_packet_manifest(source, manifest_path, rubric_id=rubric_id,
                          rubric_version=rubric_version)
    try:
        with source.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise ValueError("dashboard packet is unreadable") from exc
    return manifest, rows


def _packets(review_dir: Path):
    packets = []
    for manifest_path in sorted(review_dir.glob("*-manifest.json")):
        source = manifest_path.with_name(
            manifest_path.name.removesuffix("-manifest.json") + ".csv")
        manifest, rows = _read_packet(source, manifest_path)
        packets.append((manifest, rows))
    return packets


def _calibration_summary(packets):
    candidates = [(manifest, rows) for manifest, rows in packets
                  if manifest.get("rubric_id") == "interpretation_grounding"
                  and manifest.get("split") == "calibration"]
    if not candidates:
        return {"total": 0, "completed": 0,
                "assessment_counts": {key: 0 for key in _ASSESSMENT_ORDER},
                "by_review_kind": {}}
    manifest, rows = max(candidates, key=lambda item: (
        int(item[0].get("rubric_version") or 0),
        int(item[0].get("review_round") or 0)))
    counts = Counter(str(row.get("assessment") or "") for row in rows)
    by_kind = defaultdict(Counter)
    for row in rows:
        kind = str(row.get("review_kind") or "unknown")
        by_kind[kind]["total"] += 1
        assessment = str(row.get("assessment") or "")
        if assessment:
            by_kind[kind][assessment] += 1
    return {
        "total": len(rows),
        "completed": sum(counts[key] for key in _ASSESSMENT_ORDER),
        "assessment_counts": {key: counts[key] for key in _ASSESSMENT_ORDER},
        "by_review_kind": {kind: dict(values)
                           for kind, values in sorted(by_kind.items())},
        "rubric_version": int(manifest["rubric_version"]),
    }


def _taxonomy_summary(packets, rubric_id):
    relevant = [(manifest, rows) for manifest, rows in packets
                if manifest.get("rubric_id") == rubric_id]
    if not relevant:
        return {"version": None, "sample_size": 0, "labeled": 0,
                "validated": False, "class_support": {}}
    versions = {int(manifest["rubric_version"]) for manifest, _ in relevant}
    version = max(versions)
    relevant = [(manifest, rows) for manifest, rows in relevant
                if int(manifest["rubric_version"]) == version]
    rows = [row for _manifest, packet_rows in relevant for row in packet_rows]
    support = Counter(str(row.get("proposed_label") or "unknown") for row in rows)
    labeled = sum(bool(str(row.get("assessment") or "").strip()) for row in rows)
    splits = {}
    for manifest, packet_rows in relevant:
        split = "heldout" if manifest.get("split") == "test" else str(
            manifest.get("split") or "unknown")
        proposed = Counter(str(row.get("proposed_label") or "unknown")
                           for row in packet_rows)
        reasons = Counter(str(row.get("proposal_reason") or "unknown")
                          for row in packet_rows)
        adaptive = manifest.get("adaptive_sampling") or {}
        splits[split] = {
            "sample_size": len(packet_rows),
            "assessed": sum(bool(str(row.get("assessment") or "").strip())
                            for row in packet_rows),
            "human_labeled": sum(bool(str(row.get("human_label") or "").strip())
                                 for row in packet_rows),
            "proposed_support": dict(sorted(proposed.items())),
            "reason_support": dict(sorted(reasons.items())),
            "protocol_id": str(manifest.get("annotation_protocol_id") or ""),
            "protocol_version": int(
                manifest.get("annotation_protocol_version") or 0),
            "adaptive_round": int(adaptive.get("round") or 0),
            "minimum_heldout_per_label": int(
                adaptive.get("minimum_heldout_per_label") or 0),
            "support_labels": [str(value) for value in
                               adaptive.get("support_labels", [])],
        }
    return {"version": version, "sample_size": len(rows), "labeled": labeled,
            "validated": False, "class_support": dict(sorted(support.items())),
            "splits": splits}


def _find_report(directory: Path, preferred: str):
    exact = directory / preferred
    if exact.is_file():
        return exact
    candidates = sorted(directory.glob("*.json")) if directory.is_dir() else []
    return candidates[-1] if candidates else None


def _metric(report, harness, scorer_id):
    sources = report.get("sources", {}) if isinstance(report, dict) else {}
    items = sources.get(harness, []) if isinstance(sources, dict) else []
    return next((item for item in items
                 if item.get("scorer_id") == scorer_id), None)


def _corpus_summary(report):
    values = []
    versions = set()
    for harness in ("claude", "codex"):
        metric = _metric(report, harness, "repeated_call_rate")
        if not metric:
            continue
        versions.add(int(metric["scorer_version"]))
        values.append({
            "harness": harness,
            "count": int(metric.get("numerator") or 0),
            "eligible": int(metric.get("eligible_population") or 0),
            "rate": float(metric.get("value") or 0.0),
            "decision_support": bool(
                (metric.get("details") or {}).get("decision_support")),
        })
    if len(versions) > 1:
        raise ValueError("mixed repeated-call versions are not comparable")
    return values, (next(iter(versions)) if versions else None)


def _metric_rows(report):
    rows = []
    for harness in ("claude", "codex"):
        for metric in (report.get("sources") or {}).get(harness, []):
            details = metric.get("details") or {}
            rows.append({
                "harness": harness,
                "metric_id": str(metric.get("scorer_id") or "unknown"),
                "scorer_version": int(metric.get("scorer_version") or 0),
                "state": str(metric.get("label") or "unknown"),
                "numerator": int(metric.get("numerator") or 0),
                "eligible": int(metric.get("eligible_population") or 0),
                "population": int(metric.get("population") or 0),
                "value": metric.get("value"),
                "interval_low": metric.get("interval_low"),
                "interval_high": metric.get("interval_high"),
                "uncertainty_method": str(
                    metric.get("uncertainty_method") or ""),
                "abstained": bool(metric.get("abstained")),
                "decision_support": bool(details.get("decision_support")),
            })
    return rows


def _version_mapping(manifest, key):
    value = manifest.get(key) or {}
    if isinstance(value, dict):
        return {str(name): version for name, version in sorted(value.items())}
    if isinstance(value, list):
        return list(value)
    return value


def _git(repo_root, *args, check=True):
    result = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False)
    if check and result.returncode:
        raise ValueError("repository changeset is unreadable")
    return result


def _default_base(repo_root):
    candidate = _git(repo_root, "rev-parse", "--verify", "origin/main",
                     check=False)
    return "origin/main" if candidate.returncode == 0 else "HEAD"


def _change_scope(path):
    if path.endswith(("marketplace.json", "plugin.json")):
        return "release metadata"
    if path.startswith("docs/") or path.endswith("EVALUATION.md"):
        return "evidence contract"
    if "/tests/" in path:
        return "regression evidence"
    if "/profiles/" in path or "/rubrics/" in path:
        return "P1 / P4 taxonomy"
    if "instruction_manifest" in path:
        return "P3 provenance"
    if "usage_comparability" in path:
        return "P5 accounting"
    if "hook_lifecycle" in path or path.endswith("format-ctl"):
        return "P7 owned hooks"
    if "/ui/annotation/" in path or "review_dashboard" in path:
        return "review interface"
    if "/retro_eval/" in path:
        return "evaluation runtime"
    return "supporting change"


def _new_file_patch(repo_root, path):
    try:
        content = (repo_root / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "Binary or unreadable new file: %s\n" % path
    body = "\n".join(difflib.unified_diff(
        [], content.splitlines(), fromfile="/dev/null", tofile="b/" + path,
        lineterm=""))
    return "diff --git a/{0} b/{0}\nnew file mode 100644\n{1}\n".format(
        path, body)


def _repository_changeset(repo_root, base_ref=None):
    repo_root = Path(repo_root).resolve()
    base_ref = base_ref or _default_base(repo_root)
    base_revision = _git(repo_root, "rev-parse", base_ref).stdout.strip()
    numstat = {}
    for line in _git(repo_root, "diff", "--numstat", "--no-renames",
                     base_ref, "--").stdout.splitlines():
        additions, deletions, path = line.split("\t", 2)
        numstat[path] = (0 if additions == "-" else int(additions),
                         0 if deletions == "-" else int(deletions))
    statuses = {}
    for line in _git(repo_root, "diff", "--name-status", "--no-renames",
                     base_ref, "--").stdout.splitlines():
        status, path = line.split("\t", 1)
        statuses[path] = {"A": "added", "D": "deleted"}.get(
            status[:1], "modified")
    untracked = [path for path in _git(
        repo_root, "ls-files", "--others", "--exclude-standard").stdout
        .splitlines() if path]
    for path in untracked:
        content = repo_root / path
        try:
            additions = len(content.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            additions = 0
        numstat[path] = (additions, 0)
        statuses[path] = "added"
    files = []
    for path in sorted(statuses):
        if statuses[path] == "added" and path in untracked:
            patch = _new_file_patch(repo_root, path)
        else:
            patch = _git(repo_root, "diff", "--no-ext-diff", "--no-color",
                         "--unified=3", base_ref, "--", path).stdout
        if str(repo_root) in patch:
            raise ValueError("repository patch exposes its machine path")
        additions, deletions = numstat.get(path, (0, 0))
        files.append({
            "path": path, "status": statuses[path],
            "additions": additions, "deletions": deletions,
            "scope": _change_scope(path), "patch": patch,
        })
    return {
        "base_ref": base_ref, "base_revision": base_revision,
        "target": "working_tree", "file_count": len(files),
        "additions": sum(item["additions"] for item in files),
        "deletions": sum(item["deletions"] for item in files),
        "files": files,
    }


def _recommendations(p2, p3, p7):
    """Return the bounded policy decisions separately from measured evidence."""
    p3_population = int(p3.get("population") or 0)
    p3_resolved = int(p3.get("resolved") or 0)
    p3_unresolved = int(p3.get("unresolved") or 0)
    p3_passed = (p3_population > 0 and p3_resolved == p3_population
                 and p3_unresolved == 0)
    p7_precision = p7.get("capture_precision")
    p7_recall = p7.get("capture_recall")
    p7_unmatched = p7.get("unmatched_terminal_rate")
    p7_bytes = p7.get("added_normalized_byte_share")
    p7_passed = (p7_precision is not None and p7_precision >= 0.95
                 and p7_recall is not None and p7_recall >= 0.95
                 and p7_unmatched is not None and p7_unmatched <= 0.02
                 and p7_bytes is not None and p7_bytes <= 0.02)
    return [
        {
            "proposal": "P1", "decision": "hold",
            "decision_support": False,
            "recommended_action":
                "Use only for candidate generation; do not use it to judge "
                "wasteful duplication.",
            "evidence_basis":
                "The scorer has zero human truth labels and has not passed "
                "its preregistered held-out gates.",
            "revisit_when":
                "Held-out precision, recall, agreement, polling false-positive, "
                "and class-support gates all pass.",
        },
        {
            "proposal": "P2", "decision": "park",
            "decision_support": True,
            "recommended_action":
                "Keep explicit starts, ends, chains, and outcomes; do not report "
                "missed-trigger or opportunity rates.",
            "evidence_basis":
                "Only %d of %d starts have matched terminals; opportunity and "
                "missed-trigger rates are not observable."
                % (int(p2.get("matched_terminals") or 0),
                   int(p2.get("starts") or 0)),
            "revisit_when":
                "An owned source emits deterministic terminal and outcome events "
                "for every explicit start.",
        },
        {
            "proposal": "P3",
            "decision": "adopt" if p3_passed else "hold",
            "decision_support": p3_passed,
            "recommended_action":
                "Use private hashed instruction manifests for sessions inside a "
                "declared coverage window.",
            "evidence_basis":
                "%d/%d controlled sessions resolve to exactly one active "
                "manifest version; %d are unresolved."
                % (p3_resolved, p3_population, p3_unresolved),
            "revisit_when":
                "A declared-window session cannot resolve uniquely or privacy "
                "classification fails.",
        },
        {
            "proposal": "P4", "decision": "hold",
            "decision_support": False,
            "recommended_action":
                "Use only for candidate generation; do not use failure classes "
                "to justify changes.",
            "evidence_basis":
                "The taxonomy has zero human truth labels and has not passed its "
                "preregistered held-out gates.",
            "revisit_when":
                "Held-out precision, recall, agreement, unknown-rate, and "
                "class-support gates all pass.",
        },
        {
            "proposal": "P5", "decision": "adopt_gate_only",
            "decision_support": True,
            "recommended_action":
                "Enforce versioned accounting and paired-case comparability; do "
                "not run the 30-task experiment.",
            "evidence_basis":
                "The gate rejects incomplete, mixed-version, duplicate, and "
                "mismatched comparison rows.",
            "revisit_when":
                "A fixed task protocol, model/cache controls, run budget, and "
                "separate experiment authority exist.",
        },
        {
            "proposal": "P6", "decision": "leave_suppressed",
            "decision_support": True,
            "recommended_action": "Leave the suppressed proposal unchanged.",
            "evidence_basis":
                "This evaluation stage supplied no new evidence for promotion.",
            "revisit_when": "New evidence supports a separately authorized review.",
        },
        {
            "proposal": "P7",
            "decision": "adopt_scoped" if p7_passed else "hold",
            "decision_support": p7_passed,
            "recommended_action":
                "Instrument repository-owned deterministic wrappers only.",
            "evidence_basis":
                "Owned-hook capture passed precision, recall, unmatched-terminal, "
                "and byte-overhead targets.",
            "revisit_when":
                "Additional harness surfaces become owned and deterministically "
                "wrappable; never infer harness-wide opportunity coverage.",
        },
        {
            "proposal": "P8", "decision": "preserve",
            "decision_support": True,
            "recommended_action": "Preserve the implemented candidate-only rule.",
            "evidence_basis":
                "The current implementation prevents unvalidated candidates from "
                "driving decisions.",
            "revisit_when": "A replacement passes an explicit versioned review.",
        },
    ]


def build_review_dashboard(review_dir, *, repo_root=None, base_ref=None):
    """Return only aggregates and declared lifecycle states; never raw evidence."""
    review_dir = Path(review_dir).resolve()
    retro_home = review_dir.parent.parent
    packets = _packets(review_dir)
    report_path = _find_report(retro_home / "cross-harness-v4",
                               "deterministic-report-review-polling-v4.json")
    report = _read_json(report_path) if report_path else {}
    candidates, scorer_version = _corpus_summary(report)
    p2_metric = _metric(report, "claude", "skill_invocation_rate") or {}
    p2 = p2_metric.get("details") or {}
    p3_path = _find_report(retro_home / "current-base" / "p3-controlled",
                           "report.json")
    p3_report = _read_json(p3_path) if p3_path else {}
    p3 = ((p3_report.get("dataset_manifest") or {})
          .get("instruction_manifest_coverage") or {})
    p7_path = _find_report(retro_home / "current-base" / "p7-controlled",
                           "owned-hook-report.json")
    p7 = _read_json(p7_path) if p7_path else {}
    calibration = _calibration_summary(packets)
    assessment_counts = calibration["assessment_counts"]
    changes = []
    if assessment_counts["partly_accurate"]:
        changes.append({
            "signal": "%d partly accurate" % assessment_counts["partly_accurate"],
            "change": "Carry standing preferences into later diagnoses.",
            "reason": "The main interpretation fit, but omitted known brevity or recurrence expectations.",
        })
    if assessment_counts["wrong"]:
        changes.append({
            "signal": "%d wrong" % assessment_counts["wrong"],
            "change": "Treat cumulative polling as an efficiency question.",
            "reason": "An active job does not by itself justify repeated waits when completion notification is available.",
        })
    if assessment_counts["not_enough_context"]:
        changes.append({
            "signal": "%d insufficient" % assessment_counts["not_enough_context"],
            "change": "Abstain when repeat history is missing.",
            "reason": "Two adjacent waits cannot establish cumulative cost without duration, repeat count, or notification context.",
        })
    dataset_manifest = report.get("dataset_manifest") or {}
    repository_root = (Path(repo_root).resolve() if repo_root else
                       Path(__file__).resolve().parents[3])
    return {
        "schema_version": 4,
        "changeset": _repository_changeset(repository_root, base_ref),
        "recommendations": _recommendations(p2, p3, p7),
        "run": {
            "dataset_id": str(dataset_manifest.get("dataset_id") or "unknown"),
            "trace_population": int(dataset_manifest.get("population") or 0),
            "trace_schema_versions": _version_mapping(
                dataset_manifest, "trace_schema_versions"),
            "adapter_versions": _version_mapping(
                dataset_manifest, "adapter_versions"),
            "scorer_versions": _version_mapping(
                dataset_manifest, "scorer_versions"),
            "rubric_versions": _version_mapping(
                dataset_manifest, "rubric_versions"),
        },
        "coverage": {
            str(harness): {str(state): int(count)
                           for state, count in sorted(values.items())}
            for harness, values in sorted(
                (report.get("coverage_summary") or {}).items())
        },
        "metrics": _metric_rows(report),
        "calibration": calibration,
        "changes": changes,
        "corpus": {"traces": int((report.get("dataset_manifest") or {})
                                  .get("population") or 0),
                   "repeated_call_scorer_version": scorer_version},
        "corpus_candidates": candidates,
        "taxonomies": {
            "P1": {**_taxonomy_summary(packets, "duplicate_work"),
                   "thresholds": {"precision": 0.90, "recall": 0.75,
                                  "polling_false_positive_rate": 0.05}},
            "P4": {**_taxonomy_summary(packets, "tool_failure_kind"),
                   "thresholds": {"precision": 0.90, "recall": 0.80,
                                  "agreement": 0.80, "unknown_rate": 0.10}},
        },
        "proposals": {
            "P1": {"state": "calibrating", "decision_support": False},
            "P2": {"state": "owned_events_measured",
                   "scorer_version": int(p2_metric.get("scorer_version") or 0),
                   "starts": int(p2.get("starts") or 0),
                   "ends": int(p2.get("ends") or 0),
                   "matched_terminals": int(p2.get("matched_terminals") or 0),
                   "unmatched_starts": int(p2.get("unmatched_starts") or 0),
                   "explicit_chain_steps": int(
                       p2.get("explicit_chain_steps") or 0),
                   "completion_rate": p2.get("lifecycle_completion_rate"),
                   "unmatched_start_rate": p2.get("unmatched_start_rate"),
                   "orphan_terminal_rate": p2.get("orphan_terminal_rate"),
                   "missed_trigger_rate": p2.get(
                       "missed_trigger_rate", "not_observable"),
                   "opportunity_rate": p2.get(
                       "opportunity_rate", "not_observable")},
            "P3": {"state": "controlled_pass",
                   "population": int(p3.get("population") or 0),
                   "resolved": int(p3.get("resolved") or 0),
                   "unresolved": int(p3.get("unresolved") or 0)},
            "P4": {"state": "calibrating", "decision_support": False},
            "P5": {"state": "comparability_gate_implemented",
                   "paired_experiment": "parked"},
            "P6": {"state": "suppressed"},
            "P7": {"state": "controlled_pass",
                   "precision": p7.get("capture_precision"),
                   "recall": p7.get("capture_recall"),
                   "unmatched_terminal_rate": p7.get(
                       "unmatched_terminal_rate"),
                   "added_byte_share": p7.get("added_normalized_byte_share"),
                   "coverage": p7.get("coverage", "not_observable"),
                   "harness_wide_coverage": p7.get(
                       "harness_wide_opportunity_coverage", "not_observable")},
            "P8": {"state": "implemented_preserved"},
        },
    }
