"""Kind-specific contracts beyond the core case and acceptance records.

Unknown extra fields may carry attributed provenance. Required fields and
discriminators are validated; an empty object is never a usable record kind.
"""
from __future__ import annotations

from .errors import WorkflowError


def validate(kind, p):
    from .records import RecordRef, integer, text, timestamp

    def texts(*names):
        for name in names:
            text(p.get(name), name)

    def lists(*names):
        for name in names:
            if not isinstance(p.get(name), list):
                raise WorkflowError(name + ' must be a list')

    def objects(*names):
        for name in names:
            if not isinstance(p.get(name), dict):
                raise WorkflowError(name + ' must be an object')

    def refs(*names):
        lists(*names)
        for name in names:
            for value in p[name]:
                RecordRef.from_dict(value)

    if kind == 'capability':
        texts('source', 'adapter_version', 'capability_key', 'method')
        lists('evidence_refs', 'limitations')
        if type(p.get('adapter_supported')) is not bool:
            raise WorkflowError('adapter support must be explicit')
        for key in ('eligible_records', 'observed_records', 'invalid_records'):
            integer(p.get(key), key)
        if p['observed_records'] + p['invalid_records'] > p['eligible_records']:
            raise WorkflowError('capability counts exceed eligible records')
    elif kind in ('hypothesis', 'practice'):
        texts('context', 'observation', 'interpretation', 'mechanism',
              'consequence', 'predicted_benefit', 'possible_harm', 'next_test')
        lists('evidence_refs', 'counterevidence_refs', 'rival_explanations')
        if p.get('evidential_status') not in ('candidate', 'locally_supported', 'effect_supported'):
            raise WorkflowError('invalid evidential status')
        if p.get('operational_status') not in ('inactive', 'provisional', 'active', 'retired'):
            raise WorkflowError('invalid operational status')
        if p['evidential_status'] == 'effect_supported':
            refs('analysis_refs')
            if not p['analysis_refs']:
                raise WorkflowError('effect support needs an analysis reference')
        if kind == 'practice':
            texts('transfer_conditions', 'review_trigger', 'rollback')
    elif kind == 'pilot':
        texts('proposal_identity', 'owner', 'scope', 'rollback', 'assignment_unit',
              'assignment_mechanism', 'independence_unit', 'eligibility',
              'primary_estimand', 'primary_measure', 'direction', 'threshold_basis',
              'analysis_method', 'analysis_version', 'missingness_policy',
              'exposure_procedure', 'exposure_evidence', 'isolation', 'carryover',
              'followup_horizon', 'code_digest', 'truth_policy')
        objects('intervention', 'comparator', 'resource_budget')
        lists('units', 'split_groups', 'strata', 'checkpoints', 'quality_guardrails',
              'concurrent_changes', 'discovery_groups')
        integer(p.get('planned_sample'), 'planned sample', 1)
        from .analysis import finite
        if finite(p.get('practical_threshold'), 'practical threshold') < 0:
            raise WorkflowError('practical threshold must be nonnegative')
        integer(p['resource_budget'].get('units'), 'pilot unit budget', 1)
        integer(p['resource_budget'].get('external_calls'), 'pilot external call budget')
        if p['planned_sample'] > p['resource_budget']['units']:
            raise WorkflowError('planned sample exceeds pilot unit budget')
        if len(p['units']) != p['planned_sample']:
            raise WorkflowError('planned sample differs from assigned units')
        keys = []
        for unit in p['units']:
            if not isinstance(unit, dict):
                raise WorkflowError('invalid assignment unit')
            keys.append(text(unit.get('unit'), 'unit identity'))
            text(unit.get('split_group'), 'split group')
            RecordRef.from_dict(unit.get('case_ref'))
        if len(set(keys)) != len(keys):
            raise WorkflowError('duplicate assignment unit')
        if p.get('registration_before_grading') is not True:
            raise WorkflowError('pilot must declare registration before grading')
    elif kind == 'exposure':
        RecordRef.from_dict(p.get('pilot_ref'))
        RecordRef.from_dict(p.get('case_ref'))
        texts('component', 'version', 'context')
        objects('stages')
        expected = {'assigned', 'configuration_present', 'loaded', 'executed', 'behavior_observed'}
        if set(p['stages']) != expected:
            raise WorkflowError('all exposure stages must remain distinct')
        for stage in p['stages'].values():
            if not isinstance(stage, dict) or stage.get('status') not in ('observed', 'not_observed', 'unknown'):
                raise WorkflowError('invalid exposure stage')
            if not isinstance(stage.get('evidence_refs'), list):
                raise WorkflowError('exposure stage evidence list required')
            if stage['status'] != 'unknown' and not stage['evidence_refs']:
                raise WorkflowError('known exposure stage needs evidence')
    elif kind == 'analysis':
        texts('method', 'method_version', 'code_version')
        integer(p.get('seed'), 'analysis seed')
        refs('input_refs', 'assignment_refs', 'exposure_refs', 'outcome_refs')
        lists('evidence_refs', 'exclusions', 'intervals', 'limitations')
        objects('estimates')
        for interval in p['intervals']:
            from .analysis import valid_interval
            if not valid_interval(interval):
                raise WorkflowError('invalid analysis interval')
    elif kind == 'decision':
        if p.get('disposition') not in ('keep', 'revise', 'revert', 'insufficient_evidence'):
            raise WorkflowError('invalid effect disposition')
        if p.get('benefit_status') not in ('supported', 'not_supported', 'unknown'):
            raise WorkflowError('invalid benefit status')
        if p.get('quality_status') not in ('preserved', 'regressed', 'unknown'):
            raise WorkflowError('invalid quality status')
        refs('analysis_refs')
        lists('counterevidence_refs', 'evidence_refs')
        texts('rationale')
        objects('next_action')
        for key in ('owner', 'action', 'prerequisites', 'acceptance'):
            text(p['next_action'].get(key), 'next_action.' + key)
        if p['disposition'] == 'keep' and (p['benefit_status'] != 'supported' or
                p['quality_status'] != 'preserved' or not p['analysis_refs']):
            raise WorkflowError('keep requires supported benefit, preserved quality and analysis')
        if p.get('due_at') is not None:
            timestamp(p['due_at'])
    elif kind == 'selection':
        texts('population', 'selection_rule', 'rule_version', 'unit', 'purpose')
        refs('selected_refs')
        lists('exclusions', 'strata', 'limitations')
        timestamp(p.get('selected_at'))
        if p.get('purpose') not in ('discovery', 'confirmation', 'calibration'):
            raise WorkflowError('invalid selection purpose')
    elif kind == 'snapshot':
        texts('source', 'source_key')
        objects('raw_ref', 'normalized_ref', 'excerpts_ref', 'projection_ref', 'observations')
        lists('diagnostics')
        integer(p.get('adapter_version'), 'adapter version', 1)
        integer(p.get('capture_bytes'), 'capture bytes')
        if type(p.get('included')) is not bool:
            raise WorkflowError('snapshot inclusion must be boolean')
    elif kind == 'plan':
        if 'invocation_ref' in p:
            RecordRef.from_dict(p['invocation_ref'])
        if type(p.get('plan_format')) is not int or p['plan_format'] != 1:
            raise WorkflowError('unsupported plan format')
        texts('policy_version', 'inventory_digest', 'prior_state_digest', 'next_action')
        timestamp(p.get('captured_at'))
        objects('limits', 'window')
        from .runner import Limits
        Limits.from_dict(p['limits'])
        if timestamp(p['window'].get('end')) < timestamp(p['window'].get('start')):
            raise WorkflowError('plan window ends before it begins')
        lists('enabled_packs', 'questions', 'due_followups', 'sources', 'selected_files', 'coverage_gaps')
        if p.get('planning_inputs_frozen_before_grading') is not True:
            raise WorkflowError('planning provenance required')
