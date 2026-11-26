"""Capture source bytes once, then normalize through the existing adapters.

Source-specific observation mappings are deliberately separate from portable
records. The mapping reports actual field coverage, not the profile's promise.
Raw bytes and redacted excerpts remain private artifacts in an external store.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import stat
import tempfile

from ..adapters.registry import default_registry
from ..claude_usage import ClaudeMessageUsage
from ..schema import LocalIdFactory
from ..text_rules import redact
from lib.skill_activation import SESSION_ENV_VARS
from .errors import BudgetExhausted, PolicyDenied, WorkflowError
from .records import canonical, digest, integer, strict_loads, timestamp
from .tool_results import observe_results, VERSION as RESULT_MAPPING_VERSION
from .record_coverage import classify_record, assess_records

MAPPING_VERSION = '5'
ANCHORED_OPEN_SUPPORTED = (os.open in os.supports_dir_fd
                           and hasattr(os, 'O_DIRECTORY')
                           and hasattr(os, 'O_NOFOLLOW'))


def _open_anchored(path):
    """Walk the canonical absolute path without following replacement links."""
    if not ANCHORED_OPEN_SUPPORTED:
        raise PolicyDenied('source capture requires directory-relative no-follow opens on this platform')
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(path.anchor, directory_flags)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, directory_flags, dir_fd=parent)
            os.close(parent)
            parent = child
        return os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW
                       | getattr(os, 'O_NONBLOCK', 0), dir_fd=parent)
    finally:
        os.close(parent)


def open_source(path, root, *, buffering=-1):
    """Return a regular-file reader anchored beneath the authorized source root."""
    root = resolve_source_path(root)
    path = resolve_source_path(path)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PolicyDenied('source path escapes its authorized root') from exc
    descriptor = _open_anchored(path)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PolicyDenied('source capture requires a regular file')
        return os.fdopen(descriptor, 'rb', buffering=buffering)
    except BaseException:
        os.close(descriptor)
        raise


def reporting_session_ids(env=None):
    env = os.environ if env is None else env
    return {env[name].lower() for name in SESSION_ENV_VARS if env.get(name)}


def default_roots():
    home = Path.home()
    return {
        'claude': Path(os.environ.get('CLAUDE_CONFIG_DIR', home / '.claude')) / 'projects',
        'codex': Path(os.environ.get('CODEX_HOME', home / '.codex')) / 'sessions',
        'antigravity': Path(os.environ.get('RETRO_ANTIGRAVITY_HOME',
                                         home / '.gemini' / 'antigravity-cli')) / 'brain',
    }


def resolve_source_path(path):
    """Resolve non-strictly across pathlib versions; stat/open still report loops."""
    path = Path(path)
    try:
        return path.resolve()
    except RuntimeError:
        # Before 3.13 pathlib raises RuntimeError on loops even in non-strict
        # mode. realpath retains the unresolved remainder like newer pathlib.
        return Path(os.path.realpath(path))


def capture(path, root, byte_limit, *, on_read=None):
    """Read at most limit + 1 bytes, rejecting symlink escapes before opening."""
    integer(byte_limit, 'byte limit', 1)
    with open_source(path, root) as handle:
        data = handle.read(byte_limit + 1)
    if on_read is not None:
        on_read(data, len(data) <= byte_limit)
    if len(data) > byte_limit:
        raise BudgetExhausted('source exceeds remaining captured-byte allowance')
    return data


def selection_hint(source, prefix, *, exclude_session_ids=()):
    """A bounded structural retrieval hint, never final inclusion or truth.

    Codex puts explicit thread origin in its metadata header. Other adapters
    retain unknown until full captured-byte normalization establishes eligibility.
    A clipped or malformed header also remains unknown.
    """
    result = {'eligibility': 'unknown', 'method': 'no_header_mapping', 'version': '1'}
    if source != 'codex':
        return result
    result['method'] = 'codex_session_meta_header'
    try:
        first = strict_loads(prefix.splitlines()[0])
    except (WorkflowError, IndexError):
        return result
    if not isinstance(first, dict) or first.get('type') != 'session_meta':
        return result
    meta = first.get('payload')
    if not isinstance(meta, dict):
        return result
    identity = str(meta.get('session_id') or meta.get('id') or '').lower()
    if identity and identity in exclude_session_ids:
        result['eligibility'] = 'reporting_session'
    elif meta.get('thread_source') == 'user':
        result['eligibility'] = 'eligible_hint'
    elif meta.get('thread_source'):
        result['eligibility'] = 'ineligible_hint'
    return result


def parse_rows(data):
    rows, diagnostics = [], []
    for line_no, line in enumerate(data.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = strict_loads(line)
            if not isinstance(value, dict):
                raise WorkflowError('source record is not an object')
            rows.append((line_no, value))
        except WorkflowError:
            diagnostics.append({'line': line_no, 'reason': 'malformed_json_record'})
    return rows, diagnostics


def _observed(value, method, unit, *, field=None, reason='', coverage=None):
    result = {'availability': 'observed', 'value': value, 'method': method,
              'mapping_version': MAPPING_VERSION, 'unit': unit}
    if field:
        result['source_field'] = field
    if coverage:
        result['coverage'] = coverage
    return result


def _missing(reason, method, unit, *, unsupported=False, coverage=None):
    return {'availability': 'not_supported' if unsupported else 'not_observed',
            'value': None, 'method': method, 'mapping_version': MAPPING_VERSION,
            'unit': unit, 'reason': reason, 'coverage': coverage or {}}


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def source_observations(source, rows, diagnostics, result):
    """Profile availability never fills absent accounting or status fields."""
    values = [r for _, r in rows]
    coverage = {'parsed_records': len(rows), 'invalid_records': len(diagnostics)}
    method = source + '.captured_fields'
    observations = {}
    if source == 'claude':
        assistants = [r for r in values if r.get('type') == 'assistant'
                      and isinstance(r.get('message'), dict)]
        # Streaming snapshots may repeat a message id. Only the final snapshot
        # contributes to totals; anonymous messages lack a reliable dedup key.
        usage_snapshots = ClaudeMessageUsage()
        for row in assistants:
            usage_snapshots.add(row['message'])
        usages = usage_snapshots.usages
        for field in ('input_tokens', 'output_tokens', 'cache_read_input_tokens'):
            valid = [u[field] for u in usages
                     if isinstance(u, dict) and _nonnegative_int(u.get(field))]
            c = {'eligible': len(usages), 'observed': len(valid),
                 'duplicate_snapshots': len(assistants) - len(usages),
                 'anonymous_messages': usage_snapshots.anonymous_count}
            observations[field] = (_observed(sum(valid), 'claude.final_message_usage',
                'tokens', field='message.usage.' + field, coverage=c)
                if usages and len(valid) == len(usages) and not diagnostics
                else _missing('missing_or_invalid_message_usage', method, 'tokens', coverage=c))
        observations['completion'] = _observed(result.records[0].status if result.records else 'unknown',
            'claude.assistant_prose_presence', 'category', coverage=coverage)
        observations['origin'] = _observed('explicit_source_marker_required',
            'claude.promptSource_or_origin', 'category')
        observations['population'] = _observed('subagent' if result.is_subagent else 'main',
            'claude.transcript_layout', 'category')
    elif source == 'codex':
        events = [r['payload'] for r in values if r.get('type') == 'event_msg'
                  and isinstance(r.get('payload'), dict)]
        totals = [e['info']['total_token_usage'] for e in events if e.get('type') == 'token_count'
                  and isinstance(e.get('info'), dict)
                  and isinstance(e['info'].get('total_token_usage'), dict)]
        total = totals[-1] if totals else {}
        for field in ('input_tokens', 'output_tokens', 'cached_input_tokens'):
            observations[field] = (_observed(total[field], 'codex.last_cumulative_usage',
                'tokens', field='payload.info.total_token_usage.' + field,
                coverage={'accounting_events': len(totals)})
                if _nonnegative_int(total.get(field)) and not diagnostics
                else _missing('accounting_field_absent_or_invalid', method, 'tokens'))
        terminal = [e for e in events if e.get('type') in ('task_complete', 'turn_aborted')]
        observations['completion'] = (_observed(terminal[-1]['type'],
            'codex.explicit_terminal_event', 'category') if terminal
            else _missing('terminal_event_absent', 'codex.explicit_terminal_event', 'category'))
        if result.machine_message_origins is not None:
            observations['machine_messages'] = _observed(
                list(result.machine_message_origins), 'codex.known_context_or_wrapper',
                'events', coverage=coverage)
        else:
            observations['machine_messages'] = _missing(
                'origin_scan_not_performed', method, 'events')
        observations['origin'] = _observed('heuristic_user_role_after_known_context_and_wrapper_filter',
            'codex.context_and_wrapper_exclusion', 'category')
        observations['population'] = _observed('main', 'codex.thread_source_user', 'category') if result.included else _missing(
            'not_an_included_user_thread', method, 'category')
    elif source == 'antigravity':
        for field in ('input_tokens', 'output_tokens', 'cached_input_tokens'):
            observations[field] = _missing('step_export_has_no_complete_usage_mapping',
                method, 'tokens', unsupported=True)
        observations['completion'] = _observed(result.records[0].status if result.records else 'unknown',
            'antigravity.last_model_prose_presence', 'category', coverage=coverage)
        observations['origin'] = _observed('explicit_user_step_source',
            'antigravity.USER_EXPLICIT', 'category')
        observations['population'] = _missing('export_has_no_population_marker',
            method, 'category', unsupported=True)
    else:
        observations['origin'] = _missing('registered_adapter_requires_observation_mapping', method, 'category')
    stamps = []
    for row in values:
        raw = row.get('created_at' if source == 'antigravity' else 'timestamp')
        if raw:
            try:
                stamps.append(timestamp(raw))
            except WorkflowError:
                pass
    # One explicit common mapping: first to last captured timestamp, including
    # idle time. This is never described as work time or agent active duration.
    observations['captured_span_seconds'] = (_observed(
        (max(stamps) - min(stamps)).total_seconds(), 'captured_timestamp_range', 'seconds',
        coverage={'timestamped_records': len(stamps), 'parsed_records': len(rows)})
        if len(stamps) >= 2 else _missing('insufficient_valid_timestamps', 'captured_timestamp_range', 'seconds'))
    return observations


def _schema_diagnostics(source, rows):
    known = {
        'claude': {'user', 'assistant', 'system', 'summary', 'progress',
                   'file-history-snapshot', 'queue-operation', 'last-prompt',
                   'custom-title', 'agent-name', 'pr-link'},
        'codex': {'session_meta', 'response_item', 'event_msg', 'turn_context',
                  'compacted'},
        'antigravity': {'USER_INPUT', 'PLANNER_RESPONSE', 'ERROR_MESSAGE',
                       'GENERIC', 'CHECKPOINT', 'TASK_BOUNDARY', 'CODE_ACTION',
                       'TOOL_RESPONSE'},
    }.get(source)
    if known is None:
        return []
    diagnostics = []
    for number, row in rows:
        if classify_record(source, row) is not None:
            continue
        if not isinstance(row.get('type'), str) or row['type'] not in known:
            diagnostics.append({'line': number, 'reason': 'unmapped_record_type'})
        elif (source == 'codex' and row.get('type') in
              {'session_meta', 'response_item', 'event_msg'}
              and not isinstance(row.get('payload'), dict)):
            # These adapters skip an unreadable envelope. Preserve that loss
            # alongside usable records rather than certifying complete coverage.
            diagnostics.append({'line': number, 'reason': 'invalid_record_payload'})
    return diagnostics


@dataclass(frozen=True)
class CapturedSource:
    source: str
    source_key: str
    raw_ref: dict
    normalized_ref: dict
    excerpts_ref: dict
    projection_ref: dict
    records: tuple
    excerpts: dict
    observations: dict
    diagnostics: tuple
    included: bool
    exclusion_reason: str
    adapter_version: int
    capture_bytes: int
    result_observations_ref: dict | None = None

    def summary(self):
        return {name: getattr(self, name) for name in (
            'source', 'source_key', 'raw_ref', 'normalized_ref', 'excerpts_ref',
            'projection_ref', 'observations', 'included', 'exclusion_reason',
            'adapter_version', 'capture_bytes', 'result_observations_ref')} | {'diagnostics': list(self.diagnostics)}


def normalize_captured(source, relative_path, data, store, *, adapters=None, derived_limit=None,
                       window_end=None, exclude_session_ids=()):
    """Adapters only see the frozen projection, never a reopened live source."""
    adapters = adapters or default_registry()
    try:
        registration = adapters.get(source)
    except KeyError as exc:
        raise WorkflowError('unregistered source') from exc
    relative = Path(relative_path)
    if relative.is_absolute() or '..' in relative.parts:
        raise PolicyDenied('captured relative path escapes staging root')
    # Invalid interpretation must not erase the exact bounded input snapshot.
    raw_ref = store.publish_artifact(data, 'private_raw')
    rows, diagnostics = parse_rows(data)
    diagnostics.extend(_schema_diagnostics(source, rows))
    if window_end is not None:
        cutoff = timestamp(window_end)
        kept = []
        for line_no, row in rows:
            raw_stamp = row.get('created_at' if source == 'antigravity' else 'timestamp')
            try:
                future = raw_stamp is not None and timestamp(raw_stamp) > cutoff
            except WorkflowError:
                future = False
                diagnostics.append({'line': line_no, 'reason': 'invalid_record_timestamp'})
            if future:
                diagnostics.append({'line': line_no, 'reason': 'after_closed_window'})
            else:
                kept.append((line_no, row))
        rows = kept
    classified_diagnostics, record_coverage = assess_records(source, rows, raw_ref)
    diagnostics.extend(classified_diagnostics)
    projection = b''.join(canonical(row) + b'\n' for _, row in rows)
    runtime = {}
    if registration.accepts_option('excluded_session_ids'):
        runtime['excluded_session_ids'] = tuple(exclude_session_ids)
    adapter = registration.create(store.id_salt, runtime)
    with tempfile.TemporaryDirectory(dir=store.root, prefix='.normalize-') as temp:
        root = Path(temp)
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(projection)
        try:
            result = adapter.read(path, root)
            excerpts = adapter.private_prompt_evidence(path, root, redact) if result.included and hasattr(adapter, 'private_prompt_evidence') else {}
            tool_excerpts = adapter.private_tool_evidence(path, root, redact) if result.included and hasattr(adapter, 'private_tool_evidence') else {}
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            raise WorkflowError('captured source failed adapter validation') from exc
    # New mapping does not alter the legacy normalized representation.
    records = tuple(r.to_dict() for r in result.records)
    observations = source_observations(source, rows, diagnostics, result)
    observations['record_coverage'] = record_coverage
    ids = LocalIdFactory(store.id_salt)
    tool_results = observe_results(source, rows, raw_ref, ids, included=result.included)
    observations['tool_results'] = {
        'availability': tool_results['availability'], 'value': tool_results['coverage'],
        'method': source + '.result_association', 'mapping_version': RESULT_MAPPING_VERSION,
        'unit': 'call_observations', 'reason': tool_results['reason']}
    result_bytes = canonical(tool_results)
    excerpts = {'prompts': excerpts, 'tools': tool_excerpts}
    normalized_bytes = b''.join(canonical(r) + b'\n' for r in records)
    excerpt_bytes = canonical(excerpts)
    if derived_limit is not None and len(normalized_bytes) + len(excerpt_bytes) + len(result_bytes) > derived_limit:
        raise BudgetExhausted('normalized source exceeds remaining derived-byte allowance')
    # The projection is reproducible from the raw capture and parser version.
    # Keeping a second raw copy would consume the derived-store budget merely
    # to remove insignificant source whitespace.
    projection_ref = {'projection_sha256': digest(projection),
                      'method': 'strict_valid_object_jsonl_v1', 'retained': False}
    normalized_ref = store.publish_artifact(normalized_bytes, 'private_metadata')
    excerpts_ref = store.publish_artifact(excerpt_bytes, 'private_redacted')
    result_ref = store.publish_artifact(result_bytes, 'private_metadata')
    return CapturedSource(source, ids.make(source, relative.as_posix()), raw_ref,
        normalized_ref, excerpts_ref, projection_ref, records, excerpts,
        observations, tuple(diagnostics), result.included, result.exclusion_reason,
        adapter.adapter_version, len(data), result_ref)
