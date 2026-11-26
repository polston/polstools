"""Evidence-bound registration and attributed follow-up for custom local pilots.

This boundary records an agent's study and review; it never executes a proposed
intervention or converts an authored judgment into measured efficacy. Existing
pilot, analysis and decision record formats remain unchanged.
"""
from __future__ import annotations

from .errors import EvidenceError, RevisionConflict, WorkflowError
from .evidence import resolve_evidence
from .records import Record, RecordRef, canonical, digest, integer, strict_loads, text, timestamp, utc_now
from .registry import artifact_references, external_path, record_references
from .runner import Runner

SCOPE = 'candidate-pilots'


def capture_evidence(store, path, sha256, *, max_bytes=8 * 1024 * 1024):
    """Capture an explicitly supplied external artifact, never discover a corpus."""
    integer(max_bytes, 'evidence byte limit', 1)
    with external_path(path).open('rb') as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise EvidenceError('evidence exceeds the declared byte limit')
    if digest(data) != sha256:
        raise EvidenceError('evidence fingerprint mismatch')
    return {'artifact_ref': store.publish_artifact(data, 'private_raw'),
            'bytes': len(data), 'executed': False}


def _object(value, field):
    if not isinstance(value, dict):
        raise WorkflowError(field + ' must be an object')
    return value


def _references(store, refs, field, *, required=False):
    if not isinstance(refs, list) or required and not refs:
        raise EvidenceError(field + ' requires an evidence reference list')
    for ref in refs:
        if not isinstance(ref, dict) or resolve_evidence(ref, store).status != 'resolved':
            raise EvidenceError(field + ' contains unresolved evidence')


def _current(store, payload):
    for ref in artifact_references(payload):
        if resolve_evidence(ref, store).status != 'resolved':
            raise EvidenceError('pilot input contains unresolved evidence')
    refs = list(record_references(payload))
    # Applicability joins the publication transaction when present.
    applicability = store.applicability(refs)
    if not applicability['current']:
        raise EvidenceError('pilot input contains stale or unavailable records')


def _candidate(store, inspection_ref, key):
    if inspection_ref.get('selector', {'kind': 'whole'}) != {'kind': 'whole'}:
        raise WorkflowError('inspection reference must select the whole packet')
    data, _ = store.read_artifact(inspection_ref)
    packet = _object(strict_loads(data), 'inspection packet')
    if packet.get('inspection_format') != 1:
        raise WorkflowError('unsupported inspection packet')
    entries = packet.get('entries')
    contrasts = packet.get('noncandidate_contrasts')
    if not isinstance(entries, list) or not isinstance(contrasts, list):
        raise WorkflowError('inspection entries and contrasts must be lists')
    candidates = [_object(_object(entry, 'inspection entry').get('candidate'),
                          'inspection candidate') for entry in entries]
    selected = [candidate for candidate in candidates if candidate.get('key') == key]
    if len(selected) != 1:
        raise WorkflowError('candidate must occur exactly once in the inspection')
    candidate = selected[0]
    selection_ref = RecordRef.from_dict(packet.get('selection_ref'))
    selection = store.get(selection_ref)
    if selection is None or selection.ref.kind != 'selection' or selection.payload['purpose'] != 'discovery':
        raise EvidenceError('inspection requires its discovery selection')
    runner = Runner(store)
    run_id = text(packet.get('run_id'), 'inspection run')
    run = runner.status(run_id)
    if not runner.result_applicability(run_id)['current']:
        raise EvidenceError('inspection run is no longer applicable')
    outputs = []
    for op in run['operations']:
        if op['state'] == 'completed' and op['unit_key'].startswith('source:'):
            ref = op['result']['artifact_ref']
            outputs.append((ref, strict_loads(store.read_artifact(ref)[0])))
    # Reconstruct displayed dependencies from frozen operations. Do not trust
    # packet case_refs or a separately supplied discovery selection to identify
    # the source records: references nested inside artifact bytes are not graph
    # edges in the registry.
    displayed, sources = {}, []
    for item in candidates:
        matches = [(ref, output) for ref, output in outputs
                   if any(item in pack.get('candidates', [])
                          for pack in output.get('packs', []))]
        if not matches:
            raise EvidenceError('candidate differs from the frozen source operation')
        for ref, output in matches:
            displayed[canonical(ref)] = output
            if item == candidate:
                sources.append(ref)
    for contrast in contrasts:
        _object(contrast, 'inspection contrast')
        matched = False
        for ref, output in outputs:
            if (contrast.get('source'), contrast.get('source_key')) != (
                    output.get('source'), output.get('source_key')) or not output.get('included'):
                continue
            excerpts_ref = output['excerpts_ref']
            excerpts = strict_loads(store.read_artifact(excerpts_ref)[0])
            expected = [{**excerpts_ref, 'selector': {'kind': 'json_pointer',
                         'pointer': '/prompts/' + span}} for span in excerpts.get('prompts', {})]
            if contrast.get('reference') in expected:
                displayed[canonical(ref)] = output
                matched = True
        if not matched:
            raise EvidenceError('inspection contrast differs from the frozen source operation')
    records = {canonical(ref): ref for output in displayed.values()
               for ref in output['record_refs']}
    expected_cases = {encoded for encoded, ref in records.items() if ref['kind'] == 'case'}
    supplied_cases = {canonical(ref) for ref in selection.payload['selected_refs']}
    if supplied_cases != expected_cases:
        raise EvidenceError('discovery selection does not match the inspected source cases')
    _references(store, candidate['evidence_refs'], 'candidate evidence', required=True)
    return candidate, selection, sources, [records[k] for k in sorted(records)]


def register(store, spec):
    _object(spec, 'registration')
    if type(spec.get('registration_format')) is not int or spec['registration_format'] != 1:
        raise WorkflowError('unsupported pilot registration format')
    candidate, selection, sources, source_records = _candidate(store,
        _object(spec.get('inspection_ref'), 'inspection_ref'), text(spec.get('candidate_key'), 'candidate_key'))
    justification = _object(spec.get('justification'), 'justification')
    for name in ('interpretation', 'rationale', 'scope', 'counterevidence_search', 'possible_harm'):
        text(justification.get(name), 'justification.' + name)
    if justification.get('reviewer_type') not in ('agent', 'human'):
        raise WorkflowError('justification requires attributed agent or human review')
    _references(store, justification.get('evidence_refs'), 'justification evidence', required=True)
    _references(store, justification.get('counterevidence_refs'), 'counterevidence')
    p = dict(_object(spec.get('pilot'), 'pilot'))
    # Reserved binding fields are always derived here, never trusted from input.
    for component in ('intervention', 'comparator'):
        value = _object(p.get(component), component)
        text(value.get('description'), component + '.description')
        _references(store, value.get('evidence_refs'), component + ' composition', required=True)
    p.update(candidate_key=candidate['key'], candidate=candidate,
        inspection_ref=spec['inspection_ref'], selection_ref=selection.ref.to_dict(),
        source_operation_refs=sources, source_record_refs=source_records, justification=justification,
        execution_mode='external_recording_only',
        code_digest=digest(canonical([p['intervention'], p['comparator']])))
    for field in ('discovery_groups', 'split_groups'):
        values = p.get(field)
        if not isinstance(values, list):
            raise WorkflowError(field + ' must be a list of nonempty group names')
        for value in values:
            text(value, field + ' group')
    groups = set(p['discovery_groups'])
    for ref in selection.payload['selected_refs']:
        record = store.get(RecordRef.from_dict(ref))
        if record and record.ref.kind == 'case':
            groups.add(record.payload['split_group'])
    groups.add(candidate['split_group'])
    p['discovery_groups'] = sorted(groups)
    if p.get('direction') not in ('lower', 'higher'):
        raise WorkflowError('pilot direction must be lower or higher')
    for name in ('quality_guardrails', 'checkpoints'):
        if not isinstance(p.get(name), list) or not p[name]:
            raise WorkflowError(name + ' must be nonempty')
        for value in p[name]:
            text(value, name)
    # Validate the unchanged record contract before examining assignment details.
    pilot = Record.create('pilot', digest(canonical(p))[:24], p, study_scope=SCOPE)
    assigned_groups = set()
    for unit in p['units']:
        ref = RecordRef.from_dict(unit['case_ref'])
        case = store.get(ref)
        if ref.kind != 'case' or case is None or case.payload['split_group'] != unit['split_group']:
            raise EvidenceError('assigned unit must bind an existing case and its split group')
        assigned_groups.add(unit['split_group'])
    if assigned_groups != set(p['split_groups']):
        raise WorkflowError('pilot split groups differ from assigned units')
    with store.transaction():
        _current(store, p)
        existing = store.get(pilot.ref)
        if existing is None:
            store._import_records([pilot])
        else:
            pilot = existing
    return {'status': 'registered', 'pilot_ref': pilot.ref.to_dict(),
        'code_digest': p['code_digest'], 'candidate_key': candidate['key'],
        'execution_mode': p['execution_mode'], 'benefit_status': 'unknown',
        'next_action': 'Authorized agent performs the registered comparison within its existing authority, captures execution and outcome evidence, then submits pilot review; registration is not execution.'}


def review(store, spec):
    _object(spec, 'review')
    if type(spec.get('review_format')) is not int or spec['review_format'] != 1:
        raise WorkflowError('unsupported pilot review format')
    ref = RecordRef.from_dict(spec.get('pilot_ref'))
    pilot = store.get(ref)
    if ref.study_scope != SCOPE or ref.kind != 'pilot' or pilot is None:
        raise WorkflowError('review requires a registered candidate pilot')
    p = pilot.payload
    _current(store, {'pilot_ref': ref.to_dict()})
    a = dict(_object(spec.get('analysis'), 'analysis'))
    _references(store, a.get('evidence_refs'), 'analysis evidence')
    if (a.get('method'), a.get('method_version')) != (p['analysis_method'], p['analysis_version']):
        raise WorkflowError('analysis differs from the registered method; register a revised pilot')
    results = a.get('results')
    if not isinstance(results, list):
        raise WorkflowError('analysis requires explicit assigned-unit results')
    seen = set()
    for result in results:
        _object(result, 'unit result')
        unit = text(result.get('unit'), 'unit')
        if unit in seen or result.get('status') not in ('observed', 'unknown', 'pending', 'excluded'):
            raise WorkflowError('duplicate unit or invalid result status')
        seen.add(unit)
        text(result.get('reason'), 'unit result reason')
        _references(store, result.get('evidence_refs'), 'unit result evidence',
                    required=result['status'] == 'observed')
    if seen != {u['unit'] for u in p['units']}:
        raise WorkflowError('every assigned unit requires a result or explicit missingness')
    if a.get('reviewer_type') not in ('agent', 'human'):
        raise WorkflowError('analysis requires an attributed reviewer')
    for name, states in (('activation', ('observed', 'not_observed', 'unknown')),
                         ('comparison', ('matched', 'unmatched', 'unknown')),
                         ('benefit', ('supported', 'not_supported', 'unknown'))):
        judgment = _object(a.get(name), name)
        if judgment.get('status') not in states:
            raise WorkflowError('invalid ' + name + ' status')
        text(judgment.get('reason'), name + ' reason')
        _references(store, judgment.get('evidence_refs'), name + ' evidence',
                    required=judgment['status'] != 'unknown')
    if a['benefit']['status'] == 'supported' and (
            a['activation']['status'] != 'observed' or a['comparison']['status'] != 'matched'
            or not any(r['status'] == 'observed' for r in results)):
        raise WorkflowError('supported benefit requires observed activation, matched comparison and observed outcomes')
    a.update(pilot_ref=ref.to_dict(), registered_scope=p['scope'],
             assigned_case_refs=[u['case_ref'] for u in p['units']],
             registered_composition_digest=p['code_digest'],
             judgment_basis='attributed_review_not_automatic_effect_verification')
    analysis = Record.create('analysis', digest(canonical(a))[:24], a, study_scope=SCOPE)
    d = dict(_object(spec.get('decision'), 'decision'))
    if d.get('benefit_status') != a['benefit']['status']:
        raise WorkflowError('decision benefit differs from the attributed analysis')
    d.update(pilot_ref=ref.to_dict(), candidate_key=p['candidate_key'],
             analysis_refs=[analysis.ref.to_dict()],
             judgment_basis='attributed_review_not_automatic_effect_verification',
             global_workflow_changed=False)
    for name in ('evidence_refs', 'counterevidence_refs'):
        _references(store, d.get(name), 'decision ' + name,
                    required=name == 'evidence_refs' and d.get('disposition') != 'insufficient_evidence')
    # A declared keep needs explicit evidence for benefit AND each registered
    # guardrail, separately from the narrative and from unit completion.
    guardrails = a.get('guardrails')
    if not isinstance(guardrails, list) or len(guardrails) != len(p['quality_guardrails']):
        raise WorkflowError('review must account for every registered quality guardrail')
    if [_object(g, 'guardrail').get('guardrail') for g in guardrails] != p['quality_guardrails']:
        raise WorkflowError('review guardrails differ from registration')
    for guardrail in guardrails:
        if guardrail.get('status') not in ('preserved', 'regressed', 'unknown'):
            raise WorkflowError('invalid guardrail status')
        text(guardrail.get('reason'), 'guardrail reason')
        _references(store, guardrail.get('evidence_refs'), 'guardrail evidence',
                    required=guardrail['status'] != 'unknown')
    quality = ('regressed' if any(g['status'] == 'regressed' for g in guardrails)
               else 'preserved' if all(g['status'] == 'preserved' for g in guardrails) else 'unknown')
    if d.get('quality_status') != quality:
        raise WorkflowError('decision quality differs from registered guardrail judgments')
    _current(store, a)
    # Exclude the not-yet-published analysis reference while checking decision inputs.
    _current(store, {k: v for k, v in d.items() if k != 'analysis_refs'})
    heads = [r for r in store.heads(kind='decision', study_scope=SCOPE) if r.ref.key == ref.key]
    previous = heads[0] if heads else None
    if previous and previous.payload == d:
        return _review_receipt(previous, analysis)
    expected = integer(spec.get('expected_revision'), 'expected decision revision')
    if expected != (previous.ref.revision if previous else 0):
        raise RevisionConflict('review changed; read the latest decision before revising it')
    decision = Record.create('decision', ref.key, d, study_scope=SCOPE, revision=expected + 1)
    # Publish both or neither, including on stale revisions or invalid links.
    with store.transaction():
        # Recheck dependencies under the writer lock so a simultaneous case
        # correction cannot slip between currentness validation and publication.
        _current(store, a)
        _current(store, {k: v for k, v in d.items() if k != 'analysis_refs'})
        existing = store.get(analysis.ref)
        store._import_records([existing or analysis, decision])
    return _review_receipt(decision, existing or analysis)


def _review_receipt(decision, analysis):
    return {'decision_ref': decision.ref.to_dict(), 'analysis_ref': analysis.ref.to_dict(),
        'disposition': decision.payload['disposition'],
        'judgment_basis': decision.payload['judgment_basis'],
        'global_workflow_changed': False, 'next_action': decision.payload['next_action']}


def followups(store, *, now=None):
    with store.read_snapshot():
        return _followups(store, now=now)


def _followups(store, *, now=None):
    current = timestamp(now or utc_now())
    decisions = {r.ref.key: r for r in store.heads(kind='decision', study_scope=SCOPE)}
    result = []
    for pilot in store.heads(kind='pilot', study_scope=SCOPE):
        review = decisions.get(pilot.ref.key)
        due_at = review.payload.get('due_at') if review else None
        applicability = store.applicability([review.ref if review else pilot.ref])
        recorded_action = review.payload['next_action'] if review else {
            'owner': pilot.payload['owner'], 'action': 'Perform the registered comparison and submit pilot review.',
            'prerequisites': 'Existing execution authority and the registered eligibility, budget and isolation.',
            'acceptance': 'Account for every assigned unit and quality guardrail; retain unknown outcomes.'}
        next_action = recorded_action if applicability['current'] else {
            'owner': recorded_action['owner'],
            'action': 'Reconcile missing or superseded evidence before continuing this pilot.',
            'prerequisites': 'Inspect current_applicability reasons and the exact retained input references; preserve the existing decisions.',
            'acceptance': 'Restore the exact missing evidence or explicitly review corrected inputs; do not treat historical judgments as current.'}
        result.append({'pilot_ref': pilot.ref.to_dict(),
            'candidate_key': pilot.payload['candidate_key'],
            'decision_ref': review.ref.to_dict() if review else None,
            'disposition': review.payload['disposition'] if review else 'not_reviewed',
            'judgment_basis': 'attributed_review_not_automatic_effect_verification',
            'due_at': due_at, 'due': timestamp(due_at) <= current if due_at else None,
            'current_applicability': applicability,
            **({'recorded_next_action': recorded_action} if not applicability['current'] else {}),
            'next_action': next_action})
    return result
