"""Bounded anchored inspection packets, including noncandidate contrasts."""
from __future__ import annotations

from .errors import WorkflowError
from .evidence import resolve_evidence
from .packets import excerpt
from .records import Record, RecordRef, canonical, digest, integer, strict_loads, utc_now
from .runner import Runner


def inspect_run(store, *, run_id=None, candidate_key=None, limit=12, excerpt_limit=4000):
    integer(limit, 'inspection limit', 1)
    integer(excerpt_limit, 'excerpt limit', 1)
    if limit > 100 or excerpt_limit > 20000:
        raise WorkflowError('inspection exceeds installed packet bounds')
    runner = Runner(store)
    runs = runner.list_runs()
    if not runs:
        raise WorkflowError('no captured run to inspect')
    run = runner.status(run_id) if run_id else runs[-1]
    outputs = []
    for op in run['operations']:
        if op['state'] == 'completed' and op['unit_key'].startswith('source:'):
            data, _ = store.read_artifact(op['result']['artifact_ref'])
            outputs.append(strict_loads(data))
    buckets = {}
    all_candidates = []
    for output in outputs:
        for pack in output.get('packs', []):
            for candidate in pack.get('candidates', []):
                item = (candidate, output)
                all_candidates.append(item)
                buckets.setdefault((candidate['source'], candidate['category']), []).append(item)
    selected = []
    if candidate_key:
        selected = [item for item in all_candidates if item[0]['key'] == candidate_key]
        if not selected:
            raise WorkflowError('candidate not found in the selected run')
    else:
        ordered = sorted(buckets)
        for depth in range(max((len(v) for v in buckets.values()), default=0)):
            for key in ordered:
                values = sorted(buckets[key], key=lambda item: (-item[0]['priority'], item[0]['key']))
                if depth < len(values) and len(selected) < limit:
                    selected.append(values[depth])
    selected = selected[:limit]
    entries, contrast_entries, case_refs = [], [], []
    for candidate, output in selected:
        evidence = []
        for ref in candidate['evidence_refs']:
            resolved = resolve_evidence(ref, store)
            evidence.append({'reference': ref, 'status': resolved.status,
                'excerpt': excerpt(canonical(resolved.value).decode() if resolved.value is not None
                                   else (resolved.data or b'').decode('utf-8', errors='replace'), excerpt_limit)})
        entries.append({'candidate': candidate, 'evidence': evidence,
            'case_refs': [r for r in output['record_refs'] if r['kind'] == 'case']})
        case_refs.extend(r for r in output['record_refs'] if r['kind'] == 'case')
    # Spend the same bounded contrast allowance on captured cases not already
    # represented by candidate entries before repeating covered cases.
    # This is exploration, not a probability sample or calibrated truth set.
    represented = {output['source_key'] for _, output in selected}
    contrasts_available = 0
    for source in sorted({o['source'] for o in outputs if o.get('included')}):
        contrasts = []
        for output in outputs:
            if output['source'] != source or not output.get('included'):
                continue
            flagged = {ref.get('selector', {}).get('pointer') for candidate, owner in all_candidates
                       if owner['source_key'] == output['source_key'] for ref in candidate['evidence_refs']}
            data, _ = store.read_artifact(output['excerpts_ref'])
            for span in strict_loads(data).get('prompts', {}):
                pointer = '/prompts/' + span
                if pointer not in flagged:
                    ref = {**output['excerpts_ref'], 'selector': {'kind': 'json_pointer', 'pointer': pointer}}
                    contrasts.append((digest(canonical(ref)), ref, output))
        contrasts_available += len(contrasts)
        chosen = []
        contrast_cases = set()
        while contrasts and len(chosen) < 2:
            item = min(contrasts, key=lambda row: (
                row[2]['source_key'] in represented,
                row[2]['source_key'] in contrast_cases, row[0]))
            contrasts.remove(item)
            chosen.append(item)
            represented.add(item[2]['source_key'])
            contrast_cases.add(item[2]['source_key'])
        for _, ref, output in chosen:
            resolved = resolve_evidence(ref, store)
            contrast_entries.append({'source': source, 'source_key': output['source_key'],
                'reference': ref, 'status': resolved.status,
                'excerpt': excerpt(canonical(resolved.value).decode(), excerpt_limit)})
            case_refs.extend(r for r in output['record_refs'] if r['kind'] == 'case')
    unique_refs = {canonical(ref): ref for ref in case_refs}
    # A resumed run can expose new noncandidate families with the same selected
    # candidate keys. Bind the actual display, not just the request parameters,
    # so pilot preparation cannot mistake newly displayed contrasts for holdout.
    exposure = {'candidate_keys': [c['key'] for c, _ in selected],
        'candidate_evidence': [c['evidence_refs'] for c, _ in selected],
        'contrast_evidence': [entry['reference'] for entry in contrast_entries],
        'case_refs': [unique_refs[key] for key in sorted(unique_refs)]}
    selection = Record.create('selection', digest(canonical(['inspection-v3', run['run_id'], exposure,
        limit, excerpt_limit]))[:24], {
        'population': 'bounded captured source operations', 'selection_rule': 'source_category_round_robin_with_unrepresented_case_contrasts',
        'rule_version': '3', 'unit': 'retrieval_candidate_and_prompt_contrast', 'purpose': 'discovery',
        'selected_refs': list(unique_refs.values()), 'exclusions': [], 'strata': [list(k) for k in sorted(buckets)],
        'limitations': ['Assisted discovery exposure; not independent calibration truth or a population sample.'],
        'selected_at': run['plan']['captured_at']}, study_scope='inspections', created_at=run['plan']['captured_at'])
    # JSON tuples are not part of the portable record format.
    existing = store.get(selection.ref)
    if existing is None:
        store.append(selection, None)
    payload = {'inspection_format': 1, 'run_id': run['run_id'],
        'selection_ref': selection.ref.to_dict(), 'presentation_mode': 'assisted',
        'candidates_available': len(all_candidates), 'candidates_selected': len(entries),
        'candidates_omitted': len(all_candidates) - len(entries), 'entries': entries,
        'noncandidate_contrasts': contrast_entries,
        'noncandidate_contrasts_available': contrasts_available,
        'noncandidate_contrasts_omitted': contrasts_available - len(contrast_entries),
        'current_applicability': runner.result_applicability(run['run_id']),
        'next_action': 'Agent records supported interpretations, counterexamples and an actionable test or no-change disposition; do not infer prevalence.'}
    ref = store.publish_artifact(canonical(payload), 'private_redacted')
    return {'inspection_path': str(store.artifact_path(ref['sha256'])),
            'artifact_ref': ref, 'selection_ref': selection.ref.to_dict(),
            'candidates_selected': len(entries), 'contrasts': len(contrast_entries),
            'contrasts_omitted': contrasts_available - len(contrast_entries),
            'next_action': payload['next_action']}
