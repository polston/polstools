"""Usage coverage for normalized turn events, independent of native transport."""
from __future__ import annotations

import json
from ..workflow.records import strict_loads

FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens')
VERSION = 1


def summarize_usage(events, *, trace_complete, parse_errors=0):
    """Aggregate observations, never infer missing usage from completed turns.

    An adapter supplies event_id when its transport guarantees stable identity.
    Without it, byte-identical events are retained as ambiguous observations, not
    silently deduplicated: two genuine turns can report identical usage. Totals
    then remain partial and the ambiguity is visible. IDs are scoped to one trace.
    """
    seen, fingerprints, turns, issues = {}, set(), [], []
    duplicates = ambiguous = 0
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            issues.append('non-object event at %d' % index)
            continue
        if event.get('type') != 'turn.completed':
            continue
        identity = event.get('event_id')
        encoded = json.dumps(event, sort_keys=True, separators=(',', ':'))
        if identity is not None:
            if not isinstance(identity, str) or not identity:
                issues.append('invalid event identity at %d' % index)
                identity = None
            elif identity in seen:
                duplicates += 1
                if seen[identity] != encoded:
                    issues.append('conflicting duplicate event: ' + identity)
                continue
            else:
                seen[identity] = encoded
        if identity is None and encoded in fingerprints:
            ambiguous += 1
        fingerprints.add(encoded)
        turns.append(event.get('usage'))
    result = {}
    for field in FIELDS:
        values = []
        for index, usage in enumerate(turns):
            value = usage.get(field) if isinstance(usage, dict) else None
            if type(value) is int and value >= 0:
                values.append(value)
            elif value is not None:
                issues.append('invalid %s in turn %d' % (field, index))
        result[field] = {'observed_sum': sum(values) if values else None,
                         'measured_turns': len(values),
                         'missing_turns': len(turns) - len(values)}
    for index, usage in enumerate(turns):
        if not isinstance(usage, dict):
            continue
        a, b = usage.get('input_tokens'), usage.get('cached_input_tokens')
        if type(a) is int and type(b) is int and b > a:
            issues.append('cached input exceeds input in turn %d' % index)
    complete = (trace_complete is True and not parse_errors and not issues
                and not ambiguous and bool(turns)
                and all(v['missing_turns'] == 0 for v in result.values()))
    available = any(v['measured_turns'] for v in result.values())
    return {'version': VERSION, 'completed_turns': len(turns),
            'available': available, 'coverage': 'complete' if complete else
            'partial' if available else 'unavailable', 'trace_complete': trace_complete is True,
            'parse_errors': parse_errors, 'duplicate_events': duplicates,
            'ambiguous_duplicates': ambiguous, 'fields': result, 'issues': issues,
            'unit': 'provider-reported tokens; cached input is a subset of input'}


def read_jsonl_usage(text, *, trace_complete):
    """Normalize Codex-style JSONL; completion and usage availability differ."""
    events, errors = [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = strict_loads(line)
        except (ValueError, TypeError):
            errors += 1
            continue
        if not isinstance(event, dict):
            errors += 1
            continue
        events.append(event)
    return summarize_usage(events, trace_complete=trace_complete, parse_errors=errors)
