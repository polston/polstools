"""Validation and measurement for repository-owned wrapped hook events."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


OWNED_HOOKS = frozenset({
    "format.session-start",
    "format.user-prompt-submit",
})
COMMON_FIELDS = frozenset({
    "schema_version", "source", "hook_id", "hook_version", "trigger_kind",
    "invocation_id", "event", "observed_at_ns",
})
END_FIELDS = frozenset({
    "status", "latency_ms", "injected_bytes", "content_sha256",
})


def _read_events(path: Path):
    events = []
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError
                events.append(event)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("owned hook telemetry is unreadable") from exc
    return events


def _validate_event(event):
    allowed = COMMON_FIELDS | (END_FIELDS if event.get("event") == "end" else set())
    if set(event) - allowed:
        raise ValueError("owned hook telemetry must remain content-free")
    if event.get("schema_version") != 1:
        raise ValueError("unsupported owned hook event schema")
    if event.get("source") != "polstools.format":
        raise ValueError("hook event source is not repository-owned")
    if event.get("hook_id") not in OWNED_HOOKS:
        raise ValueError("hook event is outside owned coverage")
    if event.get("event") not in {"opportunity", "start", "end"}:
        raise ValueError("unsupported owned hook lifecycle event")
    required = COMMON_FIELDS | (END_FIELDS if event["event"] == "end" else set())
    if any(event.get(field) in {None, ""} for field in required):
        raise ValueError("owned hook event is incomplete")
    if event["event"] == "end":
        if event["status"] not in {"ok", "disabled", "missing_payload"}:
            raise ValueError("unsupported owned hook terminal status")
        if int(event["latency_ms"]) < 0 or int(event["injected_bytes"]) < 0:
            raise ValueError("owned hook measurements cannot be negative")
        digest = str(event["content_sha256"])
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("owned hook content hash is invalid")


def evaluate_owned_hook_events(path: Path, *, expected_invocations: int,
                               baseline_normalized_bytes: int):
    """Evaluate only invocations executed by the repository-owned wrapper."""
    if expected_invocations < 1 or baseline_normalized_bytes < 1:
        raise ValueError("owned hook evaluation requires positive baselines")
    events = _read_events(path)
    grouped = defaultdict(list)
    for event in events:
        _validate_event(event)
        grouped[str(event["invocation_id"])].append(event)
    complete = 0
    starts = ends = 0
    status_counts = Counter()
    for lifecycle in grouped.values():
        names = [item["event"] for item in lifecycle]
        starts += names.count("start")
        ends += names.count("end")
        if names == ["opportunity", "start", "end"]:
            complete += 1
        status_counts.update(str(item["status"]) for item in lifecycle
                             if item["event"] == "end")
    invalid_groups = len(grouped) - complete
    return {
        "schema_version": 1,
        "coverage": "repository_owned_wrapped_hooks_only",
        "harness_wide_opportunity_coverage": "not_observable",
        "expected_invocations": expected_invocations,
        "observed_invocations": len(grouped),
        "complete_invocations": complete,
        "capture_precision": (complete / (complete + invalid_groups)
                              if grouped else 0.0),
        "capture_recall": min(1.0, complete / expected_invocations),
        "unmatched_terminal_rate": (
            max(0, starts - ends) / starts if starts else 0.0),
        "added_normalized_bytes": Path(path).stat().st_size,
        "added_normalized_byte_share": (
            Path(path).stat().st_size / baseline_normalized_bytes),
        "status_counts": dict(sorted(status_counts.items())),
        "limitations": [
            "only repository-owned deterministically wrapped hooks are covered",
            "silent opportunities elsewhere in either harness are not observable",
        ],
    }
