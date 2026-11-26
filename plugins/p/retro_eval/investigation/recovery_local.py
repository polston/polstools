"""Experimental adapter for the isolated local recovery transport, version 1.

Only public evidence is read. This is not a generic adapter for arbitrary logs.
Run as a module with a workspace path; no files are written by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import time

from ..workflow.records import canonical, strict_loads
from .recovery import uint
from .recovery_summary import fingerprints, project, reference, render

ADAPTER = 'isolated-local-service-v1'
IDENTITY = re.compile(r'[a-zA-Z0-9_-]{1,96}\Z')


def capture(root):
    """Discover additions/deletions, reject symlinks and preserve exact bytes."""
    root = Path(root).resolve()
    names = {'task.json', 'client-journal.jsonl', 'recovery-barrier.json'}
    transport = root / 'transport'
    if transport.is_symlink():
        raise ValueError('transport cannot be a symlink')
    if transport.exists():
        names.update('transport/' + p.name for p in transport.iterdir()
                     if p.suffix in {'.sent', '.response', '.request', '.tmp'})
    sources = {}
    for name in sorted(names):
        p = root / name
        if p.is_symlink():
            raise ValueError('evidence cannot be a symlink: ' + name)
        if p.exists():
            if not p.is_file() or p.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('unsupported source size/type: ' + name)
            sources[name] = p.read_bytes()
    return sources


def summarize(root, *, now_ms=None):
    sources = capture(root)
    result = from_sources(sources, now_ms=time.time_ns() // 1_000_000 if now_ms is None else now_ms)
    if fingerprints(capture(root)) != result['sources']:
        result['issues'].append('evidence changed during generation')
        result['supported_outcome'] = None
        result['next_step'] = {'code': 'review_evidence', 'reason': 'Refresh after writers are quiescent.',
                               'authority': 'advisory; this view never authorizes execution'}
    return result


def check_current(root, summary, *, now_ms=None):
    """Rebuild, including semantic changes and elapsed original deadline."""
    current = summarize(root, now_ms=now_ms)
    current['as_of_ms'] = summary.get('as_of_ms')
    return current == summary


def from_sources(sources, *, now_ms):
    issues = []
    def read(path):
        try:
            value = strict_loads(sources[path])
            if not isinstance(value, dict):
                raise ValueError('expected object')
            return value
        except (KeyError, ValueError, TypeError) as exc:
            issues.append('missing or malformed ' + path + ': ' + type(exc).__name__)
            return {}
    task_raw = read('task.json')
    family = task_raw.get('family')
    if not isinstance(family, str) or family not in {'ledger', 'publish', 'opaque'}:
        family = 'unsupported'
    request = task_raw.get('request')
    req_valid = (isinstance(request, dict) and isinstance(request.get('key'), str) and bool(request['key']) and
                 (family == 'ledger' and type(request.get('delta')) is int or
                  family == 'publish' and isinstance(request.get('content'), str) or
                  family == 'opaque' and uint(request.get('units'))))
    if not req_valid:
        issues.append('unsupported or malformed service request')
        request = {}
    if task_raw.get('recovery_transport') != ADAPTER:
        issues.append('unsupported transport contract')
    task = {'request': request, 'family': family, 'deadline_ms': task_raw.get('deadline_ms'),
            'submission_allowance': task_raw.get('submission_allowance'),
            'idempotent_exact_replay': family == 'publish' and req_valid,
            'query_available': family in {'ledger', 'publish'},
            'evidence': [reference(sources, 'task.json')] if 'task.json' in sources else []}
    barrier_raw = read('recovery-barrier.json')
    barrier = {'verified': barrier_raw.get('verified') is True and uint(barrier_raw.get('at_ns')),
               'evidence': [reference(sources, 'recovery-barrier.json')] if 'recovery-barrier.json' in sources else []}
    events = []
    journal_ok = 'client-journal.jsonl' in sources
    if not journal_ok:
        issues.append('invocation journal unavailable; absence is not zero submissions')
    else:
        for i, line in enumerate(sources['client-journal.jsonl'].splitlines(), 1):
            try:
                event = strict_loads(line)
                if (not isinstance(event, dict) or not isinstance(event.get('attempt_id'), str) or
                        not IDENTITY.fullmatch(event['attempt_id']) or event.get('stage') != 'invoked' or
                        event.get('mode') not in {'submit', 'inspect', 'checkpoint'} or not uint(event.get('at_ns'))):
                    raise ValueError('invalid invocation')
                events.append((event, reference(sources, 'client-journal.jsonl', 'line=' + str(i))))
            except (ValueError, TypeError):
                journal_ok = False; issues.append('malformed invocation journal line ' + str(i))
    by_id = {}
    previous_ns = -1
    for event, ref in events:
        identity = event['attempt_id']
        if identity in by_id:
            issues.append('reused invocation identity: ' + identity)
        if event['at_ns'] < previous_ns:
            issues.append('invocation ordering is not monotonic')
        previous_ns = event['at_ns']
        by_id[identity] = (event, ref)
    attempts, observations = [], []
    latest_submit = max((e['at_ns'] for e, _ in events if e['mode'] == 'submit'), default=-1)
    # The original barrier cannot quiesce a newly started, unresolved writer.
    for identity, (event, _) in by_id.items():
        if (event['mode'] == 'submit' and uint(barrier_raw.get('at_ns')) and
                event['at_ns'] >= barrier_raw['at_ns'] and
                'transport/' + identity + '.response' not in sources):
            barrier['verified'] = False
            issues.append('unresolved submission is newer than the writer barrier: ' + identity)
    for identity, (event, ref) in by_id.items():
        mode = event['mode']; sent_path = 'transport/' + identity + '.sent'; response_path = 'transport/' + identity + '.response'
        if mode == 'submit':
            attempts.append({'attempt_id': identity, 'stage': 'invoked', 'source': ref})
        envelope = read(sent_path)
        if envelope.get('attempt_id') != identity or envelope.get('mode') != mode:
            issues.append('request identity/mode mismatch: ' + identity)
            continue
        if mode == 'submit' and canonical(envelope.get('request')) != canonical(request):
            issues.append('submission differs from requested task: ' + identity)
            continue
        if response_path not in sources:
            continue
        response = read(response_path)
        refs = [ref, reference(sources, sent_path), reference(sources, response_path)]
        if mode == 'submit':
            attempts.append({'attempt_id': identity, 'stage': 'acknowledged', 'source': refs[-1]})
            effect = response.get('effect')
            if response.get('confirmed') is not True or not isinstance(effect, dict):
                issues.append('submission response is not a supported receipt: ' + identity)
                continue
            matches = effect.get('key') == request.get('key')
            if family == 'ledger':
                matches = matches and type(effect.get('delta')) is int and effect.get('delta') == request.get('delta')
                outcome = {'balance': 100 + request.get('delta', 0)}
            elif family == 'publish':
                expected = hashlib.sha256(request.get('content', '').encode()).hexdigest()
                matches = matches and effect.get('sha256') == expected
                outcome = {'sha256': expected}
            else:
                matches = matches and uint(effect.get('units')) and effect.get('units') == request.get('units') and isinstance(effect.get('receipt'), str) and bool(effect.get('receipt'))
                outcome = {'receipt': effect.get('receipt')}
            if not matches:
                issues.append('receipt does not match requested outcome: ' + identity)
            observations.append({'kind': 'receipt', 'attempt_id': identity, 'effect_count': 1,
                                 'matches_request': matches, 'outcome': outcome, 'evidence': refs})
        elif mode == 'inspect' and response.get('available') is True:
            expected_fields = {'available', 'entries', 'balance'} if family == 'ledger' else {'available', 'published'}
            if set(response) != expected_fields:
                issues.append('query schema does not establish complete current evidence: ' + identity)
                continue
            effects = response.get('entries' if family == 'ledger' else 'published')
            if family not in {'ledger', 'publish'} or not isinstance(effects, list) or not all(isinstance(e, dict) for e in effects):
                issues.append('malformed or unsupported query: ' + identity)
                continue
            matches = all(e.get('key') == request.get('key') for e in effects)
            if family == 'ledger':
                matches = matches and all(type(e.get('delta')) is int and e.get('delta') == request.get('delta') for e in effects)
                expected_balance = 100 + len(effects) * request.get('delta', 0)
                if type(response.get('balance')) is not int or response.get('balance') != expected_balance:
                    issues.append('query balance contradicts entries: ' + identity)
                outcome = {'balance': response.get('balance')}
            else:
                expected = hashlib.sha256(request.get('content', '').encode()).hexdigest()
                matches = matches and all(e.get('sha256') == expected for e in effects)
                outcome = {'sha256': expected}
            if not matches:
                issues.append('query contains an unexpected effect: ' + identity)
            observations.append({'kind': 'query', 'effect_count': len(effects), 'matches_request': matches,
                                 'complete': True, 'current': event['at_ns'] > latest_submit and event['at_ns'] > barrier_raw.get('at_ns', 2**100) if uint(barrier_raw.get('at_ns')) else False,
                                 'quiescent': barrier['verified'], 'outcome': outcome if effects else None,
                                 'evidence': refs})
        elif mode == 'inspect' and (family != 'opaque' or response.get('available') is not False):
            issues.append('unsupported inspection response: ' + identity)
    for path in sources:
        if not path.startswith('transport/'):
            continue
        name = Path(path)
        if name.suffix in {'.request', '.tmp'}:
            issues.append('pending transport artifact: ' + path)
        elif name.stem not in by_id:
            issues.append('unlinked transport artifact: ' + path)
    return project(task=task, attempts=attempts, observations=observations, barrier=barrier,
                   coverage_complete=journal_ok and not issues, source_hashes=fingerprints(sources),
                   issues=issues, now_ms=now_ms, adapter=ADAPTER)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root'); p.add_argument('--json', action='store_true')
    args = p.parse_args(argv)
    try:
        result = summarize(args.root)
    except (OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc), 'next_step': 'review_evidence'}))
        return 2
    print(json.dumps(result, sort_keys=True) if args.json else render(result), end='\n' if args.json else '')
    return 1 if result['issues'] or result['next_step']['code'] in {'review_evidence', 'stop'} else 0


if __name__ == '__main__':
    raise SystemExit(main())
