"""Replay investigation events and enforce storage invariants transactionally.

The workflow store owns these invariants so ordinary import cannot bypass them.
Semantic research judgments remain attributed claims, not machine-certified truth.
"""
from __future__ import annotations

from .errors import EvidenceError, PolicyDenied, WorkflowError
from .records import RecordRef, canonical, digest


def projection_key(root):
    return 'investigation:projection:' + digest(canonical(root.to_dict()))


def _require(condition, reason):
    if not condition:
        raise WorkflowError(reason)


def initial(root):
    p = root.payload
    return {'root_ref': root.ref.to_dict(), 'sequence': 0, 'tip_sha256': None,
            'at_ms': p['started_ms'], 'frame_seq': 0, 'frame': p['frame'],
            'status': 'active', 'questions': {}, 'actions': {}, 'acceptance': {},
            'conjectures': {}, 'decisions': {}, 'research': [], 'methods': [],
            'interpretations': {}, 'corrections': {},
            'grants': {'root': {'id': 'root', 'parent': None,
                'recipient': p['frame']['requester'], 'limits': p['limits'],
                'authority': p['authority'], 'deadline_ms': p['deadline_ms'],
                'spent': {}, 'held': {}, 'closed': False}}, 'overruns': []}


def remaining(grant):
    return {k: v - grant['spent'].get(k, 0) - grant['held'].get(k, 0)
            for k, v in grant['limits'].items()}


def _add(target, amounts, factor=1):
    for key, value in amounts.items():
        target[key] = target.get(key, 0) + factor * value
        _require(target[key] >= 0, 'negative resource ledger')


def _reserve(grant, amounts):
    _require(not grant['closed'], 'grant is closed')
    available = remaining(grant)
    _require(not set(amounts) - set(available), 'reservation uses an ungranted resource')
    _require(all(value <= available[key] for key, value in amounts.items()), 'grant exhausted')
    _add(grant['held'], amounts)


def _evidence_usable(store, items, *, current=True):
    """Resolve immutable selectors even when replay preserves older revisions."""
    if not items:
        return False
    for item in items:
        if 'sha256' in item:
            from .evidence import resolve_evidence
            if resolve_evidence(item, store).status != 'resolved':
                return False
        elif not store._applicability([RecordRef.from_dict(item)],
                historical=not current, corrected=None if current else set())['current']:
            return False
    return True


def audit_state(store, state, *, current=True):
    criteria = []
    frame_current = (_interpretation_current(store, state, state['frame']['inputs']) and
                     (not state['frame']['inputs'] or
                      _evidence_usable(store, state['frame']['inputs'], current=current)))
    for criterion in state['frame']['criteria']:
        a = state['acceptance'].get(criterion['key'])
        status = 'missing'
        if a:
            valid = (frame_current and a['frame_seq'] == state['frame_seq'] and
                     a['claim_type'] == criterion['claim_type'] and
                     bool(a['evidence']) and
                     _interpretation_current(store, state, a['evidence'] + a['inputs']) and
                     (_evidence_usable(store, a['evidence'], current=current) and
                      (not a['inputs'] or _evidence_usable(store, a['inputs'], current=current))))
            status = a['status'] if valid else 'unresolved'
        criteria.append({'key': criterion['key'], 'required': criterion['required'],
                         'status': status})
    pending = [q['id'] for q in state['questions'].values() if q['required'] and
               (q['state'] == 'pending' or (q['state'] == 'answered' and
                (q['frame_seq'] != state['frame_seq'] or
                 not _interpretation_current(store, state, q['answer']['evidence']) or
                 not _evidence_usable(store, q['answer']['evidence'], current=current))))]
    unresolved = [a['id'] for a in state['actions'].values()
                  if a['state'] in ('admitted', 'uncertain', 'interrupted')]
    grants = [k for k, g in state['grants'].items() if k != 'root' and not g['closed']]
    ready = (all(c['status'] == 'pass' for c in criteria if c['required'])
             and not pending and not unresolved and not grants and not state['overruns'])
    return {'ready': ready, 'criteria': criteria, 'frame_inputs_current': frame_current,
            'pending_questions': pending,
            'unsettled_actions': unresolved, 'open_grants': grants,
            'overruns': state['overruns'],
            'claim_limit': 'Evidence linkage and attributed acceptance, not independent truth verification.'}


def _interpretation_current(store, state, items, extra=()):
    """Replay checks the supersessions known at that point, not future events."""
    corrected = {RecordRef.from_dict(h['target'])
                 for v in state['interpretations'].values() for h in v['history']}
    corrected.update(extra)
    if not corrected:
        return True
    refs = [RecordRef.from_dict(item) for item in items if 'sha256' not in item]
    return store._applicability(refs, corrected=corrected, historical=True)['current']


def _prior_support(store, owner, payload, *, validated_sequence=0):
    """Same-run support precedes its claim, independent of import file order."""
    from .registry import record_references
    root = owner.ref if owner.ref.kind == 'investigation' else RecordRef.from_dict(owner.payload['root_ref'])
    sequence = 0 if owner.ref.kind == 'investigation' else owner.payload['sequence']
    pending, seen = list(record_references(payload)), set()
    while pending:
        ref = pending.pop()
        _require(ref != owner.ref, 'evidence cannot depend on its own claim')
        if ref in seen:
            continue
        seen.add(ref)
        record = store.get(ref)
        if record is None:
            # Existence is checked by publication; a missing future claim cannot
            # be admitted merely because its bytes will be supplied later.
            raise EvidenceError('missing evidence dependency')
        if ref.kind == 'investigation_event' and record.payload['root_ref'] == root.to_dict():
            _require(record.payload['sequence'] < sequence,
                     'same-run evidence must precede its claim')
            # Replay has already checked this earlier event and its complete
            # dependency graph. Rewalking it for every later claim is redundant.
            if record.payload['sequence'] <= validated_sequence:
                continue
        pending.extend(record_references(record.payload))
        pending.extend(store._investigation_dependencies(record))


def _resolved_support(store, items, event):
    """Check selectors even on historical import, without requiring latest inputs."""
    from .registry import artifact_references, record_references
    from .evidence import resolve_evidence
    pending, seen = list(items), set()
    while pending:
        item = pending.pop()
        if 'sha256' in item:
            _require(resolve_evidence(item, store).status == 'resolved',
                     'correction evidence does not resolve')
        else:
            ref = RecordRef.from_dict(item)
            if ref in seen:
                continue
            seen.add(ref)
            record = store.get(ref)
            _require(record is not None, 'correction evidence record is missing')
            if ref.kind == 'investigation_event' and record.payload['root_ref'] == event.payload['root_ref']:
                _require(record.payload['sequence'] < event.payload['sequence'],
                         'correction support must precede the correction')
            pending.extend(artifact_references(record.payload))
            pending.extend(r.to_dict() for r in record_references(record.payload))


def _correct(store, state, event, current):
    d = event.payload['data']
    target = store.get(RecordRef.from_dict(d['target']))
    _require(target is not None and target.ref.kind == 'investigation_event',
             'correction target must be an existing result or correction event')
    p = target.payload
    _require(p['root_ref'] == state['root_ref'], 'correction target belongs to another root')
    _require(p['sequence'] < event.payload['sequence'], 'correction target must precede correction')
    _require(p['event_type'] in ('result', 'correction'), 'correction target is not an interpretation')
    original = (target.ref.key if p['event_type'] == 'result'
                else state['corrections'].get(target.ref.key))
    view = state['interpretations'].get(original)
    _require(view is not None and view['current_ref'] == d['target'],
             'interpretation already corrected; recover and target its current correction')
    _require(bool(d['evidence']), 'correction needs supporting evidence')
    _resolved_support(store, d['evidence'], event)
    _require(_interpretation_current(store, state, d['evidence'], [target.ref]),
             'correction support depends on a superseded interpretation')
    _require(_evidence_usable(store, d['evidence'], current=current),
             'correction evidence is stale or invalid')
    view['history'].append({'ref': event.ref.to_dict(), **d})
    view.update(current_ref=event.ref.to_dict(), interpretation=d['interpretation'],
                source=d['source'], evidence=d['evidence'])
    state['corrections'][event.ref.key] = original


def apply(store, state, event, *, current=True):
    p = event.payload
    _require(p['root_ref'] == state['root_ref'], 'event belongs to another root')
    _require(p['sequence'] == state['sequence'] + 1, 'event sequence gap or collision')
    _require(p['previous_sha256'] == state['tip_sha256'], 'event predecessor mismatch')
    _require(p['at_ms'] >= state['at_ms'], 'event clock moved backwards')
    d, typ = p['data'], p['event_type']
    _prior_support(store, event, d, validated_sequence=state['sequence'])
    if state['status'] != 'active':
        reconciliation = (state['status'] in ('bound', 'blocked', 'stopped') and
                          typ in ('result', 'grant_close', 'question_close'))
        recovery = state['status'] in ('blocked', 'stopped') and typ == 'resume'
        _require(reconciliation or recovery or typ == 'correction', 'inactive investigation permits only reconciliation or authorized recovery')
    grants, questions, actions = state['grants'], state['questions'], state['actions']
    if p['at_ms'] >= grants['root']['deadline_ms'] and typ not in ('result', 'grant_close', 'question_close', 'finish', 'correction'):
        raise PolicyDenied('root deadline reached; only settlement and handoff remain')
    if typ in ('action', 'grant'):
        if p['at_ms'] >= grants['root']['deadline_ms']:
            raise PolicyDenied('root deadline reached')
        _require(not state['overruns'], 'observed overrun prevents further admission')
    if typ == 'resume':
        _require(state['status'] in ('blocked', 'stopped'), 'only blocked or stopped investigations resume')
        _require(d['source'] == state['frame']['requester'], 'recovery needs attributed requester direction')
        _require(bool(d['evidence']), 'recovery needs source evidence')
        _require(_interpretation_current(store, state, d['evidence']),
                 'recovery uses a corrected interpretation')
        _require(_evidence_usable(store, d['evidence'], current=current), 'recovery evidence is invalid or stale')
        state.update(status='active', recovery=d)
    elif typ == 'frame':
        _require(bool(d['basis']), 'frame revision needs evidence')
        old = state['frame']
        changed_intent = any(d['frame'][k] != old[k] for k in
                            ('subject', 'objective', 'requester', 'constraints', 'preferences', 'criteria'))
        _require(not changed_intent or d['source'] == old['requester'],
                 'material reframing needs attributed requester direction')
        state['frame'] = d['frame']
        state['frame_seq'] = p['sequence']
        state['acceptance'] = {}
    elif typ == 'question':
        _require(d['id'] not in questions, 'duplicate question identity')
        _require(d['frame_seq'] == state['frame_seq'], 'question uses stale frame')
        topic = ' '.join(d['topic'].casefold().split())
        _require(not any(q['state'] == 'pending' and q['topic_normalized'] == topic
                         for q in questions.values()), 'equivalent question already pending')
        _require(bool(d['attempts']) or bool(d['exemption']), 'question needs attempts or an exemption')
        if d['category'] == 'taste':
            _require(len(d['alternatives']) >= 2, 'taste question needs concrete alternatives')
        questions[d['id']] = {**d, 'state': 'pending', 'topic_normalized': topic}
    elif typ == 'answer':
        q = questions.get(d['question_id'])
        _require(q is not None and q['state'] == 'pending', 'question is not pending')
        _require(d['frame_seq'] == q['frame_seq'] == state['frame_seq'], 'late answer needs reconciliation')
        _require(d['respondent'] == q['recipient'], 'answer respondent does not match recipient')
        _require(bool(d['evidence']), 'answer needs attributable source evidence')
        _require(_interpretation_current(store, state, d['evidence']),
                 'answer uses a corrected interpretation')
        _require(_evidence_usable(store, d['evidence'], current=current), 'answer evidence is invalid or stale')
        _require(d['origin'] != 'simulated', 'simulated response cannot answer an actual question')
        q.update(state='answered', answer=d)
    elif typ == 'question_close':
        q = questions.get(d['question_id'])
        _require(q is not None and q['state'] in ('pending', 'answered'), 'question is already closed')
        q.update(state=d['state'], resolution_reason=d['reason'])
    elif typ == 'grant':
        _require(d['id'] not in grants, 'duplicate grant identity')
        parent = grants.get(d['parent'])
        _require(parent is not None, 'missing parent grant')
        _require(set(d['authority']) <= set(parent['authority']), 'child expands authority')
        _require(p['at_ms'] < d['deadline_ms'] <= parent['deadline_ms'], 'child deadline outside parent')
        limits = {**{key: 0 for key in parent['limits']}, **d['limits']}
        _reserve(parent, limits)
        grants[d['id']] = {**d, 'limits': limits, 'spent': {}, 'held': {}, 'closed': False}
    elif typ == 'grant_close':
        g = grants.get(d['id'])
        _require(g is not None and d['id'] != 'root' and not g['closed'], 'grant cannot close')
        _require(not any(a['grant'] == d['id'] and a['state'] in ('admitted', 'uncertain', 'interrupted')
                         for a in actions.values()), 'grant has unsettled actions')
        _require(not any(x['parent'] == d['id'] and not x['closed'] for x in grants.values()),
                 'grant has open children')
        parent = grants[g['parent']]
        _add(parent['held'], g['limits'], -1)
        _add(parent['spent'], g['spent'])
        g['closed'] = True
    elif typ == 'action':
        _require(d['id'] not in actions, 'duplicate action identity')
        g = grants.get(d['grant'])
        _require(g is not None, 'missing action grant')
        _require(p['at_ms'] < g['deadline_ms'], 'grant deadline reached')
        _require(set(d['authority']) <= set(g['authority']), 'action expands authority')
        if 'actions' in g['limits']:
            _require(d['reservation'].get('actions', 0) >= 1, 'admission must reserve at least one action')
        for key in d['questions']:
            q = questions.get(key)
            _require(q is not None and q['state'] == 'answered' and q['frame_seq'] == state['frame_seq'],
                     'action depends on an unanswered or stale question')
            _require(_interpretation_current(store, state, q['answer']['evidence']),
                     'action depends on corrected answer evidence')
            _require(_evidence_usable(store, q['answer']['evidence'], current=current),
                     'action depends on invalid or stale answer evidence')
        _require(_interpretation_current(store, state, d['inputs']),
                 'action inputs use a corrected interpretation')
        _require(not d['inputs'] or _evidence_usable(store, d['inputs'], current=current), 'action inputs are stale or invalid')
        _reserve(g, d['reservation'])
        actions[d['id']] = {**d, 'state': 'admitted', 'frame_seq': state['frame_seq']}
    elif typ == 'result':
        a = actions.get(d['action_id'])
        _require(a is not None and a['state'] in ('admitted', 'uncertain', 'interrupted'),
                 'action is absent or already settled')
        g = grants[a['grant']]
        _require(set(d['usage']) <= set(a['reservation']), 'usage introduces an unreserved dimension')
        prior = a.get('result', {}).get('usage', {})
        _require(all(d['usage'].get(k, 0) >= v for k, v in prior.items()), 'observed usage cannot disappear')
        if d['status'] in ('succeeded', 'failed'):
            _require(set(d['usage']) == set(a['reservation']), 'final result needs observed usage for every reservation')
            _require(bool(d['evidence']), 'settled result needs an execution receipt')
            # Recording an effect may legitimately use older evidence. It still
            # needs actual selected content before the reservation is released.
            _require(_evidence_usable(store, d['evidence'], current=False),
                     'settled result needs a resolvable execution receipt')
            _add(g['held'], a['reservation'], -1)
            _add(g['spent'], d['usage'])
        # Uncertain/interrupted execution retains its full reservation.
        for k, value in d['usage'].items():
            if value > a['reservation'][k]:
                state['overruns'].append({'action': a['id'], 'resource': k,
                                          'reserved': a['reservation'][k], 'observed': value})
        a.update(state=d['status'], result=d)
        state['interpretations'][event.ref.key] = {
            'original_ref': event.ref.to_dict(), 'current_ref': event.ref.to_dict(),
            'action_id': d['action_id'], 'interpretation': d['observation'],
            'source': d['producer'], 'evidence': d['evidence'], 'history': []}
    elif typ == 'correction':
        _correct(store, state, event, current)
    elif typ == 'acceptance':
        criterion = next((c for c in state['frame']['criteria'] if c['key'] == d['criterion']), None)
        _require(criterion is not None and d['frame_seq'] == state['frame_seq'], 'acceptance uses unknown criterion or stale frame')
        _require(d['claim_type'] == criterion['claim_type'], 'acceptance claim type mismatch')
        if d['status'] == 'pass':
            _require(bool(d['evidence']), 'passing acceptance needs evidence')
            _require(_interpretation_current(store, state, d['evidence'] + d['inputs']),
                     'passing acceptance uses a corrected interpretation')
            _require(_evidence_usable(store, d['evidence'], current=current), 'passing acceptance needs usable evidence')
            _require(not d['inputs'] or _evidence_usable(store, d['inputs'], current=current), 'acceptance inputs are stale or invalid')
        state['acceptance'][d['criterion']] = d
    elif typ == 'finish':
        if d['status'] == 'complete':
            _require(p['at_ms'] < grants['root']['deadline_ms'], 'completion is past the bound')
            _require(audit_state(store, state, current=current)['ready'], 'investigation acceptance is unmet')
        if d['status'] == 'bound':
            _require(p['at_ms'] >= grants['root']['deadline_ms'], 'bound has not been reached')
        state.update(status=d['status'], finish=d)
    elif typ == 'research':
        state['research'].append(d)
    elif typ == 'conjecture':
        _require(d['id'] not in state['conjectures'], 'duplicate conjecture identity')
        state['conjectures'][d['id']] = d
    elif typ == 'decision':
        _require(d['id'] not in state['decisions'], 'duplicate decision identity')
        state['decisions'][d['id']] = d
    elif typ == 'methods':
        state['methods'].append(d)
    else:
        raise WorkflowError('unsupported transition')
    state.update(sequence=p['sequence'], tip_sha256=event.sha256, at_ms=p['at_ms'])


def rebuild(store, root_ref, *, persist=False, current_events=()):
    root = store.get(root_ref)
    if root is None or root.ref.kind != 'investigation':
        raise EvidenceError('investigation root not found')
    state = initial(root)
    _prior_support(store, root, root.payload['frame']['inputs'])
    events = [r for r in store.heads('investigation_event', root_ref.study_scope)
              if r.payload['root_ref'] == root_ref.to_dict()]
    events.sort(key=lambda r: r.payload['sequence'])
    from .registry import record_references, artifact_references
    for record in [root, *events]:
        for ref in record_references(record.payload):
            if store.get(ref) is None:
                raise EvidenceError('missing historical dependency')
        for ref in artifact_references(record.payload):
            store.read_artifact(ref)
    for event in events:
        apply(store, state, event, current=event.ref in current_events)
    if persist:
        store.db.execute('INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                         (projection_key(root_ref), canonical(state)))
    return state
