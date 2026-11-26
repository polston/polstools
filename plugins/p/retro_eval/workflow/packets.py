"""Bounded, deterministic, private case packets and delivery accounting."""
from __future__ import annotations

import html
import math

from ..text_rules import redact
from .evidence import resolve_evidence
from .errors import WorkflowError
from .records import evaluate_delivery, integer
from .verification import verify_outcome


def excerpt(value, limit):
    integer(limit, 'excerpt limit', 1)
    value = redact(str(value))
    shown = value[:limit]
    return {'text': shown, 'displayed_chars': len(shown),
            'available_chars': len(value), 'truncated': len(value) > limit}


def make_packet(store, cases, *, case_limit=20, excerpt_limit=1200):
    with store.read_snapshot():
        return _make_packet(store, cases, case_limit=case_limit, excerpt_limit=excerpt_limit)


def _make_packet(store, cases, *, case_limit, excerpt_limit):
    integer(case_limit, 'case limit', 1)
    integer(excerpt_limit, 'excerpt limit', 1)
    cases = sorted(cases, key=lambda r: (r.ref.study_scope, r.ref.key, r.ref.revision))
    outcomes = store.heads(kind='outcome')
    items = []
    for case in cases[:case_limit]:
        evidence_valid = lambda ref: resolve_evidence(ref, store).status == 'resolved'
        checks = [r for r in outcomes if r.payload['case_ref'] == case.ref.to_dict()]
        result = evaluate_delivery(case, checks, evidence_valid,
                                   lambda c, criterion, o: verify_outcome(c, criterion, o, store))
        # Current interpretation must also check the outcome revision's own
        # dependencies; historical stored packets keep their exact input refs.
        status_refs = [case.ref] + [r.ref for r in checks]
        current = store.applicability(status_refs)
        items.append({'case_ref': case.ref.to_dict(),
            'intent': excerpt(case.payload['intent'], excerpt_limit),
            'contract': display_contract(case.payload['contract'], excerpt_limit),
            'delivery': {'status': result.status if current['current'] else 'unknown',
                'reason': result.reason if current['current'] else 'invalid_or_superseded_input',
                'criteria': [[excerpt(key, excerpt_limit), state] for key, state in result.criteria]},
            'input_refs': [ref.to_dict() for ref in status_refs],
            'current_applicability': current,
            'evidence_refs': case.payload.get('evidence_refs', []),
            'independent_unit_eligible': case.payload.get('independent_unit_eligible', False),
            'limitations': [excerpt(value, excerpt_limit) for value in case.payload.get('limitations', [])[:50]]})
    return {'packet_format': 1, 'content_class': 'private_redacted',
        'selected': len(cases), 'displayed': len(items),
        'omitted': max(0, len(cases) - len(items)), 'case_limit': case_limit,
        'excerpt_limit': excerpt_limit, 'population_claim': False, 'cases': items}


def display_contract(contract, limit):
    criteria = contract['criteria']
    return {'version': contract['version'], 'criteria_available': len(criteria),
            'criteria_omitted': max(0, len(criteria) - 50),
            'criteria': [{'key': excerpt(c['key'], limit),
                          'description': excerpt(c['description'], limit),
                          'verification_method': excerpt(c['verification_method'], limit),
                          'required': c['required']} for c in criteria[:50]]}


def resource_summary(total, delivered_count):
    integer(delivered_count, 'delivered count')
    if total is not None and (type(total) not in (int, float) or total < 0 or
                              (isinstance(total, float) and not math.isfinite(total))):
        raise WorkflowError('invalid total resource use')
    return {'total': total, 'verified_deliveries': delivered_count,
            'per_delivery': total / delivered_count if total is not None and delivered_count else None,
            'reason': ('resource_unavailable' if total is None else
                       'zero_verified_deliveries' if not delivered_count else '')}


def render_html(packet):
    """No executable transcript markup or script is included in this renderer."""
    rows = []
    for case in packet['cases']:
        rows.append('<article><h2>' + html.escape(case['case_ref']['key']) + '</h2><p>' +
                    html.escape(case['intent']['text']) + '</p><p>Delivery: ' +
                    html.escape(case['delivery']['status']) + ' — ' +
                    html.escape(case['delivery']['reason']) + '</p></article>')
    return ('<!doctype html><html><meta charset="utf-8"><title>Private workflow evidence</title>'
            '<body><h1>Private workflow evidence</h1><p>Shown %s of %s selected cases. '
            'This is not a population estimate.</p>%s</body></html>' %
            (packet['displayed'], packet['selected'], ''.join(rows)))
