"""Result observations v1: source association, never task success or efficiency.

Transport parsing stays separate from legacy traces. Only explicit unique IDs
bind results. Exporter envelopes may corroborate a path, never create an ID.
All selectors address frozen raw bytes; this artifact copies no source text.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re
from pathlib import PurePosixPath, PureWindowsPath

from ..adapters.base import parse_timestamp
from ..adapters.antigravity import _valid_step

VERSION = '1'
_MISSING = object()
ASSOCIATIONS = ('identifier_bound', 'path_corroborated', 'adjacent_context',
                'ambiguous', 'unavailable')


def _reference(raw_ref, line, pointer):
    return {**raw_ref, 'selector': {'kind': 'jsonl_pointer', 'line': line,
                                  'pointer': pointer}}


def _content(value=_MISSING):
    texts, nontext = [], 0
    if isinstance(value, str):
        texts = [value]
    elif isinstance(value, list):
        for part in value:
            if isinstance(part, dict) and part.get('type') in ('text', 'output_text', 'input_text') and isinstance(part.get('text'), str):
                texts.append(part['text'])
            else:
                nontext += 1
    # A notice is source-reported text, not an authenticated transport flag.
    notice = any(re.search(r'^(?:Warning: truncated output|<truncated \d+ (?:lines|bytes)>|Error: result .* exceeds maximum allowed tokens)',
                           '\n'.join(t.splitlines()[:12]), re.M) for t in texts)
    return {'availability': 'observed' if texts else 'not_observed',
            'reason': ('text_fields_observed' if texts else 'field_absent' if value is _MISSING
                       else 'null_content' if value is None else 'nontext_or_empty_parts' if isinstance(value, list)
                       else 'unsupported_content_shape'),
            'text_characters': sum(map(len, texts)) if texts else None,
            'unit': 'unicode_characters', 'layer': 'captured_result_text_fields',
            'nontext_parts': nontext, 'completeness': 'unknown',
            'truncation': 'reported_notice' if notice else 'not_observed'}


def _entry(source, line, pointer, call, identity, raw_ref, ids):
    return {'key': ids.make(source, raw_ref['sha256'], line, pointer),
            'identity': ids.make(source, raw_ref['sha256'], identity) if identity else None,
            'call_ref': _reference(raw_ref, line, pointer),
            'association': 'unavailable', 'reason': 'no_unique_result',
            'result_refs': [], 'context_refs': [], 'candidate_result_refs': [],
            'candidate_results_omitted': 0,
            'content': {**_content(), 'reason': 'no_associated_result'}, 'outcome': 'unknown'}


def _identity_mapping(source, rows, raw_ref, ids):
    calls, outputs, malformed = [], [], 0
    identity_occurrences = Counter()
    def note_identity(value):
        if isinstance(value, str) and value:
            identity_occurrences[value] += 1
    for line, row in rows:
        if source == 'codex':
            p = row.get('payload')
            if row.get('type') != 'response_item' or not isinstance(p, dict):
                continue
            typ = p.get('type')
            if typ in ('function_call', 'custom_tool_call'):
                note_identity(p.get('call_id'))
                if not isinstance(p.get('name'), str) or not p['name']:
                    malformed += 1
                    continue
                calls.append((line, '/payload', p, p.get('call_id'), typ))
            elif typ in ('function_call_output', 'custom_tool_call_output'):
                outputs.append((line, '/payload', p, p.get('call_id'), typ.removesuffix('_output')))
        else:
            msg = row.get('message')
            if not isinstance(msg, dict) or not isinstance(msg.get('content'), list):
                continue
            for index, p in enumerate(msg['content']):
                if not isinstance(p, dict):
                    continue
                pointer = '/message/content/' + str(index)
                if p.get('type') == 'tool_use' and msg.get('role') == 'assistant':
                    note_identity(p.get('id'))
                    if not isinstance(p.get('name'), str) or not p['name']:
                        malformed += 1
                        continue
                    calls.append((line, pointer, p, p.get('id'), 'tool_use'))
                elif p.get('type') == 'tool_result' and msg.get('role') == 'user':
                    outputs.append((line, pointer, p, p.get('tool_use_id'), 'tool_use'))
    def key(value):
        return value if isinstance(value, str) and value else None
    by_call, by_output = defaultdict(list), defaultdict(list)
    for item in calls:
        if key(item[3]):
            by_call[item[3]].append(item)
    for item in outputs:
        if key(item[3]):
            by_output[item[3]].append(item)
    entries = []
    for line, pointer, call, identity, family in calls:
        identity = key(identity)
        entry = _entry(source, line, pointer, call, identity, raw_ref, ids)
        matches = by_output.get(identity, []) if identity else []
        if not identity:
            entry['reason'] = 'call_identity_missing_or_invalid'
        elif identity_occurrences[identity] != 1 or len(matches) > 1:
            entry.update(association='ambiguous', reason='duplicate_or_conflicting_identity')
            entry['candidate_result_refs'] = [_reference(raw_ref, o[0], o[1]) for o in matches[:4]]
            entry['candidate_results_omitted'] = max(0, len(matches) - 4)
        elif matches:
            n, ptr, result, _, result_family = matches[0]
            if family != result_family:
                entry.update(association='ambiguous', reason='transport_family_mismatch')
                entry['candidate_result_refs'] = [_reference(raw_ref, n, ptr)]
            else:
                entry.update(association='identifier_bound', reason='unique_explicit_identity',
                             result_refs=[_reference(raw_ref, n, ptr)],
                             content=_content(result.get('output' if source == 'codex' else 'content', _MISSING)))
                if source == 'claude' and type(result.get('is_error')) is bool:
                    entry['outcome'] = 'reported_error' if result['is_error'] else 'reported_non_error'
        entries.append(entry)
    orphan = sum(not key(o[3]) or o[3] not in by_call for o in outputs)
    return entries, {'malformed_calls': malformed, 'orphan_results': orphan,
                     'result_occurrences': len(outputs), 'duplicate_snapshots': 0}


def _path_corroborated(call, step, result):
    args = call.get('args')
    if call.get('name') != 'view_file' or not isinstance(args, dict):
        return False
    path = args.get('AbsolutePath')
    if not isinstance(path, str) or not (PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()):
        return False
    before, after = parse_timestamp(step.get('created_at')), parse_timestamp(result.get('created_at'))
    if before is None or after is None or after < before:
        return False
    body = result.get('content')
    if not isinstance(body, str):
        return False
    # Anchor exporter headers before numbered source content. An echoed header
    # anywhere in arbitrary file text is not an exporter envelope.
    lines = body.splitlines()
    if len(lines) < 3 or not lines[0].startswith('Created At: ') or not lines[1].startswith('Completed At: '):
        return False
    headers = re.findall(r'^File Path: `file://([^`]+)`\r?$', body, re.M)
    if headers != [path]:
        return False
    return any(line == 'File Path: `file://' + path + '`' for line in lines[2:6])


def _agy_mapping(rows, raw_ref, ids):
    latest, snapshots = {}, 0
    for line, row in rows:
        if _valid_step(row):
            snapshots += row['step_index'] in latest
            latest[row['step_index']] = (line, row)
    entries, malformed, result_steps = [], 0, set()
    for step_index, (line, step) in sorted(latest.items()):
        if step['type'] != 'PLANNER_RESPONSE' or step['source'] != 'MODEL':
            continue
        calls = step.get('tool_calls', [])
        if not isinstance(calls, list):
            malformed += 1
            continue
        following = latest.get(step_index + 1)
        adjacent = following if (following and following[1]['type'] == 'GENERIC'
                                 and following[1]['source'] == 'MODEL') else None
        for index, call in enumerate(calls):
            if not isinstance(call, dict) or not isinstance(call.get('name'), str) or not call['name']:
                malformed += 1
                continue
            entry = _entry('antigravity', line, '/tool_calls/' + str(index), call, None, raw_ref, ids)
            entry['reason'] = 'no_explicit_call_result_identity'
            if adjacent:
                n, result = adjacent
                ref = _reference(raw_ref, n, '')
                result_steps.add(step_index + 1)
                entry.update(association='adjacent_context', reason='adjacency_is_not_identity', context_refs=[ref])
                if len(calls) != 1:
                    entry.update(association='ambiguous', reason='multiple_calls_share_adjacent_context')
                elif _path_corroborated(call, step, result):
                    entry.update(association='path_corroborated', reason='single_call_exporter_path_and_order',
                                 result_refs=[ref], context_refs=[], content=_content(result.get('content')))
            entries.append(entry)
    generics = {i for i, (_, row) in latest.items() if row['type'] == 'GENERIC'}
    return entries, {'malformed_calls': malformed, 'orphan_results': None,
                     'unassociated_context_steps': len(generics - result_steps),
                     'result_occurrences': None, 'duplicate_snapshots': snapshots}


def observe_results(source, rows, raw_ref, ids, *, included=True):
    """Pure metadata projection over captured rows; no source access or effects."""
    supported = source in ('claude', 'codex', 'antigravity')
    versions = set()
    for _, row in rows:
        version = row.get('version') if source == 'claude' else None
        if source == 'codex' and row.get('type') == 'session_meta' and isinstance(row.get('payload'), dict):
            version = row['payload'].get('cli_version', row['payload'].get('version'))
        if isinstance(version, str) and version:
            versions.add(version)
    status = 'observed' if included and supported else 'not_observed' if not included else 'not_supported'
    if status == 'observed':
        calls, extra = (_agy_mapping(rows, raw_ref, ids) if source == 'antigravity'
                        else _identity_mapping(source, rows, raw_ref, ids))
        counts = Counter(c['association'] for c in calls)
        coverage = {'calls': len(calls), **{k: counts[k] for k in ASSOCIATIONS}, **extra}
        coverage['text_observed_calls'] = sum(c['content']['text_characters'] is not None for c in calls)
        coverage['observed_text_characters'] = (sum(c['content']['text_characters'] or 0 for c in calls)
                                                 if coverage['text_observed_calls'] else None)
    else:
        calls, coverage = [], None
    return {'result_observation_version': VERSION, 'source': source,
            'source_sha256': raw_ref['sha256'], 'transport_version': next(iter(versions)) if len(versions) == 1 else None,
            'transport_version_state': 'observed' if len(versions) == 1 else 'conflicting' if versions else 'unknown',
            'availability': status, 'reason': '' if status == 'observed' else 'source_excluded' if not included else 'transport_unmapped',
            'coverage': coverage, 'calls': calls,
            'limitations': ['Source associations are not task correctness.',
                            'Inferred associations are not explicit identities.',
                            'Captured text size is not generated output, delivered context, tokens or billing.']}


def summarize_result_coverage(outputs):
    """Report older/unmapped sources as missing, never measured zero."""
    observed = []
    for output in outputs:
        item = output.get('observations', {}).get('tool_results', {})
        if item.get('availability') == 'observed' and isinstance(item.get('value'), dict):
            observed.append(item['value'])
    fields = ('calls', *ASSOCIATIONS, 'malformed_calls', 'text_observed_calls')
    return {'mapped_sources': len(observed), 'unmapped_sources': len(outputs) - len(observed),
            'counts': {k: sum(o[k] for o in observed) for k in fields} if observed else None}
