"""Conservative metadata recognition, separate from semantic trace coverage.

Only closed, validated shapes can be routine metadata. Content-bearing envelopes
remain gaps with selectors into the original capture; recognition does not turn
machine context or a queued message into an independently attributed user turn.
"""
from collections import Counter
import re

VERSION = '1'


def _shape(value, required, optional=()):
    return (isinstance(value, dict) and set(required) <= value.keys()
            and value.keys() <= set(required) | set(optional))


def _text(value):
    return isinstance(value, str)


def _token_usage(row):
    if not _shape(row, ('type', 'payload'), ('timestamp', 'ordinal')):
        return False
    if (('timestamp' in row and not _text(row['timestamp']))
            or ('ordinal' in row and (type(row['ordinal']) is not int or row['ordinal'] < 0))):
        return False
    payload = row['payload']
    identities = ('response_id', 'root_turn_id', 'session_id', 'thread_id', 'turn_id')
    usages = ('usage', 'turn_token_usage', 'thread_token_usage')
    if not _shape(payload, (*identities, *usages)):
        return False
    if not all(_text(payload[key]) and payload[key] for key in identities):
        return False
    fields = {'input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
              'output_tokens', 'reasoning_output_tokens', 'total_tokens'}
    for key in usages:
        usage = payload[key]
        if (not _shape(usage, ('input_tokens', 'output_tokens', 'total_tokens'), fields)
                or not all(type(value) is int and value >= 0 for value in usage.values())):
            return False
    return True


def _claude_metadata(row):
    kind = row.get('type')
    field = {'ai-title': 'aiTitle', 'mode': 'mode',
             'permission-mode': 'permissionMode', 'atis-latch': 'atis'}.get(kind)
    if field is None:
        return None
    valid = (_shape(row, ('type', field, 'sessionId'), ('timestamp',))
             and _text(row['sessionId']) and bool(row['sessionId'])
             and _text(row[field])
             and ('timestamp' not in row or _text(row['timestamp'])))
    # An empty latch has no delivered text. A future/nonempty value has unknown
    # semantics and must not be silenced just because its envelope is familiar.
    if kind == 'atis-latch':
        valid = valid and row.get(field) == ''
    return valid


def _budget_reminder(row):
    """Recognize only the inert numeric reminder and its exact rendering."""
    attachment = row.get('attachment')
    if not isinstance(attachment, dict) or attachment.get('type') != 'total_tokens_reminder':
        return None
    envelope = {'type', 'attachment', 'cwd', 'entrypoint', 'gitBranch', 'isSidechain',
                'parentUuid', 'rendered', 'renderedRole', 'sessionId', 'session_id',
                'timestamp', 'userType', 'uuid', 'version'}
    if (not _shape(row, ('type', 'attachment'), envelope)
            or not _shape(attachment, ('type', 'text'))
            or not isinstance(attachment['text'], str)
            or re.fullmatch(r'<total_tokens>[0-9]+ tokens left</total_tokens>', attachment['text']) is None):
        return False
    for field in envelope - {'type', 'attachment', 'rendered', 'isSidechain', 'parentUuid'}:
        if field in row and not _text(row[field]):
            return False
    if ('isSidechain' in row and type(row['isSidechain']) is not bool
            or 'parentUuid' in row and row['parentUuid'] is not None and not _text(row['parentUuid'])):
        return False
    rendered = row.get('rendered')
    return (rendered is None or rendered == [
        {'content': '<system-reminder>\n' + attachment['text'] + '\n</system-reminder>'}])


def classify_record(source, row):
    """Return disposition and pointer only for explicitly assessed envelopes.

    None retains the existing parser behavior. This is deliberately not an
    exhaustive nested-schema validator or a claim of semantic completeness.
    """
    kind = row.get('type')
    if not isinstance(kind, str):
        return None
    if source == 'codex':
        if kind == 'token_usage_record':
            return ('routine_metadata' if _token_usage(row) else 'invalid_metadata_record',
                    '/payload' if 'payload' in row else '')
        if kind in ('world_state', 'compacted'):
            return ('unmapped_context_record' if isinstance(row.get('payload'), dict)
                    else 'invalid_record_payload', '/payload' if 'payload' in row else '')
    elif source == 'claude':
        metadata = _claude_metadata(row)
        if metadata is not None:
            return ('routine_metadata' if metadata else 'invalid_metadata_record', '')
        if kind == 'attachment':
            attachment = row.get('attachment')
            if not isinstance(attachment, dict):
                return ('invalid_record_payload', '/attachment' if 'attachment' in row else '')
            reminder = _budget_reminder(row)
            if reminder is not None:
                return ('routine_metadata' if reminder else 'invalid_metadata_record', '/attachment')
            # Attachment subtypes can contain instructions, queued user text,
            # hook results, or changed files. No blanket attachment allowlist.
            return ('unmapped_context_record', '/attachment')
        if kind == 'summary':
            return ('unmapped_context_record', '')
    elif source == 'antigravity' and kind in ('SYSTEM_MESSAGE', 'CHECKPOINT'):
        return ('unmapped_context_record', '')
    return None


def assess_records(source, rows, raw_ref):
    """Retain exact raw selectors without copying content or inventing actors."""
    if source not in ('claude', 'codex', 'antigravity'):
        return [], {'availability': 'not_supported', 'value': None,
                    'method': source + '.conservative_record_classification',
                    'mapping_version': VERSION, 'unit': 'records',
                    'reason': 'source_classification_unmapped'}
    entries, diagnostics = [], []
    for number, row in rows:
        assessment = classify_record(source, row)
        if assessment is None:
            continue
        disposition, pointer = assessment
        ref = {**raw_ref, 'selector': {'kind': 'jsonl_pointer', 'line': number,
                                     'pointer': pointer}}
        entries.append({'line': number, 'disposition': disposition, 'raw_ref': ref})
        if disposition != 'routine_metadata':
            diagnostics.append({'line': number, 'reason': disposition, 'raw_ref': ref})
    counts = Counter(entry['disposition'] for entry in entries)
    return diagnostics, {
        'availability': 'observed', 'method': source + '.conservative_record_classification',
        'mapping_version': VERSION, 'unit': 'records',
        'value': {'routine_metadata_records': counts['routine_metadata'],
                  'context_gap_records': counts['unmapped_context_record'],
                  'invalid_metadata_records': counts['invalid_metadata_record']},
        'entries': entries,
        'limitations': ['Only explicitly listed envelopes are classified.',
                        'Raw context is retained, not semantically normalized.',
                        'Metadata recognition does not supply usage, user attribution or result association.']}


def summarize_record_coverage(outputs):
    """Older receipts have no classification; missing is not observed zero."""
    observations = [output.get('observations', {}).get('record_coverage', {})
                    for output in outputs]
    observed = [item['value'] for item in observations
                if item.get('availability') == 'observed' and isinstance(item.get('value'), dict)]
    fields = ('routine_metadata_records', 'context_gap_records', 'invalid_metadata_records')
    return {'mapped_sources': len(observed), 'unmapped_sources': len(outputs) - len(observed),
            'counts': {key: sum(item[key] for item in observed) for key in fields} if observed else None}
