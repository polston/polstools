"""Preregistered local replay pilots selected from observed corpus gaps.

The first installed pilot tests message-envelope normalization. It replays a
frozen corpus through two deterministic retrieval procedures; it does not
estimate an effect on developer productivity or substitute model labels for
human experience. Additional pilots register through the same record boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from ..text_rules import classify_user_turn
from .analysis import classifier_metrics, confirmation_status, paired_effect, decision
from .errors import EvidenceError, WorkflowError
from .evidence import resolve_evidence
from .records import Record, RecordRef, canonical, digest, strict_loads, utc_now

PILOT_VERSION = '3'
LABEL_RUBRIC = ('True only when the user is correcting or rejecting the preceding response/action. '
    'Ordinary questions, status requests, approvals, new tasks, and scope additions alone are false. '
    'Use null when clipped context or ambiguous intent prevents a supported judgment. Do not infer satisfaction.')


def execution_composition():
    """Bind the installed procedures and their shared semantic dependencies."""
    root = Path(__file__).resolve().parents[1]
    paths = [root / 'text_rules.py', *sorted((root / 'workflow').glob('*.py'))]
    return {path.relative_to(root).as_posix(): digest(path.read_bytes()) for path in paths}


def unwrap_user_request(text):
    """Recognize the exported envelope, not arbitrary XML-looking user prose."""
    text = str(text)
    prefix = '<USER_REQUEST>'
    closing = '</USER_REQUEST>'
    stripped = text.lstrip()
    if not stripped.startswith(prefix):
        return {'text': text, 'method': 'unchanged_plain_text', 'changed': False}
    if stripped.count(prefix) != 1 or stripped.count(closing) != 1:
        return {'text': text, 'method': 'ambiguous_or_incomplete_wrapper', 'changed': False}
    body, tail = stripped[len(prefix):].split(closing, 1)
    if tail.strip() and not tail.lstrip().startswith('<ADDITIONAL_METADATA>'):
        return {'text': text, 'method': 'unrecognized_wrapper_tail', 'changed': False}
    return {'text': body.strip(), 'method': 'known_export_envelope_v1', 'changed': True}


def _latest_outputs(store):
    # Runner receipts, not report filenames, are the recovery authority.
    from .runner import Runner
    runner = Runner(store)
    runs = runner.list_runs()
    outputs = {}
    for run in reversed(runs):
        for operation in run['operations']:
            if operation['state'] != 'completed' or not operation['unit_key'].startswith('source:'):
                continue
            ref = (operation['result'] or {}).get('artifact_ref')
            if not ref:
                continue
            data, _ = store.read_artifact(ref)
            output = strict_loads(data)
            if isinstance(output, dict) and output.get('included') and output.get('source_key') not in outputs:
                outputs[output['source_key']] = output
    return list(outputs.values())


def _discovery_groups(store):
    """Retained exposure, including superseded records and later discovery."""
    discovered = set()
    # Once predictions/results have been exposed, those groups cannot become
    # fresh confirmation material for a revised procedure.
    for analysis in store.history(kind='analysis'):
        if analysis.ref.study_scope == 'pilots':
            refs = analysis.payload['input_refs']
        elif analysis.ref.study_scope == 'candidate-pilots':
            # Custom reviews bind their assigned cases separately from optional
            # typed input_refs. Even a pending review may contain aggregate
            # judgments, so conservatively reserve all reviewed families for
            # discovery. Registration alone does not establish this exposure.
            refs = analysis.payload.get('assigned_case_refs', []) + analysis.payload['input_refs']
        else:
            continue
        for ref in refs:
            case = store.get(RecordRef.from_dict(ref))
            if case and case.ref.kind == 'case':
                discovered.add(case.payload['split_group'])
    for selection in store.history(kind='selection'):
        if selection.payload['purpose'] == 'discovery':
            for ref in selection.payload['selected_refs']:
                case = store.get(RecordRef.from_dict(ref))
                if case and case.ref.kind == 'case':
                    discovered.add(case.payload['split_group'])
    for record in store.history(kind='assessment'):
        if record.payload.get('presentation_mode') != 'blind':
            case = store.get(RecordRef.from_dict(record.payload['case_ref']))
            if case:
                discovered.add(case.payload['split_group'])
    # Source keys follow paths. Exact captured bytes at another path are not
    # fresh evidence. Retain alias links across snapshot revisions so copying,
    # moving, or subsequently extending a known source cannot erase exposure.
    # This deliberately does not infer linkage from similar redacted text.
    by_digest, by_group = {}, {}
    for snapshot in store.history(kind='snapshot'):
        group = snapshot.payload.get('source_key')
        raw = snapshot.payload.get('raw_ref')
        sha = raw.get('sha256') if isinstance(raw, dict) else None
        if (not isinstance(group, str) or not group
                or not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha)):
            continue
        by_digest.setdefault(sha, set()).add(group)
        by_group.setdefault(group, set()).add(sha)
    pending, seen_digests = list(discovered), set()
    while pending:
        for sha in by_group.get(pending.pop(), ()):
            if sha in seen_digests:
                continue
            seen_digests.add(sha)
            for alias in by_digest[sha] - discovered:
                discovered.add(alias)
                pending.append(alias)
    return discovered


def _label_context(store, unit):
    """Reconstruct immutable display fields from the registered evidence."""
    resolution = resolve_evidence(unit['evidence_ref'], store)
    if resolution.status != 'resolved' or not isinstance(resolution.value, dict):
        raise EvidenceError('registered label evidence is unavailable; recover the frozen evidence before comparing')
    evidence = resolution.value
    return {'unit': unit['unit'], 'case_ref': unit['case_ref'],
        'split_group': unit['split_group'], 'evidence_ref': unit['evidence_ref'],
        'user_turn': evidence.get('user_turn', ''), 'context': evidence.get('context', ''),
        'context_chars': evidence.get('context_chars'),
        'context_truncated': evidence.get('context_chars', 0) > len(evidence.get('context', ''))}


def prepare(store, *, created_at=None):
    """Select the first feasible installed replay question without a setup survey."""
    created_at = created_at or utc_now()
    for action in store.heads(kind='decision', study_scope='recurring'):
        if action.payload.get('operational_status') != 'retired':
            continue
        previous = store.get(RecordRef('pilots', 'pilot', action.ref.key, 1))
        if previous and previous.payload.get('proposal_identity') == 'automatic-envelope-calibration':
            return {'status': 'insufficient_evidence', 'reason': 'installed_replay_candidate_retired',
                'decision_ref': action.ref.to_dict(),
                'next_action': 'Agent designs and registers a substantively different intervention before another confirmation study.'}
    discovered = _discovery_groups(store)
    outputs = [o for o in _latest_outputs(store) if o['source'] == 'antigravity']
    units = []
    for output in sorted(outputs, key=lambda o: o['source_key']):
        if output['source_key'] in discovered:
            continue
        ref = output['excerpts_ref']
        resolution = resolve_evidence(ref, store)
        if resolution.status != 'resolved':
            continue
        payload = strict_loads(resolution.data)
        case_ref = next((r for r in output['record_refs'] if r['kind'] == 'case'), None)
        for span, evidence in sorted(payload['prompts'].items()):
            units.append({'unit': digest(canonical([ref['sha256'], span]))[:24],
                'split_group': output['source_key'], 'case_ref': case_ref,
                'evidence_ref': {**ref, 'selector': {'kind': 'json_pointer', 'pointer': '/prompts/' + span}},
                'source': output['source']})
    units.sort(key=lambda u: digest(canonical(['pilot-selection-v1', u['unit']])))
    units = units[:60]
    if not units:
        return {'status': 'insufficient_evidence', 'reason': 'no_heldout_envelope_replay_units',
                'next_action': 'Collect new independently grouped excerpts within the existing local authority.'}
    composition = execution_composition()
    code_digest = digest(canonical(composition))
    input_identity = {'pilot_version': PILOT_VERSION, 'units': units,
                      'discovery_groups': sorted(discovered), 'code_digest': code_digest}
    key = digest(canonical(input_identity))[:24]
    record = Record.create('pilot', key, {
        'proposal_identity': 'automatic-envelope-calibration',
        'owner': 'agent', 'scope': 'frozen_antigravity_prompt_retrieval',
        'intervention': {'component': 'known_user_request_envelope', 'version': '1'},
        'comparator': {'component': 'export_text_as_user_text', 'version': '1'},
        'rollback': 'Keep the original captured excerpts and original retrieval rule available.',
        'assignment_unit': 'prompt', 'assignment_mechanism': 'paired_frozen_replay_both_procedures',
        'independence_unit': 'source_family', 'split_groups': sorted({u['split_group'] for u in units}),
        'eligibility': 'captured_direct_user_export_prompts_outside_recorded_discovery_families',
        'strata': ['antigravity'], 'units': units, 'planned_sample': len(units),
        'checkpoints': ['all_registered_units_accounted_for'],
        'primary_estimand': 'false_alert_difference_in_frozen_labeled_cohort',
        'primary_measure': 'correction_retrieval_false_positive',
        'direction': 'lower', 'practical_threshold': 1,
        'threshold_basis': 'engineering_default_one_avoided_false_alert_without_lost_true_corrections',
        'quality_guardrails': ['no_additional_false_negative_on_labeled_true_corrections',
                               'no_content_outside_known_wrapper_removed'],
        'analysis_method': 'paired_family_descriptive', 'analysis_version': '1',
        'missingness_policy': 'retain_all_assigned_units_and_abstain_on_unknown_truth',
        'exposure_procedure': 'invoke_both_installed_pure_functions_on_identical_captured_excerpts',
        'exposure_evidence': 'per_unit_predictions_and_code_digest',
        'isolation': 'pure_string_functions_no_source_commands_or_live_project_execution',
        'carryover': 'none_in_frozen_replay_no_claim_for_learned_live_behavior',
        'concurrent_changes': [], 'resource_budget': {'units': 60, 'external_calls': 0},
        'followup_horizon': 'same_frozen_replay_only',
        'discovery_groups': sorted(discovered), 'code_digest': code_digest,
        'execution_composition': composition,
        'independence_status': 'unverified_source_grouping_descriptive_only',
        'truth_policy': 'agent_blind_semantic_labels_not_human_preference_or_experience',
        'registration_before_grading': True,
    }, study_scope='pilots', created_at=created_at)
    existing = store.get(record.ref)
    if existing is None:
        store.append(record, None)
    else:
        record = existing
    # Prepare label packets without running either predictor.
    packet = [{**_label_context(store, unit), 'label': None, 'reason': '',
               'assessor_type': 'agent', 'presentation_mode': 'blind'} for unit in units]
    directory = store.directory('pilots', key)
    path = directory / 'labels-pending.json'
    if not path.exists():
        path.write_bytes(canonical({'label_format': 1, 'pilot_ref': record.ref.to_dict(),
            'rubric': LABEL_RUBRIC,
            'units': packet}) + b'\n')
    return {'status': 'registered', 'pilot_ref': record.ref.to_dict(), 'units': len(units),
            'source_groups': len(record.payload['split_groups']), 'label_packet': str(path),
            'next_action': 'Agent labels the frozen excerpts while predictions remain hidden, then runs compare.'}


def compare(store, pilot_ref, labels, *, created_at=None):
    # Serialize the exposure check and publication. Two competing submissions
    # must not both establish a first blind comparison for the same pilot.
    with store.transaction():
        analysis = _compare(store, pilot_ref, labels, created_at=created_at)
    return _comparison_receipt(store, analysis)


def _compare(store, pilot_ref, labels, *, created_at=None):
    pilot = store.get(pilot_ref)
    if pilot is None or pilot.ref.kind != 'pilot':
        raise WorkflowError('unknown pilot')
    p = pilot.payload
    if p.get('execution_composition') != execution_composition() or p['code_digest'] != digest(canonical(execution_composition())):
        raise WorkflowError('pilot execution code changed after registration')
    if (not isinstance(labels, dict) or type(labels.get('label_format')) is not int
            or labels['label_format'] != 1 or labels.get('pilot_ref') != pilot_ref.to_dict()
            or labels.get('rubric') != LABEL_RUBRIC or not isinstance(labels.get('units'), list)):
        raise WorkflowError('labels do not bind the registered pilot')
    if not store._applicability([pilot_ref])['current']:
        raise EvidenceError('pilot inputs are no longer applicable; inspect status and register a new comparison after resolving the inputs')
    indexed, contexts = {}, {}
    expected = {u['unit']: u for u in p['units']}
    for label in labels['units']:
        if (not isinstance(label, dict) or not isinstance(label.get('unit'), str)
                or label['unit'] not in expected or label['unit'] in indexed
                or 'label' not in label or label['label'] is not None and type(label['label']) is not bool):
            raise WorkflowError('duplicate or invalid label')
        if label.get('assessor_type') not in ('agent', 'human') or label.get('presentation_mode') != 'blind':
            raise WorkflowError('pilot calibration requires attributed blind labels')
        if not isinstance(label.get('reason'), str) or not label['reason'].strip():
            raise WorkflowError('label or abstention requires a rationale')
        context = _label_context(store, expected[label['unit']])
        if any(field not in label or canonical(label[field]) != canonical(value)
               for field, value in context.items()):
            raise WorkflowError('label display or identity differs from registered evidence; restore the original packet and label its unchanged excerpts')
        indexed[label['unit']] = label
        contexts[label['unit']] = context
    if set(indexed) != set(expected):
        raise WorkflowError('all assigned units must have a label or explicit abstention')
    label_bytes = canonical(labels)
    prior = [r for r in store.history(kind='analysis', study_scope='pilots')
             if r.payload.get('pilot_ref') == pilot_ref.to_dict()]
    for analysis in prior:
        # Identical replay is historical recovery, not a new blind assessment.
        if analysis.payload['evidence_refs'][0]['sha256'] == digest(label_bytes):
            if not store._applicability([analysis.ref])['current']:
                raise EvidenceError('stored comparison is no longer applicable; inspect status and recover its frozen evidence')
            return analysis
    if prior:
        raise WorkflowError('pilot predictions were already exposed; preserve the original comparison and prepare fresh held-out units instead of relabeling as blind')
    discovered = _discovery_groups(store) | set(p['discovery_groups'])
    if discovered & set(p['split_groups']):
        raise WorkflowError('pilot units have discovery exposure; preserve them as discovery evidence and prepare fresh held-out units')
    label_sha = store.stage_artifact(label_bytes)
    label_ref = store._bind_artifact(label_sha, 'private_raw', len(label_bytes))
    results, paired = [], []
    for unit in p['units']:
        label = indexed[unit['unit']]
        evidence = contexts[unit['unit']]
        raw = evidence.get('user_turn', '')
        normalized = unwrap_user_request(raw)
        baseline = classify_user_turn(raw, evidence.get('context_chars', 0)) in ('correction', 'interrupt')
        intervention = classify_user_turn(normalized['text'], evidence.get('context_chars', 0)) in ('correction', 'interrupt')
        results.append({**unit, 'truth': label['label'], 'baseline': baseline,
                        'intervention': intervention, 'normalization_method': normalized['method'],
                        'rationale': label['reason'], 'assessor_type': label['assessor_type']})
        if label['label'] is not None:
            scope = digest(canonical([unit['evidence_ref'], p['scope']]))
            paired.append({'unit': unit['unit'], 'split_group': unit['split_group'],
                'baseline': int(baseline and not label['label']),
                'intervention': int(intervention and not label['label']),
                'artifact_digest': unit['evidence_ref']['sha256'],
                'comparator_artifact_digest': unit['evidence_ref']['sha256'],
                'scope_digest': scope, 'comparator_scope_digest': scope})
    metrics = {arm: classifier_metrics([{'truth': row['truth'], 'prediction': row[arm]} for row in results])
               for arm in ('baseline', 'intervention')}
    effect = paired_effect(paired)
    confirmation = confirmation_status(discovered, p['split_groups'])
    confirmation['discovery_disjoint'] = confirmation['independent_confirmation']
    confirmation['independent_confirmation'] = False
    confirmation['limitation'] = 'disjoint_source_groups_do_not_establish_independent_workcases'
    missed = metrics['intervention']['fn'] - metrics['baseline']['fn']
    delta = metrics['intervention']['fp'] - metrics['baseline']['fp']
    scoped = ('retain_candidate_for_larger_confirmation' if delta <= -p['practical_threshold'] and missed <= 0
              else 'revise_candidate' if missed > 0 else 'no_demonstrated_improvement')
    result = {'pilot_ref': pilot_ref.to_dict(), 'input_refs': [u['case_ref'] for u in p['units']],
        'evidence_refs': [label_ref] + [u['evidence_ref'] for u in p['units']],
        'method': 'paired_family_descriptive', 'method_version': '1', 'code_version': p['code_digest'],
        'seed': 0, 'assignment_refs': [], 'exposure_refs': [], 'outcome_refs': [], 'exclusions': [],
        'estimates': {'metrics': metrics, 'paired_effect': effect, 'false_alert_count_difference': delta,
                      'missed_correction_count_difference': missed}, 'intervals': [],
        'assigned_units': len(p['units']), 'results': results, 'confirmation': confirmation,
        'label_binding': {'method': 'registered_display_fields_v1',
                          'discovery_groups_checked': sorted(discovered)},
        'decision': decision(analysis=effect), 'scoped_action': scoped,
        'limitations': ['Agent labels are judgments, not human preference or objective intent truth.',
                        'Source groups are dependence controls, not verified independent workcases.',
                        'This frozen replay estimates retrieval behavior, not live task efficacy.',
                        'No inferential adoption claim is supported by this small descriptive comparison.']}
    key = digest(canonical([pilot_ref.to_dict(), label_ref, p['code_digest']]))[:24]
    analysis = Record.create('analysis', key, result, study_scope='pilots', created_at=created_at or utc_now())
    existing = store.get(analysis.ref)
    if existing is None:
        store._import_records([analysis])
    else:
        analysis = existing
    return analysis


def _comparison_receipt(store, analysis):
    result = analysis.payload
    pilot_ref = RecordRef.from_dict(result['pilot_ref'])
    directory = store.directory('pilots', pilot_ref.key)
    path = directory / ('comparison-' + analysis.ref.key + '.json')
    if not path.exists():
        path.write_bytes(canonical(analysis.to_dict()) + b'\n')
    return {'analysis_ref': analysis.ref.to_dict(), 'comparison_path': str(path),
            'estimates': result['estimates'], 'assigned_units': result['assigned_units'],
            'source_groups': result['estimates']['paired_effect']['independent_units'], 'decision': result['decision'],
            'scoped_action': result['scoped_action'], 'next_action': 'Review label disagreements and counterexamples; retain uncertainty before operational adoption.'}
