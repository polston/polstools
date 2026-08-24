"""Discover and serve the next immutable taxonomy review packet."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from .annotation import _load_packet_manifest
from .annotation_ui import AnnotationWorkspace, serve_annotation_ui


_RUBRIC_ORDER = {"interpretation_grounding": -1,
                 "duplicate_work": 0, "tool_failure_kind": 1}
_ASSESSMENTS = {"", "correct", "incorrect", "unsure", "accurate",
                "partly_accurate", "wrong", "not_enough_context"}
DEFAULT_REVIEW_PORT = 8123


def _review_directory(value=None) -> Path:
    if value:
        directory = Path(value)
    elif os.environ.get("RETRO_HOME"):
        directory = (Path(os.environ["RETRO_HOME"])
                     / "annotations" / "proposal-taxonomies")
    else:
        candidates = sorted(
            path.parent for path in (Path.home() / ".retro").glob(
                "*/annotations/proposal-taxonomies/01-review-instructions.md"))
        if len(candidates) != 1:
            raise ValueError(
                "set RETRO_HOME or pass --review-dir; found %d review sets"
                % len(candidates))
        directory = candidates[0]
    if not directory.is_dir():
        raise ValueError("taxonomy review directory is unavailable")
    return directory.resolve()


def _packet_states(review_dir: Path, split: str):
    packets = []
    for manifest_path in sorted(review_dir.glob("*-manifest.json")):
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            if str(raw["split"]) != split:
                continue
            rubric_id = str(raw["rubric_id"])
            rubric_version = int(raw["rubric_version"])
            round_number = int(raw.get("review_round") or
                               raw.get("adaptive_sampling", {})["round"])
        except (OSError, KeyError, TypeError, ValueError,
                json.JSONDecodeError) as exc:
            raise ValueError("invalid review packet manifest") from exc
        source = manifest_path.with_name(
            manifest_path.name.removesuffix("-manifest.json") + ".csv")
        _load_packet_manifest(
            source, manifest_path, rubric_id=rubric_id,
            rubric_version=rubric_version)
        try:
            with source.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            raise ValueError("taxonomy review packet is unreadable") from exc
        if not rows or "assessment" not in rows[0]:
            raise ValueError("taxonomy review packet lacks assessments")
        assessments = [str(row.get("assessment") or "").strip() for row in rows]
        invalid = sorted(set(assessments) - _ASSESSMENTS)
        if invalid:
            raise ValueError("taxonomy review packet has unsupported assessment")
        completed = sum(bool(value) for value in assessments)
        packets.append({
            "rubric_id": rubric_id,
            "rubric_version": rubric_version,
            "round": round_number,
            "split": split,
            "source": str(source),
            "source_name": source.name,
            "manifest": str(manifest_path),
            "manifest_name": manifest_path.name,
            "progress": {"completed": completed, "total": len(rows)},
            "remaining": len(rows) - completed,
        })
    packets.sort(key=lambda item: (
        item["round"], _RUBRIC_ORDER.get(item["rubric_id"], 99),
        item["source_name"]))
    return packets


def review_status(review_dir, *, phase="calibration"):
    """Return resumable state for one explicit review phase."""
    if phase not in {"calibration", "heldout"}:
        raise ValueError("review phase must be calibration or heldout")
    directory = _review_directory(review_dir)
    split = "calibration" if phase == "calibration" else "test"
    packets = _packet_states(directory, split)
    if not packets:
        raise ValueError("no %s taxonomy packets found" % phase)
    next_packet = next((item for item in packets if item["remaining"]), None)
    return {
        "schema_version": 1,
        "phase": phase,
        "packet_count": len(packets),
        "phase_completed": next_packet is None,
        "remaining_cases": next_packet["remaining"] if next_packet else 0,
        "next_packet": next_packet,
        "packets": packets,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "serve-next"):
        command = commands.add_parser(name)
        command.add_argument("--review-dir", type=Path)
        command.add_argument("--phase", choices=("calibration", "heldout"),
                             default="calibration")
        if name == "serve-next":
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=DEFAULT_REVIEW_PORT)
            command.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    status = review_status(args.review_dir, phase=args.phase)
    if args.command == "status" or status["next_packet"] is None:
        print(json.dumps(status, sort_keys=True))
        return 0
    packet = status["next_packet"]
    plugin_root = Path(__file__).resolve().parents[1]
    workspace = AnnotationWorkspace(
        source=Path(packet["source"]),
        manifest_path=Path(packet["manifest"]),
        rubrics_path=plugin_root / "rubrics" / "rubrics.json",
        protocols_path=plugin_root / "rubrics" / "annotation-protocols.json")
    serve_annotation_ui(
        workspace, host=args.host, port=args.port,
        open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
