"""Reversible local dispositions and bounded recurring attention, without scheduling."""
from __future__ import annotations

from .errors import WorkflowError
from .records import Record, canonical, digest, integer, timestamp, utc_now
from .pilot_followup import SCOPE as PILOT_SCOPE

STATES = {'active', 'snoozed', 'dismissed', 'provisional', 'adopted', 'retired'}


def attention_decisions(store):
    """Freeze local attention decisions and explicitly dated custom reviews.

    A pilot disposition is a judgment, not an operational suppression state.
    Undated and unreviewed pilots remain visible through status; do not invent
    a deadline or execute their next action here.
    """
    return [*store.heads(kind='decision', study_scope='recurring'),
            *(r for r in store.heads(kind='decision', study_scope=PILOT_SCOPE)
              if r.payload.get('due_at'))]


def disposition(store, candidate_key, state, *, reason, due_at=None,
                next_action='Reassess when the evidence or context changes.',
                evidence_refs=None, created_at=None):
    if state not in STATES or not isinstance(reason, str) or not reason.strip():
        raise WorkflowError('disposition requires a supported state and reason')
    if state == 'snoozed' and due_at is None:
        raise WorkflowError('snooze requires a review time')
    if due_at is not None:
        timestamp(due_at)
    heads = [r for r in store.heads(kind='decision', study_scope='recurring') if r.ref.key == candidate_key]
    previous = heads[0] if heads else None
    payload = {'disposition': 'insufficient_evidence', 'benefit_status': 'unknown',
        'quality_status': 'unknown', 'analysis_refs': [], 'counterevidence_refs': [],
        'rationale': reason, 'next_action': {'owner': 'agent', 'action': next_action,
            'prerequisites': 'new evidence or the declared review trigger',
            'acceptance': 'Record the evidence-backed follow-up decision.'},
        'operational_status': state, 'candidate_key': candidate_key, 'due_at': due_at,
        'evidence_refs': evidence_refs or [], 'scope': 'local_framework_attention_only',
        'global_workflow_changed': False}
    if previous and previous.payload == payload:
        return previous
    record = Record.create('decision', candidate_key, payload, study_scope='recurring',
        revision=previous.ref.revision + 1 if previous else 1, created_at=created_at or utc_now())
    store.append(record, previous.ref.revision if previous else None)
    return record


def local_attention(record):
    """Interpret optional local fields without mutating portable decisions."""
    payload = record.payload
    state = payload.get('operational_status', 'unknown')
    candidate_key = payload.get('candidate_key', record.ref.key)
    metadata_issues = []
    if 'operational_status' in payload and (
            not isinstance(state, str) or state not in STATES):
        metadata_issues.append({'field': 'operational_status',
                                'reason': 'unsupported_local_attention_state'})
        state = 'unknown'
    if not isinstance(candidate_key, str) or not candidate_key.strip():
        metadata_issues.append({'field': 'candidate_key',
                                'reason': 'nonempty_string_required'})
        candidate_key = None
    return state, candidate_key, metadata_issues


def followups(store, *, now=None):
    """Expose local attention without consuming cards or authorizing execution.

    A due date establishes readiness to review, not a verified outcome or a
    satisfied execution prerequisite. Undated triggers remain unknown.
    """
    current = timestamp(now or utc_now())
    with store.read_snapshot():
        result = []
        for record in store.heads(kind='decision', study_scope='recurring'):
            payload = record.payload
            # Portable decisions validate the common contract, not these local
            # extensions. Classify malformed metadata without changing records
            # or letting arbitrary JSON values imply review readiness.
            state, candidate_key, metadata_issues = local_attention(record)
            due_at = payload.get('due_at')
            due = timestamp(due_at) <= current if due_at else None
            applicability = store.applicability([record.ref])
            recorded_action = payload['next_action']
            if not applicability['current']:
                attention = 'needs_reconciliation'
                action = {
                    'owner': recorded_action['owner'],
                    'action': 'Reconcile missing or superseded evidence before acting on this local decision.',
                    'prerequisites': 'Inspect current_applicability reasons and exact retained references; preserve the recorded decision.',
                    'acceptance': 'Restore exact evidence or explicitly review corrected inputs without promoting unknown outcomes.'}
            elif metadata_issues:
                attention = 'invalid_metadata'
                action = {
                    'owner': recorded_action['owner'],
                    'action': 'Review invalid local attention metadata before using this decision to select work.',
                    'prerequisites': 'Inspect attention_metadata_issues and the exact decision reference; preserve the original record.',
                    'acceptance': 'Record an explicit corrected decision if authorized; retain unknown outcomes and existing execution authority.'}
            else:
                action = recorded_action
                if state in ('dismissed', 'retired'):
                    attention = 'inactive'
                elif state not in STATES or due is None:
                    attention = 'trigger_unknown'
                else:
                    attention = 'ready_for_review' if due else 'waiting_for_due_time'
            result.append({
                'decision_ref': record.ref.to_dict(),
                'candidate_key': candidate_key,
                'attention_metadata_issues': metadata_issues,
                'operational_status': state,
                'attention_status': attention,
                'due_at': due_at, 'due': due,
                'disposition': payload['disposition'],
                'benefit_status': payload['benefit_status'],
                'quality_status': payload['quality_status'],
                'evidence_refs': payload['evidence_refs'],
                'current_applicability': applicability,
                **({'recorded_next_action': recorded_action}
                   if not applicability['current'] or metadata_issues else {}),
                'next_action': action,
                'scope': 'local_framework_attention_only',
                'global_workflow_changed': False})
        return result


def select_cards(candidates, decisions, *, now, seen_candidates=(), seen_followups=(), limit=3):
    integer(limit, 'card limit', 1)
    current = timestamp(now)
    local = {r.ref: local_attention(r) for r in decisions if r.ref.study_scope == 'recurring'}
    diagnostics = [{'reason': 'local_attention_metadata_unusable',
                    'decision_ref': r.ref.to_dict(),
                    'attention_metadata_issues': local[r.ref][2],
                    'operational_status': local[r.ref][0]}
                   for r in decisions if r.ref in local and
                   (local[r.ref][2] or local[r.ref][0] not in STATES)]
    decisions = [r for r in decisions if r.ref not in local or
                 (not local[r.ref][2] and local[r.ref][0] in STATES)]
    states = {local[r.ref][1]: r for r in decisions if r.ref in local}
    seen, shown_followups = set(seen_candidates), set(seen_followups)
    cards, followup_tokens = [], []
    # Severe evidence and due actions precede ordinary discovery. A known
    # candidate with genuinely changed evidence has a new semantic key.
    severe = [c for c in candidates if c.get('severe_verified') is True and c['key'] not in seen]
    for candidate in sorted(severe, key=lambda c: c['key']):
        if len(cards) < limit:
            cards.append(candidate)
    due = [r for r in decisions if r.ref.study_scope in ('recurring', PILOT_SCOPE)
           and r.payload.get('due_at') and
           timestamp(r.payload['due_at']) <= current and
           (r.ref.study_scope != 'recurring' or
            r.payload['operational_status'] not in ('dismissed', 'retired'))]
    due.sort(key=lambda r: (timestamp(r.payload['due_at']), r.ref.study_scope, r.ref.key))
    for record in due:
        token = digest(canonical([record.ref.to_dict(), record.payload['due_at']]))[:24]
        if token in shown_followups or len(cards) >= limit:
            continue
        cards.append({'key': 'followup:' + token, 'category': 'due_followup',
            'decision_ref': record.ref.to_dict(), 'candidate_key': local[record.ref][1] if record.ref in local else record.payload['candidate_key'],
            'fact': 'The recorded follow-up time has arrived.',
            'status': 'pending_action', 'benefit_status': record.payload['benefit_status'],
            'positive_practice': False, 'priority': 3,
            'next_action': record.payload['next_action']['action'],
            'owner': record.payload['next_action']['owner'],
            'prerequisites': record.payload['next_action']['prerequisites'],
            'acceptance': record.payload['next_action']['acceptance'],
            'evidence_refs': record.payload.get('evidence_refs', [])})
        followup_tokens.append(token)
    eligible = []
    for candidate in candidates:
        if candidate['key'] in seen or candidate in cards:
            continue
        record = states.get(candidate['key'])
        if record:
            state = record.payload['operational_status']
            if state in ('dismissed', 'retired', 'adopted', 'snoozed'):
                continue
        eligible.append(candidate)
    eligible.sort(key=lambda c: (-c.get('priority', 0), c['key']))
    positive = next((c for c in eligible if c.get('positive_practice')), None)
    if positive and len(cards) + 1 < limit:
        cards.append(positive)
    for candidate in eligible:
        if len(cards) >= limit:
            break
        if candidate not in cards:
            cards.append(candidate)
    return {'cards': cards, 'followup_tokens': followup_tokens,
            'attention_diagnostics': diagnostics,
            'eligible_new_candidates': len(eligible), 'available_candidates': len(candidates),
            'next_action': 'Agent reviews the displayed evidence and records a scoped disposition.' if cards
                           else 'No new review is needed until evidence or a follow-up trigger changes.'}
