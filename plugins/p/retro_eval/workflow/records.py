"""Strict, immutable JSON records and artifact-bound delivery semantics."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .errors import UnsupportedVersion, WorkflowError

KINDS = frozenset({"case", "attempt", "observation", "capability", "outcome",
                   "assessment", "hypothesis", "practice", "pilot", "exposure",
                   "analysis", "decision", "selection", "snapshot", "plan",
                   "investigation", "investigation_event"})
AVAILABILITY = frozenset({"observed", "not_observed", "not_supported",
                         "not_applicable", "invalid", "pending"})
CONTENT_CLASSES = frozenset({"public_synthetic", "private_metadata",
                            "private_redacted", "private_raw"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise WorkflowError("timestamp must be a timezone-aware string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkflowError("invalid timestamp") from exc
    if parsed.utcoffset() is None:
        raise WorkflowError("timestamp timezone is required")
    return parsed


def integer(value: object, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise WorkflowError("%s must be an integer >= %s" % (name, minimum))
    return value


def text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError("%s must be a nonempty string" % name)
    return value


def _json_value(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise WorkflowError("JSON object keys must be strings")
            _json_value(item)
    elif isinstance(value, list):
        for item in value:
            _json_value(item)
    elif type(value) is float:
        if not math.isfinite(value):
            raise WorkflowError("nonfinite JSON number")
    elif value is not None and type(value) not in (str, int, bool):
        raise WorkflowError("unsupported JSON value")


def canonical(value) -> bytes:
    _json_value(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def strict_loads(value: str | bytes):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise WorkflowError("duplicate JSON key")
            result[key] = item
        return result

    def bad_constant(value):
        raise WorkflowError("nonfinite JSON number")

    try:
        result = json.loads(value, object_pairs_hook=pairs,
                            parse_constant=bad_constant)
        _json_value(result)
        return result
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise WorkflowError("invalid JSON") from exc


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class RecordRef:
    study_scope: str
    kind: str
    key: str
    revision: int

    def __post_init__(self):
        for name in ("study_scope", "kind", "key"):
            text(getattr(self, name), name)
        integer(self.revision, "revision", 1)

    def to_dict(self):
        return {name: getattr(self, name)
                for name in ("study_scope", "kind", "key", "revision")}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {
                "study_scope", "kind", "key", "revision"}:
            raise WorkflowError("invalid record reference")
        return cls(**value)


def _criteria(payload):
    contract = payload.get("contract")
    if not isinstance(contract, dict):
        raise WorkflowError("case contract is required")
    integer(contract.get("version"), "contract version", 1)
    criteria = contract.get("criteria")
    if not isinstance(criteria, list):
        raise WorkflowError("criteria must be a list")
    keys = set()
    for item in criteria:
        if not isinstance(item, dict):
            raise WorkflowError("invalid criterion")
        key = text(item.get("key"), "criterion key")
        if key in keys:
            raise WorkflowError("duplicate criterion key")
        keys.add(key)
        text(item.get("description"), "criterion description")
        text(item.get("verification_method"), "verification method")
        if type(item.get("required")) is not bool:
            raise WorkflowError("criterion required must be boolean")
    return criteria


def _validate_payload(kind, payload):
    if not isinstance(payload, dict):
        raise WorkflowError("record payload must be an object")
    if kind in {'investigation', 'investigation_event'}:
        from .investigation_records import validate
        validate(kind, payload)
        return
    # These rules apply to declared quantitative fields across record kinds.
    for key, value in payload.items():
        if key in {"count", "duration_ms", "input_tokens", "output_tokens"}:
            integer(value, key)
        if key == "sampling_weight":
            if type(value) not in (int, float) or value <= 0:
                raise WorkflowError("sampling weight must be positive and finite")
    if kind == "case":
        text(payload.get("intent"), "case intent")
        text(payload.get("task_family"), "task family")
        text(payload.get("split_group"), "split group")
        if payload.get("origin") not in {"prospective", "retrospective"}:
            raise WorkflowError("invalid case origin")
        if payload.get("lifecycle") not in {"open", "closed_delivered",
                "closed_failed", "closed_abandoned", "blocked"}:
            raise WorkflowError("invalid case lifecycle")
        criteria = _criteria(payload)
        if payload["origin"] == "prospective" and not any(
                c["required"] for c in criteria):
            raise WorkflowError("prospective case requires acceptance")
        for link in payload.get("links", []):
            if not isinstance(link, dict) or link.get("status") not in {
                    "authoritative", "human_confirmed", "inferred", "unresolved"}:
                raise WorkflowError("invalid linkage status")
            text(link.get("relation"), "link relation")
    elif kind == "observation":
        for name in ("measure_id", "measure_version", "unit", "method"):
            text(payload.get(name), name)
        state = payload.get("availability")
        if state not in AVAILABILITY:
            raise WorkflowError("invalid observation availability")
        value = payload.get("value")
        if state == "observed" and value is None:
            raise WorkflowError("observed value cannot be null")
        if state != "observed" and value is not None:
            raise WorkflowError("unavailable value must be null")
        if state == "not_applicable" and not payload.get("applicability_rule"):
            raise WorkflowError("inapplicability needs a rule")
        if state == "observed" and payload["unit"] in {
                "count", "tokens", "bytes", "milliseconds", "seconds", "minutes",
                "currency"}:
            if type(value) not in (int, float) or value < 0:
                raise WorkflowError("nonnegative numeric observation required")
            if payload["unit"] in {"count", "tokens", "bytes"}:
                integer(value, "observation value")
    elif kind == "outcome":
        RecordRef.from_dict(payload.get("case_ref"))
        integer(payload.get("contract_version"), "contract version", 1)
        text(payload.get("criterion_key"), "criterion key")
        if payload.get("status") not in {"pass", "fail", "unknown", "not_applicable"}:
            raise WorkflowError("invalid outcome status")
        text(payload.get("verifier_type"), "verifier type")
        text(payload.get("verifier_version"), "verifier version")
        timestamp(payload.get("verified_at"))
        if not isinstance(payload.get("evidence_refs"), list):
            raise WorkflowError("outcome evidence list is required")
    elif kind == "assessment":
        RecordRef.from_dict(payload.get("case_ref"))
        for key in ("rubric_version", "protocol_version", "assessor_type",
                    "presentation_mode"):
            text(payload.get(key), key)
        if payload.get("abstention_reason") and payload.get("label") is not None:
            raise WorkflowError("abstention cannot have a semantic label")
    elif kind == "attempt":
        RecordRef.from_dict(payload.get("case_ref"))
        text(payload.get("harness"), "harness")
        if payload.get("started_at") and payload.get("ended_at"):
            if timestamp(payload["ended_at"]) < timestamp(payload["started_at"]):
                raise WorkflowError("attempt ends before its start")
    else:
        from .contracts import validate
        validate(kind, payload)


@dataclass(frozen=True)
class Record:
    """Store canonical bytes so nested user dictionaries cannot mutate a revision."""

    encoded: bytes

    def __post_init__(self):
        value = strict_loads(self.encoded)
        if not isinstance(value, dict):
            raise WorkflowError("record must be an object")
        required = {"record_format", "kind", "key", "revision", "supersedes_revision",
                    "created_at", "producer", "study_scope", "payload"}
        if set(value) != required:
            raise WorkflowError("record envelope fields mismatch")
        if type(value["record_format"]) is not int or value["record_format"] != 1:
            raise UnsupportedVersion("unsupported record format")
        ref = RecordRef.from_dict({k: value[k] for k in (
            "study_scope", "kind", "key", "revision")})
        if ref.kind not in KINDS:
            raise UnsupportedVersion("unregistered record kind")
        if ref.kind in {'investigation', 'investigation_event'} and ref.revision != 1:
            raise WorkflowError('investigation roots and events are immutable identities')
        expected = ref.revision - 1 if ref.revision > 1 else None
        if value["supersedes_revision"] != expected or (
                expected is not None and type(value["supersedes_revision"]) is not int):
            raise WorkflowError("invalid superseded revision")
        timestamp(value["created_at"])
        producer = value["producer"]
        if not isinstance(producer, dict):
            raise WorkflowError("producer must be an object")
        text(producer.get("name"), "producer name")
        text(producer.get("version"), "producer version")
        _validate_payload(ref.kind, value["payload"])
        if ref.kind == 'investigation_event' and value['payload']['root_ref']['study_scope'] != ref.study_scope:
            raise WorkflowError('event and root must share a study scope')
        object.__setattr__(self, "encoded", canonical(value))

    @classmethod
    def from_dict(cls, value):
        return cls(canonical(value))

    @classmethod
    def create(cls, kind, key, payload, *, study_scope="default", revision=1,
               created_at=None, producer="workflow", producer_version="1"):
        integer(revision, "revision", 1)
        return cls.from_dict({"record_format": 1, "kind": kind, "key": key,
            "revision": revision, "supersedes_revision": revision - 1 if revision > 1 else None,
            "created_at": created_at or utc_now(), "producer": {
                "name": producer, "version": producer_version},
            "study_scope": study_scope, "payload": payload})

    def to_dict(self):
        return strict_loads(self.encoded)

    @property
    def ref(self):
        value = self.to_dict()
        return RecordRef.from_dict({k: value[k] for k in (
            "study_scope", "kind", "key", "revision")})

    @property
    def payload(self):
        return self.to_dict()["payload"]

    @property
    def sha256(self):
        return digest(self.encoded)


@dataclass(frozen=True)
class DeliveryResult:
    status: str
    reason: str
    criteria: tuple[tuple[str, str], ...]


def evaluate_delivery(case: Record, outcomes, evidence_valid: Callable,
                      outcome_valid: Callable | None = None) -> DeliveryResult:
    """Only valid required criterion evidence can establish delivery or failure."""
    if case.ref.kind != "case":
        raise WorkflowError("delivery requires a case")
    payload = case.payload
    required = [c for c in payload["contract"]["criteria"] if c["required"]]
    results = []
    for criterion in required:
        applicable = []
        for outcome in outcomes:
            if outcome.ref.kind != "outcome":
                raise WorkflowError("non-outcome in delivery inputs")
            p = outcome.payload
            if (p["case_ref"] != case.ref.to_dict() or
                    p["contract_version"] != payload["contract"]["version"] or
                    p["criterion_key"] != criterion["key"]):
                continue
            refs = p["evidence_refs"]
            valid = bool(refs) and all(evidence_valid(ref) for ref in refs)
            if outcome_valid is not None:
                valid = valid and outcome_valid(case, criterion, outcome)
            if p["status"] == "not_applicable":
                valid = valid and bool(p.get("applicability_rule"))
            applicable.append(p["status"] if valid else "unknown")
        if "fail" in applicable:
            result = "fail"
        elif len(set(applicable)) == 1 and applicable:
            result = applicable[0]
        else:
            result = "unknown"
        results.append((criterion["key"], result))
    states = [state for _, state in results]
    if "fail" in states:
        return DeliveryResult("fail", "required_criterion_failed", tuple(results))
    if not states or all(s == "not_applicable" for s in states):
        return DeliveryResult("unknown", "no_applicable_acceptance_basis", tuple(results))
    if "unknown" in states:
        return DeliveryResult("unknown", "incomplete_acceptance_evidence", tuple(results))
    return DeliveryResult("pass", "required_acceptance_verified", tuple(results))
