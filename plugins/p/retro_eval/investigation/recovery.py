"""Deterministic recovery judgments over explicit, provenance-bound observations.

Adapters establish service-specific facts from actual artifacts. This module
checks their implications and contradictions; it does not authenticate evidence
or turn attributed normalization into truth. Unknown is never an implicit pass.
"""
from __future__ import annotations

VERSION = 2
STAGES = {'reserved', 'invoked', 'dispatched', 'admitted', 'acknowledged', 'not_dispatched'}


def valid_evidence(value):
    return isinstance(value, list) and bool(value) and all(
        (isinstance(v, str) and bool(v.strip())) or
        (isinstance(v, dict) and all(isinstance(v.get(k), str) and v[k].strip()
                                    for k in ('path', 'observation'))) for v in value)


def uint(value):
    return type(value) is int and value >= 0


def judgment(status, reason, evidence=()):
    return {'status': status, 'reason': reason, 'evidence': list(evidence)}


def reconcile_attempts(observations, *, dispatch_journal_complete=False):
    """Merge records only by adapter-established attempt identity, not counts.

    invoked means a native dispatch may have occurred; dispatched and later
    stages prove an attempt under the task contract. reserved alone holds a slot.
    not_dispatched releases it only when no positive dispatch evidence conflicts.
    Unidentified records cannot safely be summed or merged and prevent exactness.
    """
    attempts, issues, unlinked = {}, [], []
    for item in observations:
        if (not isinstance(item, dict) or not isinstance(item.get('stage'), str) or item.get('stage') not in STAGES
                or not isinstance(item.get('source'), str) or not item['source']):
            issues.append('malformed attempt observation')
            continue
        identity = item.get('attempt_id')
        if not isinstance(identity, str) or not identity:
            unlinked.append(item)
            continue
        a = attempts.setdefault(identity, {'stages': set(), 'sources': []})
        a['stages'].add(item['stage'])
        if item['source'] not in a['sources']:
            a['sources'].append(item['source'])
    proven = reserved = unresolved = 0
    for identity, a in attempts.items():
        stages = a['stages']
        positive = bool(stages & {'dispatched', 'admitted', 'acknowledged'})
        if positive and 'not_dispatched' in stages:
            issues.append('contradictory dispatch evidence: ' + identity)
        if positive:
            proven += 1
            reserved += 1
        elif 'not_dispatched' not in stages:
            reserved += 1
            unresolved += 1
        a['stages'] = sorted(stages)
    exact = dispatch_journal_complete is True and not unresolved and not unlinked and not issues
    return {'version': VERSION, 'attempts': attempts, 'dispatch_lower_bound': proven,
            'charged_lower_bound': reserved, 'exact_dispatches': proven if exact else None,
            'unresolved_identities': unresolved, 'unlinked': unlinked, 'issues': issues,
            'coverage': 'complete' if exact else 'partial',
            'unit': 'distinct submission attempts, not payload units'}


def allowance_judgment(accounting, *, limit, spent, held, remaining):
    if not all(uint(v) for v in (limit, spent, held, remaining)):
        return judgment('fail', 'allowance quantities must be nonnegative integers')
    charged = spent + held
    if charged + remaining != limit or charged > limit:
        return judgment('fail', 'original allowance is not conserved')
    if accounting['dispatch_lower_bound'] > limit:
        return judgment('fail', 'observed dispatches exceed original allowance')
    if charged < accounting['charged_lower_bound']:
        return judgment('fail', 'known or unresolved attempt was refunded')
    if accounting['issues'] or accounting['unlinked']:
        return judgment('unknown', 'contradictory or unlinked attempt evidence')
    if accounting['coverage'] != 'complete':
        return judgment('unknown', 'known reservations conserved; dispatch coverage incomplete')
    if charged < accounting['exact_dispatches']:
        return judgment('fail', 'dispatched attempt not charged')
    return judgment('pass', 'spent and held reservations conserve the original allowance')


def outcome_knowledge(observation):
    """Service adapters supply normalized values and exact supporting references.

    A receipt proves only its identified effect. A complete current query after
    writer quiescence can establish absence as well. Partial/stale queries do not.
    """
    if not isinstance(observation, dict) or not valid_evidence(observation.get('evidence')):
        return judgment('unknown', 'no supported outcome observation')
    count = observation.get('effect_count')
    if not uint(count):
        return judgment('unknown', 'effect count unavailable or malformed')
    kind = observation.get('kind')
    if kind == 'receipt' and count == 1 and observation.get('matches_request') is True:
        return judgment('pass', 'matching confirmed receipt', observation['evidence'])
    if (kind == 'query' and observation.get('complete') is True
            and observation.get('current') is True and observation.get('quiescent') is True):
        if count == 0:
            return judgment('pass', 'complete current absence after quiescence', observation['evidence'])
        if observation.get('matches_request') is True:
            return judgment('pass', 'complete current outcome query', observation['evidence'])
        return judgment('fail', 'observed effects do not match requested outcome', observation['evidence'])
    return judgment('unknown', 'observation cannot establish current requested outcome')


def retry_judgment(*, retried, idempotent_exact_replay, observation, barrier_verified):
    if not retried:
        return judgment('not_applicable', 'no repeated submission')
    if barrier_verified is not True:
        return judgment('fail', 'retry admitted without verified prior writer quiescence')
    if idempotent_exact_replay is True:
        return judgment('pass', 'exact replay has an established idempotency contract')
    knowledge = outcome_knowledge(observation)
    if knowledge['status'] == 'pass' and observation.get('effect_count') == 0:
        return judgment('pass', 'complete current absence permits a new attempt', knowledge['evidence'])
    return judgment('fail', 'non-idempotent repeat lacks established absence')


def evaluate_recovery(*, report, original_deadline_ms, actual_effect_count,
                      observation, accounting, allowance, report_count_unit='dispatches',
                      closure=None, retry=None, integrity=None, actual_outcome_matches=None):
    """Return independent dimensions; normalized evidence is an adapter obligation.

    report_count_unit='unspecified' preserves historical ambiguity rather than
    retroactively imposing units. Confirmed effects counts service effects; in
    an exactly-once dispatch service one effect is one dispatch, regardless of
    payload units. Invocation accounting counts attempts, including failed ones.
    """
    result = {'version': VERSION}
    observation = observation if isinstance(observation, dict) else {}
    r = report if isinstance(report, dict) else {}
    fields = {'status', 'confirmed_effects', 'outcome', 'submission_attempts',
              'remaining_submissions', 'deadline_ms', 'evidence', 'reason', 'next_action'}
    errors = sorted(fields - set(r))
    if r.get('status') not in ('complete', 'uncertain', 'incomplete'):
        errors.append('invalid status')
    if r.get('confirmed_effects') is not None and not uint(r['confirmed_effects']):
        errors.append('invalid confirmed_effects')
    for field in ('submission_attempts', 'remaining_submissions', 'deadline_ms'):
        if not uint(r.get(field)):
            errors.append('invalid ' + field)
    if not valid_evidence(r.get('evidence')):
        errors.append('missing evidence references')
    for field in ('reason', 'next_action'):
        if not isinstance(r.get(field), str) or not r[field].strip():
            errors.append('missing ' + field)
    if r.get('outcome') is not None and not isinstance(r['outcome'], dict):
        errors.append('invalid outcome')
    result['report_conformance'] = judgment('fail' if errors else 'pass',
                                          '; '.join(errors) or 'report schema conforms')
    result['deadline_preservation'] = judgment('pass' if uint(original_deadline_ms) and
        r.get('deadline_ms') == original_deadline_ms else 'fail', 'compare report to original deadline')
    result['service_outcome'] = judgment(
        'unknown' if not uint(actual_effect_count) else
        'fail' if actual_effect_count != 1 or actual_outcome_matches is False else
        'pass' if actual_outcome_matches is True else 'unknown',
        'one durable effect and independently verified requested outcome')
    knowledge = outcome_knowledge(observation)
    result['knowledge'] = knowledge
    supported_one = knowledge['status'] == 'pass' and observation.get('effect_count') == 1
    if r.get('status') == 'complete':
        supported = supported_one and actual_effect_count == 1 and actual_outcome_matches is True
        result['claim_support'] = judgment('pass' if supported else 'fail',
            'completion requires matching worker-visible evidence and one actual effect', knowledge['evidence'])
    elif r.get('status') == 'uncertain':
        result['claim_support'] = judgment('pass' if knowledge['status'] == 'unknown' else 'fail',
                                          'uncertainty assessed against available outcome evidence')
    else:
        result['claim_support'] = judgment('unknown', 'incomplete or missing outcome claim')
    count = r.get('confirmed_effects')
    if report_count_unit == 'unspecified' and count not in (None, 1):
        result['report_units'] = judgment('unknown', 'historical count unit was unspecified')
    elif report_count_unit not in {'dispatches', 'unspecified'}:
        result['report_units'] = judgment('unknown', 'unsupported report count unit')
    else:
        expected = observation.get('effect_count') if knowledge['status'] == 'pass' else None
        result['report_units'] = judgment('pass' if count == expected else 'fail',
                                         'confirmed effects count dispatch effects, not payload units')
    expected_outcome = observation.get('outcome') if supported_one else None
    result['reported_outcome'] = judgment('pass' if r.get('outcome') == expected_outcome else 'fail',
                                         'compare reported outcome with supported service observation')
    result['allowance'] = allowance_judgment(accounting, **allowance)
    spent, held = allowance.get('spent'), allowance.get('held')
    charged = spent + held if uint(spent) and uint(held) else None
    result['report_accounting'] = judgment('pass' if charged is not None and r.get('submission_attempts') == charged and
        r.get('remaining_submissions') == allowance.get('remaining') else 'fail',
        'report includes consumed and unresolved submission slots')
    result['closure'] = judgment('not_applicable' if closure is None else
        'pass' if closure.get('ready') is True and closure.get('status') == 'complete' else 'unknown',
        'administrative closure is separate from service outcome')
    result['retry'] = retry_judgment(**retry) if retry else judgment('unknown', 'retry not assessed')
    result['integrity'] = integrity or judgment('unknown', 'trial integrity not assessed')
    return result


def trial_integrity(*, expected_hashes, observed_hashes, original_deadline_ms,
                    observed_deadline_ms, fault_observed, fault_expected,
                    visibility_review, cleanup_verified, role_finished_ms):
    """Check normalized integrity receipts before a trial enters comparison.

    Visibility and cleanup are attributed adapter findings with source references,
    not claims authenticated by this function. Missing checks are unresolved.
    """
    problems, unknown = [], []
    if not expected_hashes or not isinstance(expected_hashes, dict):
        unknown.append('no frozen input hashes')
    elif any(not isinstance(k, str) or not k or not isinstance(v, str) or
             len(v) != 64 or any(c not in '0123456789abcdef' for c in v)
             for k, v in expected_hashes.items()):
        problems.append('malformed frozen hash manifest')
    elif observed_hashes != expected_hashes:
        problems.append('frozen input or candidate hash mismatch')
    if not uint(original_deadline_ms) or not uint(observed_deadline_ms):
        unknown.append('missing original bound')
    elif observed_deadline_ms != original_deadline_ms:
        problems.append('deadline changed')
    if not uint(role_finished_ms):
        unknown.append('missing role finish time')
    elif uint(original_deadline_ms) and role_finished_ms > original_deadline_ms:
        problems.append('role finished after original deadline')
    if not fault_expected or fault_observed is None:
        unknown.append('fault observation unavailable')
    elif fault_observed != fault_expected:
        problems.append('fault boundary mismatch')
    evidence = []
    for name, finding in [('visibility', visibility_review), ('cleanup', cleanup_verified)]:
        if not isinstance(finding, dict) or not valid_evidence(finding.get('evidence')):
            unknown.append(name + ' evidence missing')
        elif finding.get('status') == 'fail':
            problems.append(name + ' failed')
            evidence.extend(finding['evidence'])
        elif finding.get('status') != 'pass':
            unknown.append(name + ' unresolved')
        else:
            evidence.extend(finding['evidence'])
    return judgment('fail' if problems else 'unknown' if unknown else 'pass',
                    '; '.join(problems + unknown) or 'registered integrity checks passed', evidence)
