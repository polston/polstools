"""CLI for adaptive taxonomy annotation packets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .cli import command
from .annotation import _load_packet
from .catalog import load_rubric_catalogue
from .taxonomy_packets import (assess_packet_support, assess_packet_criteria,
                               write_taxonomy_review_packets)
from .private_evidence import collect_private_tool_evidence


@command
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
    assess.add_argument("--criteria", action="store_true",
                        help="assess frozen proposals against labels without authorizing promotion")
    assess.add_argument("--prior-packet", type=Path, nargs=2, action="append", default=[],
                        metavar=("CSV", "MANIFEST"))
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
        result["empty_packets"] = sorted(
            Path(item["source"]).name for item in result["packets"]
            if not item["population"])
        print(json.dumps(result, sort_keys=True))
        # An empty packet is written so its fingerprint stays immutable, but
        # it holds nothing to review: flag it instead of reporting success.
        return 1 if result["empty_packets"] else 0

    plugin_root = Path(__file__).resolve().parents[1]
    catalogue = load_rubric_catalogue(
        plugin_root / "rubrics" / "rubrics.json")
    try:
        raw_manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        rubric = next(item for item in catalogue.rubrics
                      if item.id == raw_manifest["rubric_id"]
                      and item.version == int(raw_manifest["rubric_version"]))
    except (OSError, KeyError, TypeError, ValueError, StopIteration,
            json.JSONDecodeError) as exc:
        raise ValueError("invalid adaptive taxonomy packet") from exc
    manifest, rows = _load_packet(
        args.source, args.manifest, rubric_id=rubric.id,
        rubric_version=rubric.version)
    packets = [(manifest, rows)]
    for source, prior_manifest in args.prior_packet:
        packets.append(_load_packet(source, prior_manifest,
            rubric_id=rubric.id, rubric_version=rubric.version))
    result = (assess_packet_criteria(packets, rubric) if args.criteria
              else assess_packet_support(packets, rubric))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in ("ready", "criteria_met") else 1


if __name__ == "__main__":
    raise SystemExit(main())
