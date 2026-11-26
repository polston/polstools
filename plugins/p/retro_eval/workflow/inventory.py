"""Compare private metadata snapshots without inferring content or task freshness."""

from .analysis import finite
from .errors import WorkflowError


def _count(record, key, minimum=0):
    if key in record and (type(record[key]) is not int or record[key] < minimum):
        raise WorkflowError('invalid inventory ' + key)


def _index(snapshot):
    if not isinstance(snapshot, dict) or type(snapshot.get('inventory_format')) is not int or snapshot['inventory_format'] != 1:
        raise WorkflowError('unsupported inventory snapshot')
    if not isinstance(snapshot.get('sources'), list) or not isinstance(snapshot.get('files'), list):
        raise WorkflowError('inventory requires sources and files')
    _count(snapshot, 'file_limit', 1)
    if 'file_limit' in snapshot and len(snapshot['files']) > snapshot['file_limit']:
        raise WorkflowError('inventory file count exceeds its limit')
    if 'observed_at_epoch_seconds' in snapshot:
        finite(snapshot['observed_at_epoch_seconds'], 'inventory observation time')
    sources, files = {}, {}
    for source in snapshot['sources']:
        if not isinstance(source, dict):
            raise WorkflowError('invalid inventory source')
        name, root = source.get('source'), source.get('root')
        if not isinstance(name, str) or not name or not isinstance(root, str) or not root or name in sources:
            raise WorkflowError('invalid or duplicate inventory source identity')
        if any(type(source.get(k)) is not bool for k in ('available', 'truncated')) or not isinstance(source.get('reason'), str):
            raise WorkflowError('invalid inventory source coverage')
        for key in ('enumerated', 'enumeration_error_count', 'enumeration_errors_omitted'):
            _count(source, key)
        errors = source.get('enumeration_errors', [])
        if not isinstance(errors, list):
            raise WorkflowError('invalid inventory enumeration errors')
        for error in errors:
            if (not isinstance(error, dict) or not isinstance(error.get('exception_type'), str)
                    or not error['exception_type'] or len(error['exception_type']) > 64
                    or error.get('errno') is not None and type(error['errno']) is not int
                    or error.get('relative_path') is not None and not isinstance(error['relative_path'], str)):
                raise WorkflowError('invalid inventory enumeration error')
        count = source.get('enumeration_error_count')
        if count is not None and count != len(errors) + source.get('enumeration_errors_omitted', 0):
            raise WorkflowError('inventory enumeration error counts disagree')
        if (count or errors or source.get('enumeration_errors_omitted')) and not source['reason']:
            raise WorkflowError('inventory errors require a coverage gap')
        sources[name] = source
    for entry in snapshot['files']:
        if not isinstance(entry, dict):
            raise WorkflowError('invalid inventory file')
        source, path = entry.get('source'), entry.get('relative_path')
        if not isinstance(source, str) or source not in sources:
            raise WorkflowError('inventory file lacks a declared source')
        if not isinstance(path, str) or not path or any(part in ('', '.', '..') for part in path.split('/')):
            raise WorkflowError('inventory file requires a relative path')
        if type(entry.get('bytes')) is not int or entry['bytes'] < 0 or type(entry.get('mtime_ns')) is not int:
            raise WorkflowError('invalid inventory file metadata')
        identity = (source, path)
        if identity in files:
            raise WorkflowError('duplicate inventory file identity')
        files[identity] = entry
    for name, source in sources.items():
        if 'enumerated' in source and source['enumerated'] != sum(k[0] == name for k in files):
            raise WorkflowError('inventory enumerated count disagrees with files')
    if 'population_complete' in snapshot:
        complete = snapshot['population_complete']
        expected = all(s['available'] and not s['truncated'] and not s['reason'] for s in sources.values())
        if type(complete) is not bool or complete != expected:
            raise WorkflowError('inventory population coverage disagrees with sources')
    return sources, files


def compare_inventory(previous, current):
    """Root strings are frozen observation identities; do not resolve old paths.

    Salted keys may differ between stores and do not include source-root identity.
    Equal metadata is never a digest or a statement about the underlying contents.
    """
    old_sources, old_files = _index(previous)
    new_sources, new_files = _index(current)
    comparable, incomparable, coverage = set(), [], []
    for name in sorted(set(old_sources) | set(new_sources)):
        old, new = old_sources.get(name), new_sources.get(name)
        if old is None or new is None or old['root'] != new['root']:
            incomparable.append({'source': name, 'reason': 'source_not_in_both_snapshots' if old is None or new is None else 'source_root_changed'})
        else:
            comparable.add(name)
        for label, source in (('previous', old), ('current', new)):
            if source is not None and (not source['available'] or source['truncated'] or source['reason']):
                coverage.append({'snapshot': label, 'source': name,
                                 'available': source['available'], 'truncated': source['truncated'],
                                 'reason': source['reason']})
    added, changed, absent, equal = [], [], [], 0
    for identity in sorted(set(old_files) | set(new_files)):
        source, path = identity
        if source not in comparable:
            continue
        old, new = old_files.get(identity), new_files.get(identity)
        locator = {'source': source, 'relative_path': path}
        if old is None:
            added.append(locator)
        elif new is None:
            absent.append(locator)
        elif any(old[k] != new[k] for k in ('bytes', 'mtime_ns')):
            changed.append({**locator, 'previous': {k: old[k] for k in ('bytes', 'mtime_ns')},
                            'current': {k: new[k] for k in ('bytes', 'mtime_ns')}})
        else:
            equal += 1
    return {'comparison_format': 1, 'comparable_sources': sorted(comparable),
            'incomparable_sources': incomparable, 'coverage_gaps': coverage,
            'newly_observed': added, 'metadata_changed': changed,
            'not_observed_in_current': absent, 'equal_metadata_count': equal,
            'limitation': 'Retrieval hints only: no creation, deletion, unchanged-content or task-eligibility claim.'}
