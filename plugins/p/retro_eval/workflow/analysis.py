"""Small auditable analysis primitives; no universal workflow-quality score."""
from __future__ import annotations

import math
import statistics

from .errors import WorkflowError
from .records import canonical, digest, integer


def finite(value, name='value'):
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise WorkflowError(name + ' must be a finite number')
    return value


def ratio(a, b):
    return a / b if b else None


def valid_interval(value):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise WorkflowError('interval must contain two endpoints')
    low, high = (finite(v, 'interval endpoint') for v in value)
    if low >= high:
        raise WorkflowError('interval must be ordered and nondegenerate')
    return [low, high]


def classifier_metrics(rows):
    """Truth unknown is not negative, and abstention is not a scored success."""
    counts = dict(tp=0, fp=0, fn=0, tn=0, abstained=0, unknown_truth=0)
    positives = known = 0
    for row in rows:
        truth, prediction = row.get('truth'), row.get('prediction')
        if truth is not None and type(truth) is not bool:
            raise WorkflowError('truth must be boolean or unknown')
        if prediction is not None and type(prediction) is not bool:
            raise WorkflowError('prediction must be boolean or abstention')
        if truth is None:
            counts['unknown_truth'] += 1
            continue
        known += 1
        positives += int(truth)
        if prediction is None:
            counts['abstained'] += 1
        else:
            counts['tp' if truth and prediction else 'fn' if truth else
                   'fp' if prediction else 'tn'] += 1
    scored = known - counts['abstained']
    return {**counts, 'known_truth': known, 'scored': scored,
        'coverage': ratio(scored, known),
        'precision': ratio(counts['tp'], counts['tp'] + counts['fp']),
        'selective_recall': ratio(counts['tp'], counts['tp'] + counts['fn']),
        'all_truth_capture': ratio(counts['tp'], positives),
        'whole_population_validity': False}


def arm_summary(assigned):
    """All assigned units remain, including immature follow-ups and missing data."""
    counts = {'success': 0, 'failure': 0, 'unknown': 0, 'pending': 0}
    seen = set()
    for row in assigned:
        key = row['unit']
        if key in seen:
            raise WorkflowError('duplicate assigned unit')
        seen.add(key)
        status = row.get('status', 'unknown')
        if status not in counts:
            raise WorkflowError('invalid assigned outcome')
        counts[status] += 1
    n = sum(counts.values())
    missing = counts['unknown'] + counts['pending']
    return {**counts, 'assigned': n, 'observed_rate': ratio(counts['success'], n - missing),
        'realized_lower': ratio(counts['success'], n),
        'realized_upper': ratio(counts['success'] + missing, n),
        'reason': 'empty_population' if not n else 'followup_pending' if counts['pending'] else ''}


def missingness_bounds(baseline, intervention):
    a, b = arm_summary(baseline), arm_summary(intervention)
    if not a['assigned'] or not b['assigned']:
        bounds = None
    else:
        bounds = [b['realized_lower'] - a['realized_upper'],
                  b['realized_upper'] - a['realized_lower']]
    return {'baseline': a, 'intervention': b, 'effect_bounds': bounds,
            'bounds_type': 'realized_cohort_identification_not_confidence_interval',
            'reason': 'empty_population' if bounds is None else ''}


def paired_effect(rows, *, method='descriptive', confidence=0.95, value_bounds=None):
    """One contribution per independent family, requiring exact replay inputs.

    Within-family retries are averaged before the population mean. The optional
    Hoeffding interval assumes independent bounded family differences; it is
    valid at a single prespecified sample size, not uniformly over repeated
    looks. This helper does not establish those design or stopping assumptions.
    """
    finite(confidence, 'confidence')
    if not 0 < confidence < 1:
        raise WorkflowError('confidence must lie strictly between zero and one')
    groups, excluded = {}, []
    for row in rows:
        if (not row.get('artifact_digest') or not row.get('scope_digest') or
                row.get('artifact_digest') != row.get('comparator_artifact_digest') or
                row.get('scope_digest') != row.get('comparator_scope_digest')):
            excluded.append({'unit': row.get('unit'), 'reason': 'unmatched_artifact_or_scope'})
            continue
        if row.get('baseline') is None or row.get('intervention') is None:
            excluded.append({'unit': row.get('unit'), 'reason': 'missing_paired_outcome'})
            continue
        effect = finite(finite(row['intervention']) - finite(row['baseline']), 'paired difference')
        groups.setdefault(row['split_group'], []).append(effect)
    values = [finite(statistics.mean(groups[key]), 'family mean') for key in sorted(groups)]
    n = len(values)
    point = finite(statistics.mean(values), 'effect mean') if values else None
    interval = None
    reason = 'descriptive_only'
    if method == 'hoeffding' and values and value_bounds is not None:
        low, high = valid_interval(value_bounds)
        if any(not low <= x <= high for x in values):
            raise WorkflowError('paired effects violate declared bounds')
        width = finite(finite(high - low, 'difference range') *
                       math.sqrt(math.log(2 / (1 - confidence)) / (2 * n)), 'interval width')
        interval = [max(low, point - width), min(high, point + width)]
        reason = 'requires_independent_bounded_assignment_units'
    elif method != 'descriptive':
        reason = 'interval_method_unavailable_or_missing_bounds'
    return {'effect': point, 'independent_units': n, 'paired_attempts': sum(map(len, groups.values())),
            'interval': interval, 'confidence': confidence if interval else None,
            'interval_method': method if interval else None,
            'coverage_scope': 'single_prespecified_look' if interval else None,
            'limitation': 'empty_population' if not values else reason, 'excluded': excluded}


def standardized_binary(strata):
    """Fixed equal-stratum target; an observational adjustment, not randomization."""
    a_total = b_total = a_success = b_success = 0
    effects = []
    for row in strata:
        for key in ('baseline_n', 'intervention_n', 'baseline_success', 'intervention_success'):
            integer(row[key], key)
        a, b = row['baseline_n'], row['intervention_n']
        sa, sb = row['baseline_success'], row['intervention_success']
        if sa > a or sb > b:
            raise WorkflowError('success count exceeds assigned count')
        a_total += a
        b_total += b
        a_success += sa
        b_success += sb
        if a and b:
            effects.append(sb / b - sa / a)
    return {'raw_difference': b_success / b_total - a_success / a_total if a_total and b_total else None,
            'standardized_difference': statistics.mean(effects) if len(effects) == len(strata) and effects else None,
            'target': 'equal_stratum_weights', 'claim_type': 'observational'}


def decision(*, analysis, severe_regression=False, quality_interval=None,
             quality_harm_margin=0, practical_benefit=0, direction='lower',
             design_valid=False, measurement_valid=False, explicit_preference=False,
             mechanical_repair=False):
    """Keep requires benefit AND quality evidence; absence of significance is not safety."""
    for name, value in (('severe_regression', severe_regression),
                        ('design_valid', design_valid), ('measurement_valid', measurement_valid),
                        ('explicit_preference', explicit_preference), ('mechanical_repair', mechanical_repair)):
        if type(value) is not bool:
            raise WorkflowError(name + ' must be boolean')
    finite(quality_harm_margin)
    finite(practical_benefit)
    if quality_harm_margin < 0 or practical_benefit < 0:
        raise WorkflowError('decision margins must be nonnegative')
    if direction not in ('lower', 'higher'):
        raise WorkflowError('invalid benefit direction')
    if severe_regression:
        return {'disposition': 'revert', 'basis': 'verified_severe_guardrail_failure', 'benefit_status': 'not_determinative'}
    if mechanical_repair:
        return {'disposition': 'repair', 'basis': 'direct_mechanical_check', 'benefit_status': 'not_an_efficacy_claim'}
    if explicit_preference:
        return {'disposition': 'provisional', 'basis': 'explicit_operator_preference', 'benefit_status': 'unknown'}
    interval = valid_interval(analysis.get('interval'))
    quality_interval = valid_interval(quality_interval)
    quality_safe = quality_interval is not None and quality_interval[1] <= quality_harm_margin
    improved = interval is not None and (interval[1] < -practical_benefit if direction == 'lower'
                                         else interval[0] > practical_benefit)
    if design_valid and measurement_valid and improved and quality_safe:
        return {'disposition': 'keep', 'basis': 'preregistered_benefit_and_quality_bounds', 'benefit_status': 'supported_within_scope'}
    return {'disposition': 'insufficient_evidence', 'basis': 'benefit_quality_or_design_not_established', 'benefit_status': 'unknown'}


def confirmation_status(discovery_groups, confirmation_groups, *, tuned=True):
    overlap = sorted(set(discovery_groups) & set(confirmation_groups))
    return {'independent_confirmation': bool(confirmation_groups) and not overlap,
            'overlap_groups': overlap, 'reuse_role': 'regression_only' if overlap and tuned else 'confirmation_candidate'}


def detector_effect(baseline, intervention, *, arm_calibration=None):
    """Detection rates are descriptive unless arm-specific truth supports more."""
    for arm in (baseline, intervention):
        integer(arm['assigned'], 'assigned')
        integer(arm['detected'], 'detected')
        if arm['detected'] > arm['assigned']:
            raise WorkflowError('detected count exceeds population')
    effect = (intervention['detected'] / intervention['assigned'] -
              baseline['detected'] / baseline['assigned']) if baseline['assigned'] and intervention['assigned'] else None
    result = {'detected_rate_difference': effect, 'true_rate_difference': None,
              'claim_type': 'descriptive_detection',
              'reason': 'arm_specific_measurement_validity_not_established'}
    if arm_calibration and arm_calibration.get('complete_truth') is True:
        a, b = arm_calibration['baseline_true'], arm_calibration['intervention_true']
        integer(a, 'baseline truth')
        integer(b, 'intervention truth')
        if a > baseline['assigned'] or b > intervention['assigned']:
            raise WorkflowError('true count exceeds population')
        result['true_rate_difference'] = b / intervention['assigned'] - a / baseline['assigned'] if effect is not None else None
        result['reason'] = 'complete_truth_for_this_cohort_only'
    return result
