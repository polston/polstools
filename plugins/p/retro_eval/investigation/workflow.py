"""Bounded reuse of the existing local corpus service as an evidence action."""
from dataclasses import asdict
from pathlib import Path

from ..workflow.errors import BudgetExhausted, PolicyDenied, WorkflowError
from ..workflow.records import canonical
from ..workflow.runner import Limits, Runner
from ..workflow.service import run_automatic
from ..workflow.sources import default_roots
from ..workflow.investigation_state import remaining


def inspect_workflow(api, root, action_id):
    """Inspect exact invocation links without current capture inputs or writes."""
    with api.store.read_snapshot():
        if root.kind != 'investigation' or api.store.get(root) is None:
            raise WorkflowError('investigation root not found')
        admissions = [event for event in api.store.history(
            kind='investigation_event', study_scope=root.study_scope)
            if event.payload['root_ref'] == root.to_dict()
            and event.payload['event_type'] == 'action'
            and event.payload['data']['id'] == action_id]
        if not admissions:
            raise WorkflowError('action admission not found')
        reference = admissions[0].ref.to_dict() if len(admissions) == 1 else None
        runs = []
        if reference is not None:
            for run in Runner(api.store).list_runs():
                if run['plan'].get('invocation_ref') != reference:
                    continue
                runs.append({**{k: run[k] for k in ('run_id', 'state', 'started', 'deadline', 'used', 'usage_scope')},
                    'invocation_ref': reference,
                    'published_views': [op['result']['artifact_ref'] for op in run['operations']
                        if op['unit_key'].startswith('view:') and op['state'] == 'completed'
                        and op['result'] and 'artifact_ref' in op['result']]})
        return {'invocation_ref': reference, 'runs': runs,
                'link_status': 'recorded' if runs else 'unknown',
                'next_action': ('Inspect all linked receipts and views. Reconcile an unsettled action only '
                    'after verifying effects and usage; inspection does not execute or settle work.' if runs else
                    'No linked run is recorded. Inspect retained evidence before reconciling; '
                    'absence of a link does not establish absence of effects.')}


def collect_workflow(api, root, expected_sequence, action_id, *, roots=None, limits=None, grant='root'):
    state = api.checkpoint(root)
    allocation = state['grants'].get(grant)
    if allocation is None or 'local-session-history' not in allocation['authority']:
        raise PolicyDenied('workflow capture requires an existing local-session-history grant')
    roots = roots if roots is not None else default_roots()
    requested_limits = asdict(limits or Limits())
    binding = api.store.publish_artifact(canonical({
        'roots': {name: str(Path(path).resolve()) for name, path in roots.items()},
        'requested_limits': requested_limits, 'grant': grant}), 'private_metadata')
    prior = state['actions'].get(action_id)
    if prior is not None:
        if prior['method'] != 'empirical' or prior['inputs'] != [binding]:
            from ..workflow.errors import RevisionConflict
            raise RevisionConflict('workflow action identity has different capture inputs')
        replay = {'admitted': False, 'reason': 'already_admitted', 'action': prior}
        if prior['state'] in ('admitted', 'uncertain'):
            replay['recovery'] = inspect_workflow(api, root, action_id)
        return replay
    now = api.clock()
    deadline = min(allocation['deadline_ms'], state['grants']['root']['deadline_ms'])
    seconds = (deadline - now) // 1000
    if seconds < 1:
        raise BudgetExhausted('insufficient root time for a workflow episode')
    requested = requested_limits.copy()
    requested['runtime_seconds'] = min(requested['runtime_seconds'], seconds)
    # This bridge performs local descriptive work only, never provider calls.
    requested['provider_calls'] = 0
    available = remaining(allocation)
    reservation = {'actions': 1} if 'actions' in available else {}
    if 'bytes' in available:
        capacity = available['bytes']
        if capacity < 2:
            raise BudgetExhausted('insufficient bytes for capture and derived evidence')
        requested['captured_bytes'] = min(requested['captured_bytes'], capacity * 4 // 5)
        requested['derived_bytes'] = min(requested['derived_bytes'], capacity - requested['captured_bytes'])
        reservation['bytes'] = requested['captured_bytes'] + requested['derived_bytes']
    bounded = Limits.from_dict(requested)
    action = {'id': action_id, 'grant': grant, 'kind': 'inspect', 'method': 'empirical',
              'purpose': 'Inspect bounded workflow evidence across configured local corpuses',
              'prediction': 'Captured sources support or refute candidate workflow explanations',
              'falsifier': 'Sources are missing, ineligible or contradict the proposed explanation',
              'alternatives': ['manual targeted source inspection'],
              'authority': ['local-session-history'], 'reservation': reservation,
              'questions': [], 'inputs': [binding]}
    admitted = api.admit(root, expected_sequence, action)
    if not admitted['admitted']:
        return admitted

    def clock():
        current = api.clock()
        if current >= deadline:
            raise BudgetExhausted('root deadline reached during workflow episode')
        return current / 1000

    try:
        report = run_automatic(api.store.root, roots=roots, limits=bounded, clock=clock,
                               invocation_ref=admitted['event_ref'])
        evidence = api.store.publish_artifact(canonical(report), 'private_metadata')
        usage = {'actions': 1} if 'actions' in reservation else {}
        if 'bytes' in reservation:
            usage['bytes'] = report['used']['captured_bytes'] + report['used']['derived_bytes']
        result = {'action_id': action_id, 'status': 'succeeded', 'usage': usage,
                  'evidence': [evidence], 'producer': 'local workflow service', 'origin': 'helper',
                  'observation': 'Workflow report produced; coverage and candidate quality need inspection.'}
        # Partial runs can retain uncertain byte reservations. Do not claim
        # those are observed usage; preserve the investigation allocation.
        if 'bytes' in reservation and report['state'] != 'completed':
            result.update(status='uncertain', usage={},
                          observation='Partial workflow report; byte accounting requires reconciliation.')
    except Exception as exc:
        evidence = api.store.publish_artifact(canonical({'error': type(exc).__name__, 'message': str(exc)}), 'private_metadata')
        result = {'action_id': action_id, 'status': 'uncertain', 'usage': {},
                  'evidence': [evidence], 'producer': 'local workflow service', 'origin': 'helper',
                  'observation': 'Capture failed; retain reservation until actual effects are reconciled.'}
        seq = api.checkpoint(root)['sequence']
        api.settle(root, seq, result)
        raise
    seq = api.checkpoint(root)['sequence']
    api.settle(root, seq, result)
    return {'report_ref': evidence, 'coverage': report.get('coverage', {}),
            'run_id': report['run_id'], 'state': report['state'],
            'next_action': report.get('next_action', 'inspect the workflow report')}
