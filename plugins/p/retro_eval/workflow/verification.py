"""Artifact-bound verification receipts; completion prose is not an oracle."""
from __future__ import annotations

from .evidence import resolve_evidence
from .records import canonical, timestamp


def verify_outcome(case, criterion, outcome, store):
    """Validate an imported receipt, without executing any claimed command.

    A receipt preserves its claimed producer and execution provenance. Importing
    it does not authenticate an external producer. Automatic trace retrieval
    never creates these receipts from assistant prose or tool-name heuristics.
    """
    p = outcome.payload
    reference = p.get('verification_receipt')
    if not isinstance(reference, dict):
        return False
    resolved = resolve_evidence(reference, store)
    receipt = resolved.value
    if resolved.status != 'resolved' or not isinstance(receipt, dict):
        return False
    expected = {'verification_format': 1, 'case_ref': case.ref.to_dict(),
                'criterion_key': criterion['key'], 'contract_version': case.payload['contract']['version'],
                'verifier_type': p['verifier_type'], 'verifier_version': p['verifier_version'],
                'procedure_id': criterion['verification_method'], 'status': p['status']}
    if any(receipt.get(key) != value for key, value in expected.items()):
        return False
    if (type(receipt.get('verification_format')) is not int or
            type(receipt.get('contract_version')) is not int):
        return False
    if not isinstance(receipt.get('environment'), str) or not receipt['environment'].strip():
        return False
    try:
        timestamp(receipt.get('verified_at'))
    except ValueError:
        return False
    if receipt.get('verified_at') != p['verified_at']:
        return False
    artifacts = receipt.get('artifact_refs')
    if not isinstance(artifacts, list) or not artifacts:
        return False
    if any(resolve_evidence(ref, store).status != 'resolved' for ref in artifacts):
        return False
    # One artifact may hold results for many different checks. Matching only its
    # digest would let a receipt silently select another check in those bytes.
    # Classification is enforced by the registry, not a caller's stale label.
    def identity(ref):
        return (ref.get('sha256'), canonical(ref.get('selector', {'kind': 'whole'})))
    bound = {identity(ref) for ref in p['evidence_refs'] if isinstance(ref, dict)}
    if any(identity(ref) not in bound for ref in artifacts):
        return False
    expected_class = criterion.get('expected_artifact_class')
    if expected_class and any(store.read_artifact(ref)[1] != expected_class for ref in artifacts):
        return False
    kind = p['verifier_type']
    if p['status'] == 'not_applicable':
        return bool(receipt.get('applicability_rule')) and receipt['applicability_rule'] == p.get('applicability_rule')
    if kind == 'code':
        exit_code = receipt.get('exit_code')
        if receipt.get('executed') is not True or type(exit_code) is not int or exit_code < 0:
            return False
        if not isinstance(receipt.get('command_identity'), str) or not receipt['command_identity'].strip():
            return False
        return (p['status'] == 'pass' and exit_code == 0 or p['status'] == 'fail' and exit_code != 0)
    if kind == 'human':
        return (receipt.get('assessor_type') == 'human' and
                type(receipt.get('accepted')) is bool and
                p['status'] == ('pass' if receipt['accepted'] else 'fail'))
    if kind == 'artifact':
        # An artifact comparison receipt must contain the expected and observed
        # canonical values; the declared procedure is the acceptance oracle.
        if 'expected' not in receipt or 'observed' not in receipt:
            return False
        equal = canonical(receipt['expected']) == canonical(receipt['observed'])
        return p['status'] == ('pass' if equal else 'fail')
    return False
