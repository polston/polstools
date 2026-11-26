"""Resolve selectors against exactly the bytes whose digest was checked."""

from __future__ import annotations

from dataclasses import dataclass

from ..proposal_report import _json_pointer
from .errors import EvidenceError, PolicyDenied, WorkflowError
from .records import CONTENT_CLASSES, strict_loads


@dataclass(frozen=True)
class EvidenceResolution:
    status: str
    data: bytes | None = None
    value: object = None
    content_class: str = ""
    reason: str = ""


def resolve_evidence(reference, registry, *, allowed_classes=CONTENT_CLASSES):
    try:
        data, content_class = registry.read_artifact(reference, allowed_classes)
        selector = reference.get("selector", {"kind": "whole"})
        if not isinstance(selector, dict):
            raise EvidenceError('evidence selector must be an object')
        kind = selector.get("kind")
        if kind == "whole":
            value = data
        elif kind == "json_pointer":
            value = _json_pointer(strict_loads(data), selector["pointer"])
        elif kind == "jsonl_pointer":
            from .records import integer
            if set(selector) != {'kind', 'line', 'pointer'}:
                raise EvidenceError('JSONL selector fields mismatch')
            line = integer(selector['line'], 'JSONL line', 1)
            lines = data.splitlines()
            if line > len(lines):
                raise EvidenceError('JSONL line absent')
            pointer = selector['pointer']
            if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')):
                raise EvidenceError('invalid JSON pointer')
            value = _json_pointer(strict_loads(lines[line - 1]), pointer)
        elif kind == "normalized_span":
            if not isinstance(selector.get('span_id'), str) or not selector['span_id']:
                raise EvidenceError('normalized span identity must be a nonempty string')
            rows = [strict_loads(line) for line in data.splitlines() if line.strip()]
            if any(not isinstance(row, dict) for row in rows):
                raise EvidenceError("normalized span evidence must contain object rows")
            found = [r for r in rows if r.get("span_id") == selector["span_id"]]
            if len(found) != 1:
                raise EvidenceError("span selector is absent or ambiguous")
            value = found[0]
        else:
            raise EvidenceError("unsupported evidence selector")
        return EvidenceResolution("resolved", data, value, content_class)
    except PolicyDenied as exc:
        return EvidenceResolution("policy_denied", reason=str(exc))
    except (WorkflowError, KeyError, TypeError, ValueError) as exc:
        return EvidenceResolution("invalid", reason=str(exc))


def judge_input(reference, registry, *, external=False, allow_private_external=False):
    if type(external) is not bool or type(allow_private_external) is not bool:
        raise PolicyDenied("judge authority flags must be booleans")
    result = resolve_evidence(reference, registry, allowed_classes={
        "public_synthetic", "private_metadata", "private_redacted"})
    if result.status != "resolved":
        raise PolicyDenied("judge evidence is unavailable or disallowed")
    if external and result.content_class != "public_synthetic" and not allow_private_external:
        raise PolicyDenied("private external judging lacks authority")
    return result
