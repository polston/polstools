"""Automatic local workflow: frozen plan, bounded capture, packets, and receipts."""
from __future__ import annotations

from datetime import datetime, timezone
from collections import Counter
from pathlib import Path
import time
import os
import tempfile

from .errors import WorkflowError
from .importing import candidate_records
from .packs import default_packs
from .packets import make_packet, render_html
from .planning import inventory_sources, plan_run, cadence
from .records import Record, RecordRef, canonical, digest, strict_loads, timestamp
from .registry import Registry
from .runner import Limits, Runner
from .sources import capture, default_roots, normalize_captured, reporting_session_ids, selection_hint
from .sources import resolve_source_path, open_source
from .recurring import STATES, attention_decisions, local_attention, select_cards
from .composition import measurement_composition
from ..adapters.registry import default_registry


def _adapter_options(adapters, names):
    """Snapshot declared options, separate from installed implementation hashes."""
    return strict_loads(canonical({item.name: item.options or {}
                                  for item in adapters if item.name in names}))


def _adapter_implementations(adapters, names, composition):
    """Bind named implementations; arbitrary factories have no frozen identity."""
    return {item.name: {'module': item.module, 'class_name': item.class_name,
                       'factory': item.factory is not None,
                       'module_bound': item.module.replace('.', '/') + '.py' in composition}
            for item in adapters if item.name in names}


def _prior(store):
    row = store.db.execute("SELECT value FROM metadata WHERE key='automatic_state'").fetchone()
    state = strict_loads(row[0]) if row else {}
    decisions = attention_decisions(store)
    state['decision_refs'] = [r.ref.to_dict() for r in decisions]
    state['followups'] = []
    for record in decisions:
        if not record.payload.get('due_at'):
            continue
        operational_state = 'pending_action'
        if record.ref.study_scope == 'recurring':
            operational_state, _, issues = local_attention(record)
            if issues or operational_state not in STATES:
                continue
        state['followups'].append({'key': record.ref.key,
            'due_at': record.payload['due_at'], 'state': operational_state,
            'decision_ref': record.ref.to_dict()})
    return state


def _completed_outputs(runner, run_id, store):
    outputs = []
    for operation in runner.status(run_id)['operations']:
        if operation['unit_key'].startswith('source:') and operation['state'] == 'completed' and operation['result']:
            data, _ = store.read_artifact(operation['result']['artifact_ref'])
            outputs.append(strict_loads(data))
    return outputs


def _interpretation_gaps(outputs):
    """Usable records do not establish complete interpretation of a capture."""
    gaps = []
    for output in outputs:
        diagnostics = output.get('diagnostics', [])
        outside = {d['line'] for d in diagnostics
                   if d.get('reason') == 'after_closed_window' and type(d.get('line')) is int}
        counts = Counter(d['reason'] for d in diagnostics
                         if d.get('line') not in outside)
        if counts:
            gaps.append({'source': output['source'], 'source_key': output['source_key'],
                         'reason': 'source_interpretation_incomplete',
                         'diagnostic_counts': dict(sorted(counts.items()))})
    return gaps


def run_automatic(root, *, roots=None, limits=None, captured_at=None,
                  enabled_packs=('diagnosis', 'review', 'design'), resume=None,
                  clock=time.time, packs=None, adapters=None, invocation_ref=None):
    limits = limits or Limits()
    packs = packs or default_packs()
    adapters = adapters or default_registry()
    packs.versions(enabled_packs)
    started = clock()
    reporting_exclusions = reporting_session_ids()
    captured_at = captured_at or datetime.fromtimestamp(started, timezone.utc).isoformat().replace('+00:00', 'Z')
    store = Registry(Path(root))
    try:
        if invocation_ref is not None and store.get(RecordRef.from_dict(invocation_ref)) is None:
            raise WorkflowError('invocation record is missing')
        runner = Runner(store, clock=clock)
        if resume:
            receipt = runner.status(resume)
            plan = receipt['plan']
            reporting_exclusions.update(plan.get('reporting_session_exclusions', []))
            if invocation_ref is not None and plan.get('invocation_ref') != invocation_ref:
                raise WorkflowError('resumed invocation differs from the frozen plan')
            limits = Limits.from_dict(plan['limits'])
            run_id = resume
            if receipt['state'] not in ('completed', 'failed', 'canceled') and (
                    canonical(plan.get('adapter_options')) != canonical(_adapter_options(
                        adapters, {source['source'] for source in plan['sources']}))):
                raise WorkflowError('adapter options changed; preserve this receipt and resume with its original configuration')
            if receipt['state'] not in ('completed', 'failed', 'canceled'):
                implementations = _adapter_implementations(adapters,
                    {source['source'] for source in plan['sources']}, measurement_composition())
                if plan.get('adapter_implementations') != implementations:
                    raise WorkflowError('adapter implementation changed; preserve this receipt and restore its original registration')
                if any(item['factory'] or not item['module_bound'] for item in implementations.values()):
                    raise WorkflowError('adapter implementation has no frozen resumable identity; preserve this receipt and start a new bounded run')
            if receipt['state'] not in ('completed', 'failed', 'canceled') and (
                    plan.get('execution_composition') != measurement_composition() or
                    plan.get('pack_versions') != packs.versions(plan['enabled_packs'])):
                raise WorkflowError('measurement composition changed; preserve this receipt and start a new bounded run')
            if receipt['state'] not in ('completed', 'failed', 'canceled'):
                implementations = packs.implementations(plan['enabled_packs'], measurement_composition())
                if canonical(plan.get('pack_implementations')) != canonical(implementations):
                    raise WorkflowError('domain pack implementation or configuration changed; preserve this receipt and restore its original registration')
                if any(not item['resumable'] for item in implementations.values()):
                    raise WorkflowError('domain pack implementation has no frozen resumable identity; preserve this receipt and start a new bounded run')
            authorized_roots = roots if roots is not None else default_roots()
            roots = {}
            for source in plan['sources']:
                name = source['source']
                if name not in authorized_roots or resolve_source_path(authorized_roots[name]) != resolve_source_path(source['root']):
                    raise WorkflowError('resumed source root is not in current authorized configuration; use a new bounded run')
                roots[name] = Path(source['root'])
        else:
            roots = roots if roots is not None else default_roots()
            hint_bytes = 0
            hint_refs = []
            # Header hints prevent a burst of delegated threads from exhausting
            # every main-thread slot. They are bounded captured evidence too.
            hint_limit = min(25 * 1024 * 1024, limits.captured_bytes // 4)
            def read_hint(source, path, source_root):
                nonlocal hint_bytes
                if source != 'codex' or hint_bytes >= hint_limit or len(hint_refs) >= limits.source_files // 2 or clock() >= started + limits.runtime_seconds:
                    return selection_hint(source, b'')
                resolved = resolve_source_path(path)
                try:
                    resolved.relative_to(source_root)
                except ValueError:
                    return {'eligibility': 'unknown', 'method': 'path_denied', 'version': '1'}
                with open_source(resolved, source_root) as handle:
                    prefix = handle.readline(min(65536, hint_limit - hint_bytes))
                hint_bytes += len(prefix)
                ref = store.publish_artifact(prefix, 'private_raw')
                hint_refs.append(ref)
                return {**selection_hint(source, prefix, exclude_session_ids=reporting_exclusions),
                        'evidence_ref': ref}
            inventory = inventory_sources(roots, store.id_salt, file_limit=limits.source_files,
                                          hint_reader=read_hint, deadline=started + limits.runtime_seconds,
                                          clock=clock, adapters=adapters)
            plan = plan_run(inventory, captured_at=captured_at, prior=_prior(store),
                            limits=limits, enabled_packs=enabled_packs)
            plan['preflight'] = {'captured_bytes': hint_bytes,
                'files_read': len(hint_refs), 'evidence_refs': hint_refs,
                'inventory_files': len(inventory['files'])}
            plan['execution_composition'] = measurement_composition()
            plan['adapter_options'] = _adapter_options(adapters, roots)
            plan['adapter_implementations'] = _adapter_implementations(
                adapters, roots, plan['execution_composition'])
            plan['reporting_session_exclusions'] = sorted(reporting_exclusions)
            plan['pack_versions'] = packs.versions(enabled_packs)
            plan['pack_implementations'] = packs.implementations(enabled_packs, plan['execution_composition'])
            if invocation_ref is not None:
                plan['invocation_ref'] = invocation_ref
            receipt = runner.begin(plan, started_at=started)
            run_id = receipt['run_id']
            plan_record = Record.create('plan', run_id, plan,
                study_scope='automatic', created_at=plan['captured_at'])
            planning = runner.record_preflight(run_id, plan_record, captured_bytes=hint_bytes,
                                               source_files=len(hint_refs), evidence_refs=hint_refs)
            if planning['status'] != 'completed':
                receipt = runner.finish(run_id, 'failed', planning['reason'])
                return _planning_failure(receipt)
        if receipt['state'] in ('completed', 'failed', 'canceled'):
            if receipt['receipt'].get('reason') == 'planning_output_exceeds_budget':
                return _planning_failure(receipt)
            return _write_report(store, runner, run_id, plan)
        # Inventory failures and truncation remain gaps even when another
        # readable source produces useful evidence. Never advance the cutoff
        # from a partial inventory as if all configured sources were inspected.
        gaps = list(plan.get('coverage_gaps', []))
        names = sorted({f['source'] for f in plan['selected_files']})
        source_byte_limit = (limits.captured_bytes - plan.get('preflight', {}).get('captured_bytes', 0)) // max(1, len(names))
        report_allowance = min(4 * 1024 * 1024, limits.derived_bytes // 4)
        source_derived_limit = (limits.derived_bytes - report_allowance) // max(1, len(names))
        for file in plan['selected_files']:
            status = runner.status(run_id)
            if clock() >= status['deadline']:
                runner.finish(run_id, 'partial', 'deadline_exhausted')
                break
            key = 'source:' + file['key']
            prior_op = next((o for o in status['operations'] if o['unit_key'] == key), None)
            if prior_op and prior_op['state'] == 'completed':
                continue
            # Per-source allowances preserve access to each corpus even when
            # the first source contains one unusually large transcript.
            source_keys = {'source:' + f['key'] for f in plan['selected_files'] if f['source'] == file['source']}
            source_operations = [o for o in status['operations'] if o['unit_key'] in source_keys]
            byte_remaining = source_byte_limit - sum(o['reservation'].get('captured_bytes', 0) for o in source_operations)
            derived_remaining = source_derived_limit - sum(o['reservation'].get('derived_bytes', 0) for o in source_operations)
            capture_allowance = max(1, file['bytes']) + 1
            if capture_allowance > byte_remaining or derived_remaining < 16384:
                gaps.append({'source': file['source'], 'source_key': file['key'], 'reason': 'source_allocation_exhausted'})
                continue
            fp = digest(canonical([file, plan['window'], plan['enabled_packs'], 'workflow-v1']))
            measured = {'derived_bytes': 0}
            failed_capture = {}
            staged_records = []
            derived_before = _derived_size(store)
            def work(file=file, byte_remaining=byte_remaining, derived_remaining=derived_remaining):
                def on_read(data, complete):
                    measured['captured_bytes'] = len(data)
                    failed_capture.update(raw_ref=store.publish_artifact(data, 'private_raw'),
                                          capture_complete=complete, source=file['source'], source_key=file['key'])
                raw = capture(Path(roots[file['source']]) / file['relative_path'], roots[file['source']],
                              max(1, file['bytes']), on_read=on_read)
                captured = normalize_captured(file['source'], file['relative_path'], raw, store,
                                             derived_limit=derived_remaining // 2,
                                             window_end=plan['window']['end'],
                                             exclude_session_ids=reporting_exclusions, adapters=adapters)
                records = candidate_records(captured, created_at=plan['captured_at'])
                # Preserve the original creation envelope for exact candidate
                # replay, but expose no new case until the unit receipt commits.
                for record in records:
                    existing = store.get(record.ref)
                    if existing and existing.payload != record.payload:
                        raise WorkflowError('stable candidate identity has inconsistent payload')
                    staged_records.append(existing or record)
                context = {**captured.summary(), 'records': captured.records, 'excerpts': captured.excerpts,
                           'capabilities': {name: observation['availability'] for name, observation in captured.observations.items()}}
                results = packs.run(plan['enabled_packs'], context) if captured.included else []
                # Bound candidates independently of source length and card count.
                for result in results:
                    candidates = sorted(result.get('candidates', []), key=lambda c: (-c['priority'], c['key']))
                    result['candidates_available'] = len(candidates)
                    result['candidates'] = candidates[:100]
                    result['candidates_omitted'] = max(0, len(candidates) - 100)
                normalized, _ = store.read_artifact(captured.normalized_ref)
                excerpts, _ = store.read_artifact(captured.excerpts_ref)
                result_observations, _ = store.read_artifact(captured.result_observations_ref)
                stamps = [timestamp(r['started_at']) for r in captured.records if r.get('started_at')]
                in_window = any(timestamp(plan['window']['start']) <= t <= timestamp(plan['window']['end']) for t in stamps)
                return {'source': captured.source, 'source_key': captured.source_key,
                    'included': captured.included, 'exclusion_reason': captured.exclusion_reason,
                    'within_window': in_window, 'window_basis': 'captured_record_timestamps',
                    'outside_window_reason': '' if in_window else 'sparse_source_exploration_or_unknown_timestamp',
                    'raw_ref': captured.raw_ref, 'normalized_ref': captured.normalized_ref,
                    'excerpts_ref': captured.excerpts_ref, 'observations': captured.observations,
                    'result_observations_ref': captured.result_observations_ref,
                    'diagnostics': list(captured.diagnostics), 'packs': results,
                    'record_refs': [r.ref.to_dict() for r in records],
                    '_usage': {'captured_bytes': len(raw), 'derived_bytes': len(normalized) + len(excerpts) + len(result_observations) + sum(len(r.encoded) for r in records)}}
            try:
                def failed_usage():
                    measured['derived_bytes'] = max(0, _derived_size(store) - derived_before)
                    return measured
                result = runner.unit(run_id, key, fp, work, reservation={
                    'cases': 1, 'source_files': 1, 'captured_bytes': capture_allowance,
                    'derived_bytes': derived_remaining}, failure_usage=failed_usage,
                    publication_records=lambda: staged_records,
                    failure_result=lambda: {'status': 'failed', **failed_capture})
                if result['status'] != 'completed':
                    gaps.append({'source': file['source'], 'source_key': file['key'], 'reason': result['status']})
            except (WorkflowError, OSError) as exc:
                gaps.append({'source': file['source'], 'source_key': file['key'],
                             'reason': getattr(exc, 'code', type(exc).__name__)})
        outputs = _completed_outputs(runner, run_id, store)
        gaps.extend(_interpretation_gaps(outputs))
        included = [o for o in outputs if o.get('included')]
        state = runner.status(run_id)
        if not included:
            runner.finish(run_id, 'failed', 'no_eligible_evidence')
        elif gaps or any(o['state'] != 'completed' for o in state['operations']) or clock() >= state['deadline']:
            runner.finish(run_id, 'partial', 'coverage_or_budget_gaps')
        else:
            runner.finish(run_id, 'completed')
        with store.transaction():
            store.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',
                ('run_gaps:' + run_id, canonical(gaps)))
        return _write_report(store, runner, run_id, plan)
    finally:
        store.close()


def _derived_size(store):
    artifacts = store.db.execute("SELECT coalesce(sum(byte_count),0) FROM artifacts WHERE content_class != 'private_raw'").fetchone()[0]
    records = store.db.execute('SELECT coalesce(sum(length(body)),0) FROM records').fetchone()[0]
    return artifacts + records


def _planning_failure(receipt):
    return {'run_id': receipt['run_id'], 'state': receipt['state'], 'coverage': {},
            'used': receipt['used'], 'usage_scope': receipt['usage_scope'],
            'report_path': None, 'exit_code': 2,
            'next_action': 'Agent starts a new bounded run with enough derived bytes to retain its plan; the failed receipt preserves observed inventory costs.'}


def _write_report(store, runner, run_id, plan):
    status = runner.status(run_id)
    outputs = _completed_outputs(runner, run_id, store)
    prior = _prior(store)
    source_operations = [o for o in status['operations'] if o['unit_key'].startswith('source:')]
    binding = digest(canonical({'state': status['state'], 'operations': source_operations}))
    binding_key = 'report_binding:' + run_id + ':' + binding
    previous = store.db.execute('SELECT value FROM metadata WHERE key=?', (binding_key,)).fetchone()
    previous_receipt = strict_loads(previous[0]) if previous else None
    previous_cards = previous_receipt['cards'] if previous_receipt else None
    candidates = [candidate for output in outputs for pack in output.get('packs', [])
                  for candidate in pack.get('candidates', [])]
    candidates.sort(key=lambda c: (-c['priority'], not c['positive_practice'], c['key']))
    seen = set(prior.get('seen_candidates', []))
    frozen_decisions = [store.get(RecordRef.from_dict(ref)) for ref in plan.get('decision_refs', [])]
    selection = select_cards(candidates, [r for r in frozen_decisions if r is not None],
        now=plan['captured_at'], seen_candidates=seen,
        seen_followups=prior.get('seen_followups', []), limit=plan['limits']['cards'])
    cards = previous_cards if previous_cards is not None else selection['cards']
    # Reusing a frozen report must not consume the next undisplayed reminder.
    displayed_followups = {c['key'][len('followup:'):] for c in cards
                           if c['category'] == 'due_followup'}
    cases = []
    for output in outputs:
        for ref in output.get('record_refs', []):
            if ref['kind'] == 'case':
                cases.append(store.get(RecordRef.from_dict(ref)))
    packet = make_packet(store, cases, case_limit=min(20, plan['limits']['cases']))
    html_ref = runner.publish_render(run_id, render_html(packet).encode('utf-8'))
    gaps_row = store.db.execute('SELECT value FROM metadata WHERE key=?', ('run_gaps:' + run_id,)).fetchone()
    # Older completed receipts may have omitted inventory gaps from run_gaps.
    # Preserve their terminal state, but surface the frozen plan's known gaps
    # in current inspection rather than repeat the old clean-exit judgment.
    gap_rows = [*plan.get('coverage_gaps', []), *(strict_loads(gaps_row[0]) if gaps_row else []),
                *_interpretation_gaps(outputs), *selection['attention_diagnostics']]
    report_gaps = list({canonical(gap): gap for gap in gap_rows}.values())
    coverage = {}
    from .tool_results import summarize_result_coverage
    from .record_coverage import summarize_record_coverage
    for source in plan['sources']:
        rows = [o for o in outputs if o['source'] == source['source']]
        coverage[source['source']] = {'captured': len(rows), 'included': sum(o['included'] for o in rows),
            'within_window': sum(o['included'] and o['within_window'] for o in rows),
            'diagnostic_records': sum(len(o['diagnostics']) for o in rows),
            'enumeration_truncated': source['truncated'], 'source_reason': source['reason'],
            'tool_results': summarize_result_coverage(rows),
            'record_classification': summarize_record_coverage(rows)}
    source_usage = {key: sum(o['reservation'].get(key, 0) for o in source_operations) for key in status['used']}
    applicability = runner.result_applicability(run_id)
    next_action = (
        'Agent reconciles changed dependencies before acting on historical cards.'
        if not applicability['current'] else
        'Agent inspects the source records the importer could not understand. Preserve the history; fix the importer or obtain a complete export, then start a new bounded run.'
        if any(g['reason'] == 'source_interpretation_incomplete' for g in report_gaps) else
        'Agent inspects local attention metadata through status and records a corrected decision only within existing authority; unknown state does not authorize a due review.'
        if any(g['reason'] == 'local_attention_metadata_unusable' for g in report_gaps) else
        'Agent follows the due card action, prerequisites and acceptance within existing authority.'
        if any(c['category'] == 'due_followup' for c in cards) else
        'Agent reviews anchored candidates and noncandidate contrasts, then records a supported action or no-change disposition. Register a bounded comparison only when consequential uncertainty warrants it.'
        if cards else
        'Agent checks coverage and due follow-ups; rerun only for new evidence or a changed input.')
    report = {'report_format': 1, 'run_id': run_id, 'state': status['state'],
        'coverage': coverage, 'plan': plan, 'used': source_usage,
        'usage_scope': 'source_operation_reservations',
        'run_reason': status['receipt'].get('reason', ''),
        'runtime': {'seconds': status['receipt'].get('elapsed_seconds'),
                    'method': status['receipt'].get('runtime_measure', 'not_observed'),
                    'scope': 'planning_and_source_operations_before_report_rendering'},
        'result_applicability': applicability,
        'gaps': report_gaps,
        'cards': cards, 'candidates_available': len(candidates),
        'candidate_index': [{k: c[k] for k in ('key', 'source', 'category', 'priority', 'positive_practice', 'evidence_refs')} for c in candidates],
        'candidate_details': 'Frozen source-operation artifacts contain full hypotheses and counterevidence.',
        'packet': packet, 'rendered_packet_ref': html_ref,
        'outputs': [{k: v for k, v in o.items() if k not in ('packs', '_usage')} for o in outputs],
        'limitations': ['Candidates require evidence review before adoption.',
                        'Historical fragments do not establish independent case prevalence.',
                        'Source completion does not establish verified delivery.',
                        'No external model calls or mandatory human annotations were used.'],
        'cadence': previous_receipt['cadence'] if previous_receipt else cadence(prior, new_evidence=selection['eligible_new_candidates']),
        'next_action': next_action}
    directory = store.directory('reports', run_id)
    # Content-addressed report revisions preserve history while a resumed run
    # gets a new receipt. Current validity is recomputed even for completed runs.
    encoded = canonical(report) + b'\n'
    ref = runner.publish_view(run_id, report, cards=0 if previous_receipt else len(cards))
    report_path = directory / ('report-' + digest(encoded)[:24] + '.json')
    if not report_path.exists():
        os.link(store.artifact_path(ref['sha256']), report_path)
    descriptor, staged = tempfile.mkstemp(dir=directory, prefix='.report-')
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(canonical({'report': report_path.name, 'artifact_ref': ref}) + b'\n')
        os.replace(staged, directory / 'latest.json')
    finally:
        if os.path.exists(staged):
            os.unlink(staged)
    html_path = directory / ('cases-' + html_ref['sha256'][:24] + '.html')
    if not html_path.exists():
        os.link(store.artifact_path(html_ref['sha256']), html_path)
    with store.transaction():
        store.db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)',
                         (binding_key, canonical({'cards': cards, 'cadence': report['cadence']})))
        # Display suppression applies to partial receipts too; retrying a
        # bounded window must not repeatedly demand review of the same cards.
        # Another report can commit while this report renders. Merge the
        # latest state under the writer transaction, and never let historical
        # inspection move the committed review boundary backward.
        committed = _prior(store)
        state = {**committed,
                 'seen_candidates': sorted(set(committed.get('seen_candidates', [])) |
                                           {c['key'] for c in cards}),
                 'seen_followups': sorted(set(committed.get('seen_followups', [])) |
                                          displayed_followups)}
        cutoff = committed.get('last_committed_cutoff')
        current_window = cutoff is None or timestamp(plan['window']['end']) >= timestamp(cutoff)
        if current_window:
            state['cadence_days'] = report['cadence']['cadence_days']
            if status['state'] == 'completed' and not report_gaps:
                state['last_committed_cutoff'] = plan['window']['end']
        store.db.execute("INSERT OR REPLACE INTO metadata VALUES('automatic_state',?)", (canonical(state),))
    current = runner.status(run_id)
    return {**report, 'report_path': str(report_path),
            'used': current['used'], 'usage_scope': current['usage_scope'],
            'exit_code': 2 if not any(v['included'] for v in coverage.values()) else
                         1 if cards or report_gaps or status['state'] != 'completed' or not report['result_applicability']['current'] else 0}
