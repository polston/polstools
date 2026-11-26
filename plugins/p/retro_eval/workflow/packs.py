"""Trusted domain extensions: retrieval hypotheses and separate evaluation oracles."""
from __future__ import annotations

from dataclasses import dataclass
import re

from ..schema import TraceRecord
from ..taxonomies import repeated_call_candidates
from ..text_rules import classify_user_turn
from ..proposal_report import _json_pointer
from .analysis import finite, ratio
from .errors import WorkflowError
from .records import canonical, digest, integer


@dataclass(frozen=True)
class Pack:
    name: str
    version: str
    analyze: object
    required_capabilities: tuple = ()
    claim_requirements: object = None
    measurement_mappings: object = None


class PackRegistry:
    def __init__(self, packs=()):
        self._packs = {}
        for pack in packs:
            self.register(pack)

    def register(self, pack):
        if not pack.name or not pack.version or not callable(pack.analyze) or pack.name in self._packs:
            raise WorkflowError('invalid or duplicate domain pack')
        self._packs[pack.name] = pack

    def versions(self, names):
        if any(name not in self._packs for name in names):
            raise WorkflowError('unregistered domain pack')
        return {name: self._packs[name].version for name in sorted(set(names))}

    def implementations(self, names, composition):
        """Bind shipped analyzers and configuration, not arbitrary closures.

        Custom callbacks remain usable for fresh runs. Their version labels
        cannot establish unchanged code/state when continuing a partial run.
        """
        self.versions(names)
        shipped = default_packs()._packs.values()
        result = {}
        for name in sorted(set(names)):
            pack = self._packs[name]
            analyzer = next((p.analyze.__name__ for p in shipped
                             if p.analyze is pack.analyze), None)
            result[name] = {
                'analyzer': analyzer,
                'resumable': analyzer is not None and
                             'retro_eval/workflow/packs.py' in composition,
                'required_capabilities': list(pack.required_capabilities),
                'claim_requirements': pack.claim_requirements or {},
                'measurement_mappings': pack.measurement_mappings or {},
            }
        return result

    def run(self, names, context):
        results = []
        for name in sorted(set(names)):
            if name not in self._packs:
                raise WorkflowError('unregistered domain pack')
            pack = self._packs[name]
            missing = [cap for cap in pack.required_capabilities
                       if context.get('capabilities', {}).get(cap) != 'observed']
            result = ({'status': 'abstained', 'reason': 'required_capability_unobserved',
                       'missing': missing, 'candidates': []} if missing else pack.analyze(context))
            claims = {}
            for claim, requirements in (pack.claim_requirements or {}).items():
                gaps = [cap for cap in requirements if context.get('capabilities', {}).get(cap) != 'observed']
                claims[claim] = {'eligible': not gaps, 'missing_requirements': gaps}
            results.append({**result, 'pack': name, 'pack_version': pack.version,
                            'claim_capabilities': claims,
                            'measurement_mappings': pack.measurement_mappings or {}})
        return results


def _anchor(ref, kind, **selector):
    return {**ref, 'selector': {'kind': kind, **selector}}


def _candidate(context, category, fact, interpretation, mechanism, refs, *, positive=False, priority=1):
    # Container bytes may grow while the selected event remains unchanged.
    # Deduplicate by the actual selected evidence and context, not the digest of
    # an entire evolving transcript.
    semantic = []
    for ref in refs:
        selector = ref['selector']
        if selector['kind'] == 'json_pointer':
            value = _json_pointer(context['excerpts'], selector['pointer'])
        elif selector['kind'] == 'normalized_span':
            found = [r for r in context['records'] if r['span_id'] == selector['span_id']]
            if len(found) != 1:
                raise WorkflowError('candidate anchor is absent or ambiguous')
            value = found[0]
        else:
            raise WorkflowError('unsupported candidate selector')
        semantic.append([selector, value])
    key = digest(canonical(['candidate-v2', category, context['source_key'], semantic]))[:24]
    return {'key': key, 'category': category, 'fact': fact,
            'interpretation': interpretation, 'proposed_mechanism': mechanism,
            'status': 'candidate', 'benefit_status': 'unestablished',
            'positive_practice': positive, 'priority': priority,
            'evidence_refs': refs, 'counterevidence_refs': [],
            'source': context['source'], 'split_group': context['source_key'],
            'next_action': 'Inspect anchored context and a noncandidate contrast; confirm or reject before proposing adoption.',
            'owner': 'agent', 'acceptance': 'Independent evidence establishes the interpretation and its scope.',
            'subjective_value': None}


def diagnosis(context):
    candidates = []
    prompts = context['excerpts']['prompts']
    for span, evidence in sorted(prompts.items()):
        label = classify_user_turn(evidence.get('user_turn', ''), evidence.get('context_chars', 0))
        if label in ('correction', 'interrupt'):
            candidates.append(_candidate(context, 'user_' + label,
                'The versioned user-turn retrieval rule flagged this turn.',
                'Possible avoidable correction or interrupted work; intent needs contextual confirmation.',
                'Clarify the specific missed constraint before changing standing instructions.',
                [_anchor(context['excerpts_ref'], 'json_pointer', pointer='/prompts/' + span)], priority=2))
    records = [TraceRecord.from_dict(r) for r in context['records']]
    for repeat in repeated_call_candidates(records):
        if repeat.candidate_class == 'candidate_waste':
            candidates.append(_candidate(context, 'repeated_call',
                'An identical tool signature recurred without an observed intervening mutation.',
                'Possible redundant work; polling, hidden state changes, and changed external inputs remain alternatives.',
                'Reuse an earlier result only when its input and freshness contract still holds.',
                [_anchor(context['normalized_ref'], 'normalized_span', span_id=span.span_id)
                 for span in (repeat.previous, repeat.current)], priority=1))
    for row in context['records']:
        if row.get('span_kind') == 'retro.tool_result' and row.get('status') == 'error':
            kind = row.get('attributes', {}).get('error_kind', 'unknown')
            if kind != 'expected_probe':
                candidates.append(_candidate(context, 'tool_error',
                    'The source marked a tool result as an error.',
                    'An execution error occurred; avoidability and task impact are unestablished.',
                    'Use the source error category to choose a specific preflight or recovery check.',
                    [_anchor(context['normalized_ref'], 'normalized_span', span_id=row['span_id'])], priority=2))
    # Summary-shaped text can be quoted, echoed or returned by a rejected
    # invocation. Retrieval cannot authenticate execution or positive practice.
    # Claude's legacy excerpt map includes both call and result projections;
    # inspect the call once, retaining distinct reruns even with equal output.
    span_kinds = {row['span_id']: row.get('span_kind') for row in context['records']}
    for span, evidence in sorted(context['excerpts'].get('tools', {}).items()):
        if context['source'] == 'claude' and span_kinds.get(span) == 'retro.tool_result':
            continue
        result = evidence.get('tool_result', '')
        command = evidence.get('tool_input', '')
        if re.search(r'\b(test|unittest|pytest|validate|check)\b', command, re.I) and re.search(
                r'\b(?:[1-9][0-9]* passed|Ran [1-9][0-9]* tests?)\b', result):
            candidates.append(_candidate(context, 'verification_evidence',
                'A tool-result excerpt contains test-summary-shaped text.',
                'Execution is unverified: this may be actual check output, a quotation, an echo, or a rejection. Inspect source provenance before treating it as verification.',
                'Establish which checks actually ran, their outcomes and relevance to acceptance criteria; preserve rejected and quoted text as context.',
                [_anchor(context['excerpts_ref'], 'json_pointer', pointer='/tools/' + span)],
                priority=2))
    return {'status': 'completed', 'candidates': candidates,
            'limitations': ['Retrieval rules are not confirmed failures or benefits.',
                            'Fragments do not establish independent workcase prevalence.']}


def _topic_candidates(context, domain, pattern, mechanism):
    candidates = []
    for span, evidence in sorted(context['excerpts']['prompts'].items()):
        if re.search(pattern, evidence.get('user_turn', ''), re.I):
            candidates.append(_candidate(context, domain + '_request',
                'A user-turn excerpt contains a ' + domain + ' request cue.',
                'The request may support a ' + domain + ' evaluation; deliverables and independent oracles must be established.',
                mechanism, [_anchor(context['excerpts_ref'], 'json_pointer', pointer='/prompts/' + span)]))
    return {'status': 'completed', 'candidates': candidates,
            'limitations': ['A request cue does not verify review correctness or design quality.']}


def review_retrieval(context):
    return _topic_candidates(context, 'review', r'\b(review|audit|inspect|critique)\b',
        'Evaluate confirmed findings, missed defects, harmful fixes, and review effort separately.')


def design_retrieval(context):
    return _topic_candidates(context, 'design', r'\b(design|architect|architecture|modular|abstraction)\b',
        'Compare current acceptance and a separately prepared future-change challenge, including a simple alternative.')


def proportionality(context):
    """Detect candidate process waste/ceremony leads from normalized trace records.

    Relies solely on observed NormalizedRecord fields (`tool_kind`, `call_signature`,
    `main_or_subagent`) and `excerpts['tools']`. Abstains cleanly on per-subagent
    reuse leads when the source exposes no subagent dispatch signatures.
    """
    candidates = []
    limitations = [
        'Telemetry leads identify candidate process waste; task risk and legitimacy require contextual inspection.',
    ]
    records = context.get('records') or []
    tools = context.get('excerpts', {}).get('tools', {}) or {}
    span_kinds = {row.get('span_id'): row.get('span_kind') for row in records}

    # 1. Repeated subagent dispatch/message lead (observable only when call_signature carries subagent ops)
    dispatch_counts = {}
    has_subagent_signals = False
    for row in records:
        tool_kind = str(row.get('tool_kind') or '').lower()
        sig = str(row.get('call_signature') or '')
        if tool_kind in ('subagent', 'agent', 'delegation') or re.search(
            r'\b(send_message|followup_task|spawn_agent)\b', sig, re.I
        ):
            has_subagent_signals = True
            dispatch_counts.setdefault(sig or tool_kind, []).append(row['span_id'])
    if not has_subagent_signals:
        limitations.append('Subagent dispatch identity was unobserved in this source; thread-reuse leads were skipped.')
    else:
        for sig, span_ids in sorted(dispatch_counts.items()):
            if len(span_ids) >= 4:
                candidates.append(_candidate(
                    context,
                    'repeated_subagent_dispatch_lead',
                    f'A subagent dispatch signature recurred {len(span_ids)} times in one session.',
                    'Possible recycled reviewer or long-lived worker thread; confirm whether fresh blinded reviews were used.',
                    'Spawn a fresh short-lived read-only reviewer per concrete review instead of recycling threads.',
                    [_anchor(context['normalized_ref'], 'normalized_span', span_id=span_ids[0]),
                     _anchor(context['normalized_ref'], 'normalized_span', span_id=span_ids[-1])],
                    priority=2,
                ))

    # 2. Repo-local scratch sprawl lead
    repo_scratch_spans = []
    scratch_re = re.compile(r'(?:scratch|tmp|probe|snapshot|dump|observation[-_]\d+|audit[-_]v\d+)', re.I)
    for span, evidence in sorted(tools.items()):
        if context.get('source') == 'claude' and span_kinds.get(span) == 'retro.tool_result':
            continue
        cmd = str(evidence.get('tool_input') or '')
        if not cmd or '/tmp/' in cmd or '/private/tmp/' in cmd:
            continue
        if scratch_re.search(cmd) and re.search(r'(?:>>?|tee\b|write_file|mkdir\b|cp\b)', cmd, re.I):
            repo_scratch_spans.append(span)
    if len(repo_scratch_spans) >= 3:
        candidates.append(_candidate(
            context,
            'repo_scratch_sprawl_lead',
            f'Observed {len(repo_scratch_spans)} scratch-like file write commands outside temporary directories.',
            'Possible scratch sprawl inside a repository working tree; verify target paths and cleanup.',
            'Confine temporary diagnostic dumps and scripts to /tmp and remove them at closeout.',
            [_anchor(context['excerpts_ref'], 'json_pointer', pointer='/tools/' + repo_scratch_spans[0])],
            priority=2,
        ))

    # 3. Ceremony-overhead lead (read/review/doc-only spans overwhelming code-edit spans)
    edit_spans = []
    ceremony_spans = []
    for row in records:
        if row.get('span_kind') != 'retro.tool_call':
            continue
        kind = str(row.get('tool_kind') or '').lower()
        sig = str(row.get('call_signature') or '').lower()
        if kind in ('edit', 'write', 'patch') and not re.search(r'\.(?:md|txt)\b', sig):
            edit_spans.append(row['span_id'])
        elif kind in ('read', 'search', 'review') or re.search(r'(?:handoff|roadmap|\.md\b|review)', sig):
            ceremony_spans.append(row['span_id'])
    if edit_spans and len(ceremony_spans) >= max(12, len(edit_spans) * 6):
        candidates.append(_candidate(
            context,
            'ceremony_overhead_lead',
            f'Observed {len(ceremony_spans)} read/review/doc spans alongside {len(edit_spans)} code-edit spans.',
            'Possible verification or documentation ceremony disproportionate to code change size; verify risk tier.',
            'Scale review rounds and doc updates proportionally to diff risk and bundle docs inside the code PR.',
            [_anchor(context['normalized_ref'], 'normalized_span', span_id=ceremony_spans[0]),
             _anchor(context['normalized_ref'], 'normalized_span', span_id=edit_spans[0])],
            priority=1,
        ))

    return {'status': 'completed', 'candidates': candidates, 'limitations': limitations}


def default_packs():
    return PackRegistry([Pack('diagnosis', '2', diagnosis), Pack('review', '1', review_retrieval),
                         Pack('design', '1', design_retrieval), Pack('proportionality', '1', proportionality)])


def evaluate_review(findings, oracle, *, effort_seconds=None, oracle_kind='seeded',
                    oracle_scope=None):
    """Confirm findings separately from capture of a declared reference set.

    Independent truth checks do not establish how the oracle was selected.
    Scope is caller-attributed provenance, not authenticated by this helper.
    """
    if effort_seconds is not None:
        finite(effort_seconds, 'review effort')
    if effort_seconds is not None and effort_seconds < 0:
        raise WorkflowError('negative review effort')
    if oracle_kind not in ('seeded', 'independent_natural'):
        raise WorkflowError('review oracle must be independently established')
    scope = oracle_scope if oracle_scope is not None else (
        'seed_set' if oracle_kind == 'seeded' else 'unknown')
    if not isinstance(scope, str) or scope not in (
            'seed_set', 'independent_reference_set', 'reported_findings', 'unknown'):
        raise WorkflowError('invalid review oracle selection scope')
    if (scope == 'seed_set' and oracle_kind != 'seeded' or
            scope == 'independent_reference_set' and oracle_kind != 'independent_natural'):
        raise WorkflowError('review oracle kind and selection scope disagree')
    consolidated = {}
    for finding in findings:
        key = finding['defect_key']
        item = consolidated.setdefault(key, {'defect_key': key, 'reviewers': set(),
                                            'dispositions': set()})
        item['reviewers'].add(finding['reviewer'])
        item['dispositions'].add(finding.get('disposition', 'unresolved'))
    true_defects = {k for k, value in oracle.items() if value.get('truth') is True}
    confirmed = set(consolidated) & true_defects
    false = {k for k in consolidated if oracle.get(k, {}).get('truth') is False}
    unknown = set(consolidated) - confirmed - false
    known_capture = ratio(len(confirmed), len(true_defects))
    reference_scope = scope in ('seed_set', 'independent_reference_set')
    oracle_unknown = sum(type(value.get('truth')) is not bool for value in oracle.values())
    scoped_recall = reference_scope and not oracle_unknown
    retained = []
    for key, item in sorted(consolidated.items()):
        truth = oracle.get(key, {})
        retained.append({'defect_key': key, 'reviewer_count': len(item['reviewers']),
            'truth': truth.get('truth'), 'severity': truth.get('severity', 'unknown'),
            'usefulness': truth.get('usefulness'), 'dispositions': sorted(item['dispositions']),
            'retain_severe_singleton': key in confirmed and truth.get('severity') == 'severe'})
    return {'findings': retained, 'confirmed': len(confirmed), 'false_positives': len(false),
        'unknown': len(unknown), 'escaped_defects': sorted(true_defects - confirmed),
        'recall': known_capture if scoped_recall else None,
        'known_defect_capture': known_capture, 'oracle_scope': scope,
        'oracle_unknown_truth': oracle_unknown,
        'recall_scope': scope if reference_scope else None,
        'recall_unavailable_reason': ('oracle_selection_does_not_establish_denominator'
            if not reference_scope else 'oracle_truth_incomplete' if oracle_unknown
            else 'no_known_positives' if known_capture is None else None),
        'effort_seconds': effort_seconds,
        'oracle_kind': oracle_kind, 'natural_population_claim': False,
        'harmful_fixes': sum(v.get('harmful_fix_verified') is True for v in oracle.values())}


def evaluate_design(alternatives, challenge, *, maintenance_judgment=None):
    if (challenge.get('prepared_independently') is not True
            or challenge.get('withheld_until_design_frozen') is not True):
        raise WorkflowError('future-change challenge lacks held-out provenance')
    if not challenge.get('evidence_refs'):
        raise WorkflowError('challenge requires evidence')
    results = []
    for alt in alternatives:
        current = alt.get('current_acceptance', 'unknown')
        future = alt.get('future_acceptance', 'unknown')
        if current not in ('pass', 'fail', 'unknown') or future not in ('pass', 'fail', 'unknown'):
            raise WorkflowError('invalid design acceptance')
        costs = [alt.get(name) for name in ('initial_effort', 'change_effort')]
        for cost in costs:
            if cost is not None and finite(cost, 'design effort') < 0:
                raise WorkflowError('negative design effort')
        # Missing historical cost cannot become zero or disqualify a working
        # alternative merely to make an effort ranking possible.
        effort = sum(costs) if all(cost is not None for cost in costs) else None
        if effort is not None:
            finite(effort, 'total design effort')
        results.append({'alternative': alt['key'], 'current_acceptance': current,
            'future_acceptance': future, 'total_effort': effort,
            'initial_effort': costs[0], 'change_effort': costs[1],
            'regressions': alt.get('verified_regressions', []),
            'eligible': current == future == 'pass' and not alt.get('verified_regressions')})
    eligible = [r for r in results if r['eligible']]
    complete = bool(eligible) and all(r['total_effort'] is not None for r in eligible)
    ranked = sorted(eligible, key=lambda r: (r['total_effort'], r['alternative'])) if complete else []
    return {'alternatives': results, 'least_measured_effort': ranked[0]['alternative'] if ranked else None,
            'effort_comparison': ('complete' if complete else 'incomplete' if eligible else 'no_eligible_alternatives'),
            'maintenance_judgment': maintenance_judgment,
            'claim': 'specific_challenge_comparison_only', 'challenge': challenge}
