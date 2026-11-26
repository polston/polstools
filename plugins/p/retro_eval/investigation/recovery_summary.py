"""Read-only recovery projections; attributed observations are not authority.

Adapters establish semantic facts and complete source discovery. A projection
binds those facts to a snapshot, never authenticates them or locks the service.
"""
from __future__ import annotations

import hashlib
import json

from .recovery import outcome_knowledge, reconcile_attempts, retry_judgment, uint

VERSION = 1


def fingerprints(sources):
    return {path: hashlib.sha256(raw).hexdigest() for path, raw in sorted(sources.items())}


def reference(sources, path, selector=''):
    return path + '@' + hashlib.sha256(sources[path]).hexdigest() + '#' + selector


def bound_reference(ref, hashes):
    if not isinstance(ref, str) or '#' not in ref or '@' not in ref:
        return False
    path, digest = ref.split('#', 1)[0].rsplit('@', 1)
    return hashes.get(path) == digest


def project(*, task, attempts, observations, barrier, coverage_complete,
            source_hashes, issues, now_ms, adapter):
    """Project validated adapter facts without writing, executing or reserving.

    Evidence refs use path@sha256#selector. The adapter validates selectors and
    their semantics; this layer checks their binding and existing recovery rules.
    coverage_complete is an adapter claim about the entire submission channel,
    not an inference from a missing file. Observations are in service order.
    """
    problems = list(issues)
    if not isinstance(task, dict):
        task = {}; problems.append('task is unavailable')
    evidence = task.get('evidence', [])
    if not evidence or not all(bound_reference(r, source_hashes) for r in evidence):
        problems.append('task evidence is not bound')
    if not uint(task.get('deadline_ms')) or not uint(task.get('submission_allowance')):
        problems.append('original bounds are unavailable')
    for row in attempts:
        if not bound_reference(row.get('source'), source_hashes):
            problems.append('attempt evidence is not bound')
    valid_observations = []
    for obs in observations:
        if not obs.get('evidence') or not all(bound_reference(r, source_hashes) for r in obs['evidence']):
            problems.append('outcome evidence is not bound')
        else:
            valid_observations.append(obs)
    barrier_ok = (barrier.get('verified') is True and bool(barrier.get('evidence')) and
                  all(bound_reference(r, source_hashes) for r in barrier['evidence']))
    accounting = reconcile_attempts(attempts, dispatch_journal_complete=coverage_complete)
    problems.extend(accounting['issues'])
    if accounting['unlinked']:
        problems.append('unlinked attempt evidence')
    if coverage_complete is not True:
        problems.append('submission-channel coverage is incomplete')
    charged = accounting['charged_lower_bound']
    spent = sum(bool(set(a['stages']) & {'acknowledged'}) for a in accounting['attempts'].values())
    limit = task.get('submission_allowance')
    remaining = limit - charged if uint(limit) and limit >= charged and coverage_complete is True else None
    if uint(limit) and charged > limit:
        problems.append('observed attempts exceed original allowance')
    current = [o for o in valid_observations if outcome_knowledge(o)['status'] == 'pass']
    queries = [o for o in current if o.get('kind') == 'query']
    receipts = [o for o in current if o.get('kind') == 'receipt']
    if queries and receipts and queries[-1].get('effect_count') == 0:
        problems.append('current absence contradicts a confirmed receipt')
    observation = queries[-1] if queries else receipts[-1] if receipts else {}
    knowledge = outcome_knowledge(observation)
    idempotent = task.get('idempotent_exact_replay') is True
    # A receipt confirms one identified effect, not absence of other attempts.
    confirmed_one = (knowledge['status'] == 'pass' and observation.get('effect_count') == 1 and
                     (observation.get('kind') == 'query' or charged == 1 or idempotent))
    if not idempotent and len({o.get('attempt_id') for o in receipts}) > 1:
        problems.append('multiple non-idempotent effects have matching receipts')
        confirmed_one = False
    if knowledge['status'] == 'pass' and observation.get('effect_count', 0) > 1:
        problems.append('multiple effects violate the requested single outcome')
    if not uint(now_ms):
        problems.append('current time unavailable')
    expired = uint(now_ms) and uint(task.get('deadline_ms')) and now_ms >= task['deadline_ms']
    if problems:
        code, reason = 'review_evidence', 'Resolve the listed evidence problems before a consequential action.'
    elif expired:
        code, reason = 'stop', 'Original deadline reached; preserve evidence without new submissions.'
    elif not barrier_ok:
        code, reason = 'review_evidence', 'Prior-writer quiescence has not been established.'
    elif confirmed_one:
        code, reason = 'record_completion', 'Evidence supports one requested outcome; do not resubmit.'
    elif task.get('query_available') is True and not queries:
        code, reason = 'inspect', 'Obtain a complete current outcome query before deciding whether to submit.'
    elif remaining is None or remaining <= 0:
        code, reason = 'preserve_uncertainty', 'No established remaining submission allowance; preserve unresolved work.'
    else:
        retry = retry_judgment(retried=True, idempotent_exact_replay=idempotent,
                               observation=observation, barrier_verified=barrier_ok)
        if charged == 0 or retry['status'] == 'pass':
            code, reason = 'consider_submission', 'A submission may be justified only after checking current evidence, exact request and normal admission.'
        else:
            code, reason = 'preserve_uncertainty', 'Execution is unknown and a non-idempotent repeat could duplicate the effect.'
    return {'version': VERSION, 'adapter': adapter, 'as_of_ms': now_ms,
            'sources': dict(sorted(source_hashes.items())), 'task': task,
            'accounting': {'spent': spent, 'held': charged - spent, 'charged': charged,
                           'remaining': remaining, 'coverage_complete': coverage_complete,
                           'unit': 'submission slots; pending invocations remain charged'},
            'attempts': accounting['attempts'], 'observations': valid_observations,
            'knowledge': knowledge, 'supported_outcome': observation if confirmed_one and not problems else None,
            'barrier': barrier, 'issues': sorted(set(problems)),
            'next_step': {'code': code, 'reason': reason,
                          'authority': 'advisory; this view never authorizes execution'},
            'validity': 'Refresh after any source or service change; snapshot equality is not a lock.'}


def render(summary):
    """Compact text plus exact references; no universal reply-format requirement."""
    rows = ['RECOVERY SNAPSHOT (read-only, advisory)',
            'Requested outcome: ' + json.dumps(summary['task'].get('request'), sort_keys=True),
            'Original deadline (UTC epoch ms): ' + str(summary['task'].get('deadline_ms')),
            'Allowance: ' + json.dumps(summary['accounting'], sort_keys=True),
            'Next step: ' + summary['next_step']['code'] + ' — ' + summary['next_step']['reason']]
    rows.extend('Task evidence: ' + ref for ref in summary['task'].get('evidence', []))
    rows.append('Writer barrier: ' + json.dumps(summary['barrier'], sort_keys=True))
    for identity, attempt in sorted(summary['attempts'].items()):
        rows.append('Attempt ' + identity + ': ' + ', '.join(attempt['stages']))
        rows.extend('  evidence: ' + source for source in attempt['sources'])
    for obs in summary['observations']:
        rows.append('Observation: ' + json.dumps(obs, sort_keys=True))
    rows.extend('Problem: ' + p for p in summary['issues'])
    rows.append(summary['validity'])
    return '\n'.join(rows) + '\n'
