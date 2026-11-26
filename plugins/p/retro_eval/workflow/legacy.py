"""Read effect-record v1 without upgrading its authored efficacy judgment."""
from __future__ import annotations

from pathlib import Path
import tempfile

from ..effect_records import check_record
from .errors import EvidenceError, PolicyDenied
from .records import Record, canonical, digest, strict_loads
from .registry import external_path


def effect_summaries(store):
    """Expose retained declarations for review without interpreting their triggers.

    A prose task/date trigger is not a runnable schedule. Keep these records
    separate from registered pilots and retain the importer's unknown claims.
    """
    with store.read_snapshot():
        summaries = []
        for record in store.heads(kind='decision', study_scope='legacy-effects'):
            declaration = record.payload.get('legacy_declaration')
            # Portable decisions may omit importer-specific extensions. The
            # scope name alone does not certify that import_effect produced it.
            if not isinstance(declaration, dict):
                declaration = {}
            applicability = store.applicability([record.ref])
            recorded_action = record.payload['next_action']
            next_action = recorded_action if applicability['current'] else {
                'owner': recorded_action['owner'],
                'action': 'Reconcile missing or superseded evidence before reassessing this declaration.',
                'prerequisites': 'Inspect current_applicability reasons and the exact retained input references; preserve the original declaration.',
                'acceptance': 'Restore the exact missing evidence or explicitly review corrected inputs; historical declarations do not establish current benefit.'}
            summaries.append({
                'decision_ref': record.ref.to_dict(),
                'proposal_id': declaration.get('proposal_id'),
                'owner': declaration.get('owner'),
                'declared_review_trigger': declaration.get('review_trigger'),
                'declared_next_action': declaration.get('next_action'),
                'disposition': record.payload['disposition'],
                'benefit_status': record.payload['benefit_status'],
                'operational_status': record.payload.get('operational_status', 'unknown'),
                'current_applicability': applicability,
                **({'recorded_next_action': recorded_action} if not applicability['current'] else {}),
                'next_action': next_action,
            })
        return summaries


def import_effect(store, record_path, index_path):
    """Validate a frozen portable copy through the existing v1 binding checker.

    No library executes a script. Source bytes are captured once and fingerprints
    checked before constructing a disposable, private compatibility packet.
    """
    record_path, index_path = external_path(record_path), external_path(index_path)
    record_bytes, index_bytes = record_path.read_bytes(), index_path.read_bytes()
    record, index = strict_loads(record_bytes), strict_loads(index_bytes)
    captured, rewritten = [], []
    for entry in index.get('evidence', []):
        locator = Path(entry['artifact'])
        if locator.is_absolute() or '..' in locator.parts:
            raise PolicyDenied('legacy artifact locator escapes the authorized collection')
        path = external_path(index_path.parent / locator)
        try:
            path.relative_to(index_path.parent)
        except ValueError as exc:
            raise PolicyDenied('legacy artifact symlink escapes the authorized collection') from exc
        data = path.read_bytes()
        if digest(data) != entry.get('sha256'):
            raise EvidenceError('legacy effect artifact fingerprint mismatch')
        captured.append(data)
        rewritten.append({**entry, 'artifact': digest(data) + '.json'})
    with tempfile.TemporaryDirectory(dir=store.root, prefix='.effect-import-') as root:
        root = Path(root)
        for data, entry in zip(captured, rewritten):
            (root / entry['artifact']).write_bytes(data)
        (root / 'record.json').write_bytes(record_bytes)
        (root / 'index.json').write_bytes(canonical({**index, 'evidence': rewritten}))
        check = check_record(root / 'record.json', root / 'index.json')
    refs = [store.publish_artifact(data, 'private_raw') for data in [record_bytes, index_bytes] + captured]
    key = digest(canonical([digest(record_bytes), digest(index_bytes)]))[:24]
    payload = {'disposition': 'insufficient_evidence', 'benefit_status': 'unknown',
        'quality_status': 'unknown', 'analysis_refs': [], 'counterevidence_refs': [],
        'evidence_refs': refs, 'legacy_schema': 1, 'legacy_declaration': record,
        'binding_check': check, 'operational_status': 'inactive',
        'rationale': 'Preserve the v1 declaration and binding check without converting authored judgments into verified effects.',
        'next_action': {'owner': 'agent', 'action': 'Reassess the declared result against the current analysis and evidence contract.',
            'prerequisites': 'Comparable outcome evidence and applicable calibrated measures.',
            'acceptance': 'A new analysis supports a scoped decision, or insufficiency remains explicit.'}}
    decision = Record.create('decision', key, payload, study_scope='legacy-effects')
    prior = store.get(decision.ref)
    if prior is not None:
        return prior
    store.append(decision, None)
    return decision
