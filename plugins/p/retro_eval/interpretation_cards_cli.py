"""CLI for external authored interpretation review cards."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .interpretation_cards import write_interpretation_review


def _hydrate(cards, authored_path):
    cache = {}
    hydrated = []
    for raw in cards:
        card = dict(raw)
        if card.get("context") and card.get("user_turn"):
            hydrated.append(card)
            continue
        source_value = str(card.pop("evidence_source", ""))
        evidence_case_id = str(card.pop("evidence_case_id", ""))
        if not source_value or not evidence_case_id:
            raise ValueError("authored card requires evidence or a source reference")
        source_path = Path(source_value)
        if not source_path.is_absolute():
            source_path = authored_path.parent / source_path
        source_path = source_path.resolve()
        if source_path not in cache:
            try:
                with source_path.open("r", encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))
            except OSError as exc:
                raise ValueError("authored card evidence source is unreadable") from exc
            cache[source_path] = rows
        matches = [row for row in cache[source_path]
                   if str(row.get("case_id") or "") == evidence_case_id]
        if len(matches) != 1:
            raise ValueError("authored card evidence case is not unique")
        evidence = matches[0]
        card.update({
            "source": str(evidence.get("source") or "unknown"),
            "context": str(evidence.get("context") or ""),
            "user_turn": str(evidence.get("user_turn") or ""),
        })
        hydrated.append(card)
    return hydrated


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--cards", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--round", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.cards.read_text(encoding="utf-8"))
        cards = payload["cards"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("invalid authored interpretation cards") from exc
    if not isinstance(cards, list):
        raise ValueError("authored interpretation cards must be a list")
    cards = _hydrate(cards, args.cards)
    output = args.output_dir / (
        "mixed-interpretation-calibration-round%d.csv" % args.round)
    manifest = args.output_dir / (
        "mixed-interpretation-calibration-round%d-manifest.json" % args.round)
    metadata = write_interpretation_review(
        cards, output, manifest, review_round=args.round,
        dataset_id="mixed-interpretation-calibration-v1-round%d" % args.round)
    print(json.dumps({
        "source": str(output.resolve()), "manifest": str(manifest.resolve()),
        "cards": metadata["review_quality"]["case_count"],
        "decision_support": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
