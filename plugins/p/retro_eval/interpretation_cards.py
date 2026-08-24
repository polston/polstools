"""Versioned plain-language cards for mixed understanding review."""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
from collections import Counter
from pathlib import Path

from .annotation import FIELDS, _external, _packet_fingerprint, _salt
from .catalog import (load_annotation_protocol_catalogue,
                      load_rubric_catalogue)


INTERPRETATION_FIELDS = FIELDS[:-2] + (
    "review_kind", "situation_summary", "interpretation", "rationale",
    "expected_action", "assessment") + FIELDS[-2:]
REVIEW_KINDS = frozenset({"user_understanding", "agent_judgment"})
PLAIN_FIELDS = ("situation_summary", "interpretation", "rationale",
                "expected_action")
RAW_MARKERS = ("const r=", "const r =", "await tools.", "exit code:", "[{\"text\"",
               "structural observation:", "purpose=")


def _validate(card):
    kind = str(card.get("review_kind") or "")
    if kind not in REVIEW_KINDS:
        raise ValueError("interpretation card has unsupported review kind")
    if not str(card.get("stable_key") or ""):
        raise ValueError("interpretation card requires a stable key")
    if not str(card.get("context") or "").strip() \
            or not str(card.get("user_turn") or "").strip():
        raise ValueError("interpretation card requires source evidence")
    for field in PLAIN_FIELDS:
        value = str(card.get(field) or "").strip()
        if not value or len(value) > 700:
            raise ValueError("interpretation card requires bounded plain-language fields")
        lowered = value.lower()
        if any(marker in lowered for marker in RAW_MARKERS):
            raise ValueError("interpretation card plain-language field contains raw telemetry")


def write_interpretation_review(cards, output: Path, manifest_path: Path, *,
                                dataset_id="mixed-interpretation-calibration-v1",
                                split="calibration", review_round=1):
    """Write authored cards outside Git with immutable evidence and optional ratings."""
    output = Path(output)
    manifest_path = Path(manifest_path)
    _external(output)
    _external(manifest_path)
    if split != "calibration" or review_round < 1:
        raise ValueError("interpretation review currently supports calibration rounds")
    cards = list(cards)
    if not cards:
        raise ValueError("interpretation review requires cards")
    for card in cards:
        _validate(card)
    stable_keys = [str(card["stable_key"]) for card in cards]
    if len(stable_keys) != len(set(stable_keys)):
        raise ValueError("interpretation review has duplicate stable keys")

    plugin_root = Path(__file__).resolve().parents[1]
    rubrics = load_rubric_catalogue(plugin_root / "rubrics" / "rubrics.json")
    rubric = next(item for item in rubrics.rubrics
                  if item.id == "interpretation_grounding" and item.version == 1)
    protocol = load_annotation_protocol_catalogue(
        plugin_root / "rubrics" / "annotation-protocols.json", rubrics).get(
            "interpretation-grounding", 1)
    salt = _salt(output.parent)
    rows = []
    for card in cards:
        key = str(card["stable_key"])
        case_id = hmac.new(salt, (dataset_id + "|" + key).encode("utf-8"),
                           hashlib.sha256).hexdigest()[:24]
        row = {
            "case_id": case_id,
            "source": str(card.get("source") or "unknown"),
            "split": split,
            "context_chars": len(str(card["context"])),
            "user_turn_chars": len(str(card["user_turn"])),
            "context": str(card["context"]),
            "user_turn": str(card["user_turn"]),
            "review_kind": str(card["review_kind"]),
            "situation_summary": str(card["situation_summary"]).strip(),
            "interpretation": str(card["interpretation"]).strip(),
            "rationale": str(card["rationale"]).strip(),
            "expected_action": str(card["expected_action"]).strip(),
            "assessment": "", "human_label": "", "notes": "",
        }
        rows.append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INTERPRETATION_FIELDS)
        writer.writeheader(); writer.writerows(rows)
    metadata = {
        "schema_version": 1,
        "annotation_packet_version": 1,
        "dataset_id": dataset_id,
        "rubric_id": rubric.id,
        "rubric_version": rubric.version,
        "split": split,
        "review_round": review_round,
        "selection": "explicit authored calibration cards",
        "source_counts": dict(sorted(Counter(row["source"] for row in rows).items())),
        "review_kind_counts": dict(sorted(Counter(
            row["review_kind"] for row in rows).items())),
        "card_sha256": [hashlib.sha256(
            (dataset_id + "|" + key).encode("utf-8")).hexdigest()
            for key in stable_keys],
        "sample_sha256": _packet_fingerprint(output),
        "annotation_protocol_id": protocol.id,
        "annotation_protocol_version": protocol.version,
        "annotation_protocol_sha256": protocol.sha256,
        "review_quality": {
            "status": "passed", "case_count": len(rows),
            "plain_language_contract": "validated before write",
            "decision_support": False,
        },
    }
    manifest_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return metadata
