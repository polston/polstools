"""CLI for adaptive taxonomy annotation packets."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .annotation import _load_packet_manifest
from .catalog import load_rubric_catalogue
from .taxonomy_packets import (AdaptiveSamplingPlan, assess_label_support,
                               write_taxonomy_review_packets)
from .private_evidence import collect_private_tool_evidence


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    sample = commands.add_parser("sample")
    sample.add_argument("--traces", type=Path, required=True)
    sample.add_argument("--output-dir", type=Path, required=True)
    sample.add_argument("--round", type=int, default=1)
    sample.add_argument("--prior-manifest", type=Path, action="append", default=[])
    sample.add_argument("--duplicate-calibration", type=int)
    sample.add_argument("--duplicate-heldout", type=int)
    sample.add_argument("--failure-calibration", type=int)
    sample.add_argument("--failure-heldout", type=int)
    sample.add_argument("--source-root", action="append", default=[],
                        metavar="SOURCE=PATH")
    sample.add_argument("--id-salt", type=Path)
    assess = commands.add_parser("assess")
    assess.add_argument("--source", type=Path, required=True)
    assess.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "sample":
        source_roots = {}
        for value in args.source_root:
            try:
                source, path = value.split("=", 1)
            except ValueError as exc:
                raise ValueError("source roots must use source=path") from exc
            if not source or not path or source in source_roots:
                raise ValueError("source roots require unique non-empty names")
            source_roots[source] = Path(path)
        if bool(source_roots) != bool(args.id_salt):
            raise ValueError("private evidence requires source roots and id salt")
        private_evidence = {}
        if source_roots:
            try:
                id_salt = args.id_salt.read_bytes()
            except OSError as exc:
                raise ValueError("id salt is unreadable") from exc
            private_evidence = collect_private_tool_evidence(source_roots, id_salt)
        overrides = {
            "duplicate_work": {
                key: value for key, value in {
                    "calibration": args.duplicate_calibration,
                    "test": args.duplicate_heldout,
                }.items() if value is not None},
            "tool_failure_kind": {
                key: value for key, value in {
                    "calibration": args.failure_calibration,
                    "test": args.failure_heldout,
                }.items() if value is not None},
        }
        result = write_taxonomy_review_packets(
            args.traces, args.output_dir, size_overrides=overrides,
            round_number=args.round, prior_manifests=args.prior_manifest,
            private_evidence=private_evidence)
        print(json.dumps(result, sort_keys=True))
        return 0

    plugin_root = Path(__file__).resolve().parents[1]
    catalogue = load_rubric_catalogue(
        plugin_root / "rubrics" / "rubrics.json")
    try:
        raw_manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        rubric = next(item for item in catalogue.rubrics
                      if item.id == raw_manifest["rubric_id"]
                      and item.version == int(raw_manifest["rubric_version"]))
        round_number = int(raw_manifest["adaptive_sampling"]["round"])
    except (OSError, KeyError, TypeError, ValueError, StopIteration,
            json.JSONDecodeError) as exc:
        raise ValueError("invalid adaptive taxonomy packet") from exc
    _load_packet_manifest(
        args.source, args.manifest, rubric_id=rubric.id,
        rubric_version=rubric.version)
    with args.source.open("r", encoding="utf-8", newline="") as handle:
        labels = [str(row.get("human_label") or "").strip()
                  for row in csv.DictReader(handle)
                  if str(row.get("human_label") or "").strip()]
    result = assess_label_support(
        labels, AdaptiveSamplingPlan.from_rubric(rubric),
        round_number=round_number)
    result["labelled"] = len(labels)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
