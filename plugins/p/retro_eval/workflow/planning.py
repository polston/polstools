"""Deterministic automatic planning from a frozen inventory and prior state."""
from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
import itertools
import inspect
from os import fsdecode
from pathlib import Path
import stat
import time

from ..adapters.registry import default_registry
from ..schema import LocalIdFactory
from .records import canonical, digest, timestamp
from .runner import Limits
from .sources import resolve_source_path

POLICY_VERSION = '1'
DEFAULT_PACKS = ('diagnosis', 'review', 'design')
ERROR_SAMPLE_LIMIT = 16


def _error_sample(exc, root):
    """Retain a bounded lexical locator without dereferencing or exception prose."""
    relative = None
    if exc.filename is not None:
        try:
            path = Path(fsdecode(exc.filename)).relative_to(root)
            value = path.as_posix()
            if '..' not in path.parts and len(value.encode('utf-8')) <= 4096:
                relative = value
        except (TypeError, ValueError, UnicodeError):
            pass
    return {'exception_type': type(exc).__name__[:64],
            'errno': exc.errno if type(exc.errno) is int else None,
            'relative_path': relative}


def _discovery_order(root, registration, stopped=lambda: False, on_error=None):
    """Honor registered selection, with optional cooperative discovery hooks."""
    try:
        parameters = inspect.signature(registration.discover).parameters
    except (TypeError, ValueError):
        parameters = {}
    hooks = {name: value for name, value in (('stopped', stopped), ('on_error', on_error))
             if name in parameters and parameters[name].kind in (
                 inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)}
    for path in registration.discover(root, **hooks):
        if stopped():
            return
        yield path


def inventory_sources(roots, salt, *, file_limit=1000, adapters=None, hint_reader=None,
                      deadline=None, clock=time.time):
    """Bound enumeration per source; report truncation instead of claiming census.

    Modification time is a retrieval hint only. Eligibility needs timestamps
    from captured bytes. The inventory itself is private and may contain paths.
    """
    limits = Limits(source_files=file_limit)
    adapters = adapters or default_registry()
    ids = LocalIdFactory(salt)
    sources, files = [], []
    names = sorted(roots)
    quotas = {name: file_limit // max(1, len(names)) + int(i < file_limit % max(1, len(names)))
              for i, name in enumerate(names)}
    for name in names:
        root = Path(roots[name]).expanduser()
        report = {'source': name, 'root': str(root), 'enumerated': 0,
                  'truncated': False, 'reason': '', 'available': False,
                  'enumeration_error_count': 0, 'enumeration_errors': [],
                  'enumeration_errors_omitted': 0}
        def unreadable(exc):
            report['reason'] = 'source_enumeration_unreadable'
            report['enumeration_error_count'] += 1
            if len(report['enumeration_errors']) < ERROR_SAMPLE_LIMIT:
                report['enumeration_errors'].append(_error_sample(exc, root))
            else:
                report['enumeration_errors_omitted'] += 1
        stopped = lambda: deadline is not None and clock() >= deadline
        if stopped():
            report.update(truncated=True, reason='planning_deadline_exhausted')
            sources.append(report)
            continue
        try:
            adapter = adapters.get(name)
        except KeyError:
            report['reason'] = 'unregistered_source'
            sources.append(report)
            continue
        try:
            root = resolve_source_path(root)
            report['root'] = str(root)
            try:
                root_is_directory = stat.S_ISDIR(root.stat().st_mode)
            except (FileNotFoundError, NotADirectoryError):
                root_is_directory = False
            if not root_is_directory:
                report['reason'] = 'source_root_unavailable'
                sources.append(report)
                continue
            # The extra item establishes truncation without inspecting contents.
            paths = []
            try:
                paths.extend(itertools.islice(
                    _discovery_order(root, adapter, stopped, unreadable), quotas[name] + 1))
            except OSError as exc:
                # A later failure must not erase already discovered evidence.
                unreadable(exc)
            report['truncated'] = len(paths) > quotas[name]
            if stopped():
                report.update(truncated=True, reason='planning_deadline_exhausted')
            for path in paths[:quotas[name]]:
                if stopped():
                    report.update(truncated=True, reason='planning_deadline_exhausted')
                    break
                try:
                    resolved = resolve_source_path(path)
                    try:
                        relative = resolved.relative_to(root).as_posix()
                    except ValueError:
                        report['reason'] = 'some_source_paths_escape_root'
                        continue
                    file_stat = resolved.stat()
                    if not stat.S_ISREG(file_stat.st_mode):
                        continue
                    entry = {'source': name, 'relative_path': relative,
                        'key': ids.make(name, relative), 'bytes': file_stat.st_size,
                        'mtime_ns': file_stat.st_mtime_ns}
                    if hint_reader is not None:
                        entry['selection_hint'] = hint_reader(name, resolved, root)
                except OSError as exc:
                    unreadable(exc)
                    continue
                files.append(entry)
                report['enumerated'] += 1
            report['available'] = True
        except OSError as exc:
            unreadable(exc)
        sources.append(report)
    files.sort(key=lambda r: (r['source'], -r['mtime_ns'], r['key']))
    return {'inventory_format': 1, 'sources': sources, 'files': files,
            'file_limit': file_limit, 'population_complete': all(
                s['available'] and not s['truncated'] and not s['reason'] for s in sources)}


def plan_run(inventory, *, captured_at, prior=None, limits=None, enabled_packs=DEFAULT_PACKS):
    """No operator-chosen question, window, threshold, or annotation is required."""
    now = timestamp(captured_at)
    prior = prior or {}
    limits = limits or Limits()
    end = now.isoformat().replace('+00:00', 'Z')
    start = prior.get('last_committed_cutoff') or (now - timedelta(days=14)).isoformat().replace('+00:00', 'Z')
    if timestamp(start) > now:
        start = (now - timedelta(days=14)).isoformat().replace('+00:00', 'Z')
    due = [row for row in prior.get('followups', []) if timestamp(row['due_at']) <= now
           and row.get('state') not in ('retired', 'dismissed', 'completed')]
    due.sort(key=lambda r: (not r.get('severe', False), r['due_at'], r['key']))
    source_names = sorted(s['source'] for s in inventory['sources'] if s['available'])
    def eligibility_order(file):
        hint = file.get('selection_hint', {}).get('eligibility', 'unknown')
        return ({'eligible_hint': 0, 'unknown': 1, 'ineligible_hint': 2,
                 'reporting_session': 3}.get(hint, 1), -file['mtime_ns'], file['key'])
    grouped = {name: sorted((f for f in inventory['files'] if f['source'] == name),
                           key=eligibility_order) for name in source_names}
    # Half the file allowance is sufficient for candidate construction; the
    # independent case cap remains the hard upper bound. Fair round-robin keeps
    # a large corpus from consuming the entire first run.
    selected = []
    for position in range(max((len(v) for v in grouped.values()), default=0)):
        for name in source_names:
            if position < len(grouped[name]) and len(selected) < limits.cases:
                selected.append({**grouped[name][position], 'selection_mode': 'recent_retrieval',
                                 'reason': 'source_balanced_recent_inventory'})
    # Reserve one fifth for deterministic exploration of the bounded inventory,
    # including older material needed to observe a sparse harness.
    exploration_n = min(len(selected), max(1, limits.cases // 5))
    chosen = {f['key'] for f in selected}
    others = sorted((f for f in inventory['files'] if f['key'] not in chosen),
                    key=lambda f: digest(canonical([POLICY_VERSION, f['key'], start, end])))
    if others:
        # Replace from the most represented source only, preserving at least one
        # input for each readable source. This is retrieval, not probability sampling.
        replaced = set()
        for new in others[:exploration_n]:
            counts = {name: sum(f['source'] == name for f in selected) for name in source_names}
            eligible = [i for i, f in enumerate(selected) if counts[f['source']] > 1 and i not in replaced]
            if not eligible:
                break
            index = max(eligible, key=lambda i: (counts[selected[i]['source']], i))
            selected[index] = {**new, 'selection_mode': 'exploration', 'reason': 'reserved_noncandidate_retrieval'}
            replaced.add(index)
    coverage_gaps = [{'source': s['source'], 'reason': s['reason'] or 'bounded_inventory_truncated'}
                     for s in inventory['sources'] if s['reason'] or s['truncated'] or not s['available']]
    question_order = [
        {'pack': 'diagnosis', 'question': 'observable_failures_and_useful_practices',
         'reason': 'trace_evidence_available_without_subjective_setup'},
        {'pack': 'review', 'question': 'review_findings_and_verification_gaps',
         'reason': 'separate_confirmed_defects_from_review_agreement'},
        {'pack': 'design', 'question': 'design_claims_and_future_change_evidence',
         'reason': 'separate_current_acceptance_from_maintainability_claims'},
    ]
    plan = {'plan_format': 1, 'policy_version': POLICY_VERSION, 'captured_at': end,
        'window': {'start': start, 'end': end, 'basis': 'last_committed_cutoff' if prior.get('last_committed_cutoff') else 'bootstrap_14_days',
                   'older_sparse_source_evidence': 'retained_as_explicitly_outside_window'},
        'limits': asdict(limits), 'enabled_packs': sorted(set(enabled_packs)),
        'questions': [q for q in question_order if q['pack'] in enabled_packs],
        'due_followups': due[:limits.cards], 'sources': inventory['sources'],
        'selected_files': selected, 'coverage_gaps': coverage_gaps,
        'inventory_digest': digest(canonical(inventory)),
        'prior_state_digest': digest(canonical(prior)),
        'decision_refs': prior.get('decision_refs', []),
        'comparison_policy': 'frozen_replay_if_eligible_otherwise_descriptive',
        'decision_policy': 'quality_guardrails_and_practical_benefit_required',
        'subjective_preference': {'availability': 'not_observed', 'value': None},
        'next_action': 'capture_selected_sources_and_run_eligible_packs' if selected else 'report_coverage_gap_and_retry_on_new_evidence',
        'selection_population_claim': False,
        'planning_inputs_frozen_before_grading': True}
    return plan


def cadence(prior, *, useful_confirmed=0, false_alerts=0, new_evidence=0):
    """Recommend attention, not a schedule; candidate volume is not useful yield.

    A legacy numeric interval alone does not establish benefit. Callers without
    confirmed outcome evidence retain event-triggered inspection and due followups.
    """
    periodic = useful_confirmed > 0 and new_evidence > 0 and false_alerts <= useful_confirmed
    previous_days = prior.get('cadence_days')
    days = None
    if periodic:
        # A recommendation only; real review cost must still justify adoption.
        days = (max(7, min(28, previous_days) // 2)
                if type(previous_days) is int and previous_days > 0 else 7)
    return {'cadence_days': days, 'installed': False,
            'mode': 'periodic_candidate' if periodic else 'event_triggered',
            'reason': 'confirmed_yield_review_cost_before_adoption' if periodic
                      else 'await_incident_new_evidence_or_due_followup'}
