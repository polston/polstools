"""Explicit manifest import and conservative historical case candidates."""
from __future__ import annotations

from .errors import EvidenceError, UnsupportedVersion, WorkflowError
from .records import Record, RecordRef, canonical, digest, strict_loads
from .registry import artifact_references


def import_manifest(manifest, store):
    """Import already-published snapshots and revisions as one transaction.

    Manifest authors may assert case boundaries; the assertion is preserved as
    provenance, never upgraded to an observed or independently verified fact.
    """
    if manifest is None:
        raise WorkflowError('case manifest is required for explicit import')
    if isinstance(manifest, (str, bytes)):
        manifest = strict_loads(manifest)
    if not isinstance(manifest, dict) or type(manifest.get('manifest_format')) is not int or manifest['manifest_format'] != 1:
        raise UnsupportedVersion('unsupported manifest format')
    if not isinstance(manifest.get('records'), list) or not manifest['records']:
        raise WorkflowError('manifest has no eligible records')
    records = [Record.from_dict(row) for row in manifest['records']]
    if not any(r.ref.kind == 'case' for r in records):
        raise WorkflowError('manifest requires an explicit case')
    for record in records:
        for ref in artifact_references(record.payload):
            store.read_artifact(ref)
    return store.import_records(records)


def legacy_observation(raw, field, *, unit='category'):
    """Deserializer defaults do not establish raw-source observation provenance."""
    provenance = raw.get('field_provenance', {}).get(field)
    if not isinstance(provenance, dict) or not provenance.get('evidence_refs'):
        return {'availability': 'not_observed', 'value': None, 'unit': unit,
                'method': 'legacy_import', 'reason': 'raw_field_provenance_unestablished'}
    if field not in raw:
        return {'availability': 'not_observed', 'value': None, 'unit': unit,
                'method': 'legacy_import', 'reason': 'field_absent'}
    return {'availability': 'observed', 'value': raw[field], 'unit': unit,
            'method': 'explicit_import_provenance', 'evidence_refs': provenance['evidence_refs']}


def candidate_records(captured, *, created_at, study_scope='automatic'):
    """A trace fragment is a retrieval candidate, never a confirmed task unit.

    The key binds the frozen snapshot. Split groups use the stable source key,
    ensuring different snapshots/retries cannot appear as independent cases.
    Similar redacted text is not a linkage key.
    """
    if not captured.included or not captured.records:
        return []
    key = digest(canonical([captured.source_key, captured.raw_ref['sha256'],
                            captured.adapter_version, captured.projection_ref,
                            captured.observations, 'candidate-v2']))[:24]
    common = {'study_scope': study_scope, 'created_at': created_at}
    snapshot = Record.create('snapshot', key, captured.summary(), **common)
    prompts = list(captured.excerpts['prompts'].values())
    intent = prompts[0].get('user_turn', '').strip() if prompts else ''
    case = Record.create('case', key, {
        'intent': intent or 'Historical fragment with unestablished intent',
        'task_family': 'unclassified', 'split_group': captured.source_key,
        'origin': 'retrospective', 'lifecycle': 'open',
        'contract': {'version': 1, 'criteria': []},
        'boundary_status': 'inferred', 'independent_unit_eligible': False,
        'links': [{'relation': 'source_fragment', 'status': 'inferred',
                   'snapshot_ref': snapshot.ref.to_dict()}],
        'evidence_refs': [captured.excerpts_ref, captured.normalized_ref],
        'limitations': ['Trace boundaries do not establish task boundaries.',
                       'No contract-bound delivery evidence was imported.'],
    }, **common)
    attempt = Record.create('attempt', key, {
        'case_ref': case.ref.to_dict(), 'snapshot_ref': snapshot.ref.to_dict(),
        'harness': captured.source,
        'trace_ids': sorted({r['trace_id'] for r in captured.records}),
        'source_observations': captured.observations,
        'evidence_refs': [captured.normalized_ref],
    }, **common)
    return [snapshot, case, attempt]


def confirm_link(cases, *, key, intent, criteria, created_at, provenance,
                 study_scope='confirmed'):
    """Construct an explicit common-case contract without altering its fragments."""
    if provenance.get('status') not in ('authoritative', 'human_confirmed'):
        raise WorkflowError('common-case linkage requires authoritative or human-confirmed provenance')
    if not provenance.get('evidence_refs'):
        raise EvidenceError('common-case linkage requires evidence')
    return Record.create('case', key, {
        'intent': intent, 'task_family': 'confirmed', 'split_group': key,
        'origin': 'retrospective', 'lifecycle': 'open',
        'contract': {'version': 1, 'criteria': criteria},
        'boundary_status': provenance['status'], 'independent_unit_eligible': True,
        'links': [{'relation': 'continuation', 'status': provenance['status'],
                   'case_ref': c.ref.to_dict()} for c in cases],
        'link_provenance': provenance}, created_at=created_at, study_scope=study_scope)


def linked_attempts(store, case):
    """View attempts under an evidenced common case, preserving original bindings."""
    from .records import RecordRef
    if not store.applicability([case.ref])['current']:
        raise EvidenceError('common case has invalid or superseded linkage evidence')
    members = {canonical(case.ref.to_dict())}
    for link in case.payload.get('links', []):
        if link.get('status') in ('authoritative', 'human_confirmed') and 'case_ref' in link:
            members.add(canonical(RecordRef.from_dict(link['case_ref']).to_dict()))
    return [attempt for attempt in store.heads(kind='attempt')
            if canonical(attempt.payload['case_ref']) in members]
