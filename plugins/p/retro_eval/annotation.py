"""Stratified external annotation packets and content-free label import."""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
from pathlib import Path

from .labels import LabelRecord, LabelStore


FIELDS = ("case_id", "source", "split", "context_chars", "user_turn_chars",
          "context", "user_turn", "human_label", "notes")
MUTABLE_FIELDS = frozenset({"human_label", "notes", "assessment"})


def _protocol_value(protocol, field):
    if isinstance(protocol, dict):
        return protocol[field]
    return getattr(protocol, field)


def render_annotation_guide(protocol):
    """Render human instructions from the same versioned protocol judges use."""
    lines = [
        "# Held-out annotation guide",
        "",
        "Protocol: `%s v%s`" % (_protocol_value(protocol, "id"),
                                _protocol_value(protocol, "version")),
        "Protocol SHA-256: `%s`" % _protocol_value(protocol, "sha256"),
        "",
        _protocol_value(protocol, "human_instruction"),
        "Enter exactly one label in `human_label`; use `notes` only when useful.",
        "",
        "## Decision order",
        "",
    ]
    prompts = _protocol_value(protocol, "label_prompts")
    for index, label in enumerate(_protocol_value(protocol, "decision_order"), 1):
        lines.append("%d. **%s** (`%s`) — %s" % (
            index, prompts[label]["action"], label, prompts[label]["detail"]))
    lines.extend(["", "## Tie-breaking rules", ""])
    for index, rule in enumerate(_protocol_value(protocol, "tie_breaks"), 1):
        lines.append("%d. %s" % (index, rule))
    return "\n".join(lines) + "\n"


def _external(path: Path):
    resolved = path.resolve()
    if any((parent / ".git").exists() for parent in (resolved, *resolved.parents)):
        raise ValueError("annotation artifacts must remain outside repositories")


def _salt(directory: Path):
    path = directory / ".annotation-salt"
    try:
        return path.read_bytes()
    except OSError:
        value = os.urandom(32)
        directory.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
        return value


def _digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _packet_fingerprint(path: Path):
    """Fingerprint sample identity while allowing human label and note edits."""
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            immutable = tuple(field for field in (reader.fieldnames or ())
                              if field not in MUTABLE_FIELDS)
            rows = [{field: str(row.get(field) or "") for field in immutable}
                    for row in reader]
    except OSError as exc:
        raise ValueError("annotation packet is unreadable") from exc
    canonical = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_packet_manifest(source: Path, manifest_path: Path, *, rubric_id,
                          rubric_version):
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid annotation manifest") from exc
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported annotation manifest schema")
    if not str(manifest.get("dataset_id") or ""):
        raise ValueError("annotation manifest requires dataset id")
    if (manifest.get("rubric_id") != rubric_id
            or int(manifest.get("rubric_version") or 0) != rubric_version):
        raise ValueError("annotation manifest rubric mismatch")
    if manifest.get("sample_sha256") != _packet_fingerprint(source):
        raise ValueError("annotation packet fingerprint mismatch")
    return manifest


def sample_annotations(extract_paths, output: Path, manifest_path: Path, *, per_source=20,
                       dataset_id="turn-friction-heldout-v1",
                       rubric_id="turn_friction_legacy", rubric_version=1,
                       split="test", annotation_protocol=None):
    _external(output)
    _external(manifest_path)
    if per_source < 1:
        raise ValueError("per_source must be positive")
    if split not in {"calibration", "test"}:
        raise ValueError("annotation split must be calibration or test")
    salt = _salt(output.parent)
    pools = {}
    input_fingerprints = {}
    for extract_path in extract_paths:
        try:
            payload = json.loads(extract_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid working-review extract") from exc
        source = str(payload.get("source_system") or extract_path.stem)
        input_fingerprints[source] = _digest(extract_path)
        candidates = pools.setdefault(source, [])
        for session in payload.get("sessions") or ():
            previous_assistant = ""
            previous_assistant_chars = 0
            for message in session.get("messages") or ():
                role = message.get("role")
                excerpt = str(message.get("excerpt") or "")
                if role == "assistant":
                    previous_assistant = excerpt
                    previous_assistant_chars = int(message.get("chars") or len(excerpt))
                elif role == "user" and excerpt:
                    raw_id = "%s|%s|%s" % (
                        source, session.get("session_id"), message.get("line"))
                    case_id = hmac.new(salt, raw_id.encode(), hashlib.sha256).hexdigest()[:24]
                    rank = hmac.new(salt, ("rank|" + raw_id).encode(), hashlib.sha256).hexdigest()
                    candidates.append((rank, {
                        "case_id": case_id, "source": source, "split": split,
                        "context_chars": previous_assistant_chars,
                        "user_turn_chars": int(message.get("chars") or len(excerpt)),
                        "context": previous_assistant, "user_turn": excerpt,
                        "human_label": "", "notes": "",
                    }))
    selected = []
    source_counts = {}
    for source, candidates in sorted(pools.items()):
        rows = [row for _, row in sorted(candidates)[:per_source]]
        selected.extend(rows)
        source_counts[source] = len(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(selected)
    manifest = {
        "schema_version": 1, "annotation_packet_version": 2,
        "dataset_id": dataset_id,
        "rubric_id": rubric_id, "rubric_version": rubric_version,
        "split": split, "selection": "keyed deterministic rank by source",
        "per_source": per_source, "source_counts": source_counts,
        "input_fingerprints": dict(sorted(input_fingerprints.items())),
        "sample_sha256": _packet_fingerprint(output),
    }
    if annotation_protocol is not None:
        manifest.update({
            "annotation_protocol_id": str(_protocol_value(annotation_protocol, "id")),
            "annotation_protocol_version": int(_protocol_value(annotation_protocol, "version")),
            "annotation_protocol_sha256": str(_protocol_value(annotation_protocol, "sha256")),
        })
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    return manifest


def import_annotations(source: Path, target: Path, *, manifest_path: Path, rubric_id,
                       rubric_version, allowed_labels,
                       annotator_id="human-rater-v1"):
    manifest = _load_packet_manifest(
        source, manifest_path, rubric_id=rubric_id, rubric_version=rubric_version)
    allowed = frozenset(allowed_labels)
    labels = []
    seen = set()
    with source.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            label = str(row.get("human_label") or "").strip()
            if not label:
                continue
            if label not in allowed:
                raise ValueError("annotation contains an unsupported label")
            case_id = str(row["case_id"])
            if case_id in seen:
                raise ValueError("annotation packet contains duplicate case id")
            seen.add(case_id)
            if str(row.get("split") or "") != manifest["split"]:
                raise ValueError("annotation row split differs from manifest")
            labels.append(LabelRecord(
                schema_version=1, dataset_id=str(manifest["dataset_id"]),
                case_id=case_id, rubric_id=rubric_id, rubric_version=rubric_version,
                split=str(row["split"]), label=label, annotator_kind="human",
                annotator_id=annotator_id, evidence_refs=("annotation:" + case_id,),
                created_at="human-import",
            ))
    LabelStore(target).write(labels)
    with source.open("r", encoding="utf-8") as handle:
        available_rows = sum(1 for _ in handle) - 1
    return {"imported": len(labels), "available_rows": available_rows}


def predict_annotations(source: Path, target: Path, *, manifest_path: Path,
                        prediction_manifest_path: Path, allowed_labels, predictor,
                        predictor_id, created_commit, predictor_config):
    """Freeze truth-blind predictions over a fingerprinted annotation packet."""
    _external(prediction_manifest_path)
    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rubric_id = str(raw_manifest["rubric_id"])
        rubric_version = int(raw_manifest["rubric_version"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid annotation manifest") from exc
    manifest = _load_packet_manifest(
        source, manifest_path, rubric_id=rubric_id, rubric_version=rubric_version)
    commit = str(created_commit).lower()
    if len(commit) not in {40, 64} or any(character not in "0123456789abcdef"
                                          for character in commit):
        raise ValueError("prediction artifact requires a full creation commit")
    config_sha256 = hashlib.sha256(str(predictor_config).encode("utf-8")).hexdigest()
    allowed = frozenset(allowed_labels)
    records = []
    seen = set()
    with source.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            case_id = str(row.get("case_id") or "")
            if not case_id or case_id in seen:
                raise ValueError("prediction packet requires unique case ids")
            seen.add(case_id)
            label = str(predictor(row) or "")
            if label not in allowed:
                raise ValueError("predictor returned an unsupported label")
            if str(row.get("split") or "") != manifest["split"]:
                raise ValueError("annotation row split differs from manifest")
            records.append(LabelRecord(
                schema_version=1, dataset_id=str(manifest["dataset_id"]),
                case_id=case_id, rubric_id=rubric_id,
                rubric_version=rubric_version, split=str(manifest["split"]),
                label=label, annotator_kind="deterministic",
                annotator_id=predictor_id,
                evidence_refs=("annotation:" + case_id,),
                created_at="frozen-prediction",
            ))
    LabelStore(target).write(records)
    prediction_manifest = {
        "schema_version": 1, "dataset_id": manifest["dataset_id"],
        "rubric_id": rubric_id, "rubric_version": rubric_version,
        "split": manifest["split"], "annotator_kind": "deterministic",
        "predictor_id": predictor_id, "population": len(records),
        "created_commit": commit,
        "predictor_config_sha256": config_sha256,
        "sample_sha256": manifest["sample_sha256"],
        "predictions_sha256": _digest(target),
        "human_truth_used": False,
    }
    protocol_fields = ("annotation_protocol_id", "annotation_protocol_version",
                       "annotation_protocol_sha256")
    if any(field in manifest for field in protocol_fields):
        if not all(manifest.get(field) for field in protocol_fields):
            raise ValueError("annotation protocol binding is incomplete")
        prediction_manifest.update({field: manifest[field] for field in protocol_fields})
    prediction_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    prediction_manifest_path.write_text(
        json.dumps(prediction_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return {"predicted": len(records), "predictor_id": predictor_id,
            "predictions_sha256": prediction_manifest["predictions_sha256"]}


def validate_prediction_artifact(predictions_path: Path, manifest_path: Path, *,
                                 sample_manifest_path: Path):
    """Resolve a prediction manifest to its exact packet and content-free records."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sample = json.loads(sample_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid prediction or sample manifest") from exc
    if manifest.get("schema_version") != 1 or sample.get("schema_version") != 1:
        raise ValueError("unsupported prediction manifest schema")
    if manifest.get("predictions_sha256") != _digest(predictions_path):
        raise ValueError("prediction artifact fingerprint mismatch")
    if manifest.get("human_truth_used") is not False:
        raise ValueError("prediction artifact is not truth-blind")
    annotator_kind = manifest.get("annotator_kind")
    if annotator_kind == "deterministic":
        if not manifest.get("created_commit") or not manifest.get("predictor_config_sha256"):
            raise ValueError("deterministic prediction provenance is incomplete")
    elif annotator_kind == "model_judge":
        if not manifest.get("provider_id") or not manifest.get("prompt_hash") \
                or not manifest.get("config_hash"):
            raise ValueError("model-judge prediction provenance is incomplete")
    else:
        raise ValueError("prediction artifact annotator kind is unsupported")
    if manifest.get("sample_sha256") != sample.get("sample_sha256"):
        raise ValueError("prediction artifact sample fingerprint mismatch")
    identity = ("dataset_id", "rubric_id", "rubric_version", "split")
    protocol_fields = ("annotation_protocol_id", "annotation_protocol_version",
                       "annotation_protocol_sha256")
    if any(field in sample for field in protocol_fields):
        if not all(sample.get(field) for field in protocol_fields):
            raise ValueError("sample annotation protocol binding is incomplete")
        if any(manifest.get(field) != sample.get(field) for field in protocol_fields):
            raise ValueError("prediction artifact annotation protocol mismatch")
    if any(manifest.get(field) != sample.get(field) for field in identity):
        raise ValueError("prediction artifact metadata differs from sample")
    records = LabelStore(predictions_path).read()
    if int(manifest.get("population") or -1) != len(records):
        raise ValueError("prediction artifact population mismatch")
    for record in records:
        if any(getattr(record, field) != manifest.get(field) for field in identity):
            raise ValueError("prediction record metadata differs from manifest")
        if record.annotator_kind != manifest.get("annotator_kind"):
            raise ValueError("prediction record annotator kind differs from manifest")
        expected_annotator = manifest.get("predictor_id") or manifest.get("annotator_id")
        if expected_annotator and record.annotator_id != expected_annotator:
            raise ValueError("prediction record annotator id differs from manifest")
    return manifest
