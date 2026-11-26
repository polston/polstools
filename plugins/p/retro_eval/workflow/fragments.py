"""Bounded private byte fragments for discovery, never session normalization."""
from __future__ import annotations

import os
from pathlib import Path
import stat

from .errors import PolicyDenied, WorkflowError
from .records import canonical, digest, integer, utc_now
from .registry import external_path
from .sources import resolve_source_path, open_source

MAX_BYTES = 64 * 1024 * 1024


def _metadata(value):
    return {'bytes': value.st_size, 'mtime_ns': value.st_mtime_ns,
            'ctime_ns': value.st_ctime_ns, 'device': value.st_dev,
            'inode': value.st_ino}


def capture_fragment(root, relative_path, *, max_bytes, offset=None, tail=False):
    """Retain exactly one bounded range; metadata equality is not atomicity."""
    integer(max_bytes, 'fragment byte limit', 1)
    if max_bytes > MAX_BYTES:
        raise WorkflowError('fragment byte limit exceeds 64 MiB; use bounded whole-source capture if appropriate')
    if type(tail) is not bool or (offset is None) == (not tail):
        raise WorkflowError('choose exactly one of offset or tail')
    if offset is not None:
        integer(offset, 'fragment offset', 0)
    relative_path = Path(relative_path)
    if relative_path.is_absolute() or '..' in relative_path.parts:
        raise PolicyDenied('fragment file must be relative to its source root')
    root = resolve_source_path(Path(root).expanduser())
    path = resolve_source_path(root / relative_path)
    try:
        resolved_relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise PolicyDenied('fragment source escapes its root') from exc
    if not stat.S_ISREG(path.stat().st_mode):
        raise PolicyDenied('fragment source must be a regular file')
    # NONBLOCK prevents a replacement FIFO from hanging at open on supporting
    # platforms. Recheck the opened descriptor before reading any source bytes.
    with open_source(path, root, buffering=0) as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise PolicyDenied('fragment source must be a regular file')
        before = _metadata(opened)
        start = max(0, before['bytes'] - max_bytes) if tail else offset
        if start > before['bytes']:
            raise WorkflowError('fragment offset is beyond the observed source size')
        handle.seek(start)
        data = handle.read(max_bytes)
        after = _metadata(os.fstat(handle.fileno()))
    path_error = None
    try:
        path_after = _metadata(path.stat())
    except OSError as exc:
        path_after = None
        path_error = {'type': type(exc).__name__, 'errno': exc.errno}
    unchanged = before == after == path_after
    expected = min(max_bytes, before['bytes'] - start)
    receipt = {'fragment_format': 1, 'content_class': 'private_raw',
        'scope': 'discovery_only_not_session_normalization', 'captured_at': utc_now(),
        'source_root': str(root), 'requested_relative_path': relative_path.as_posix(),
        'resolved_relative_path': resolved_relative,
        'selection': 'tail' if tail else 'offset', 'requested_max_bytes': max_bytes,
        'offset': start, 'end_offset_exclusive': start + len(data),
        'bytes_read': len(data), 'sha256': digest(data),
        'source_before': before, 'source_after': after, 'source_path_after': path_after,
        'source_path_error': path_error, 'source_metadata_unchanged': unchanged,
        'short_read': len(data) < expected,
        'omitted_before': start,
        'omitted_after_observed_start_size': max(0, before['bytes'] - start - len(data)),
        'covers_observed_file': unchanged and start == 0 and len(data) == before['bytes'],
        'limitations': ['Metadata equality is an observation, not an atomic snapshot.',
            'Range boundaries may split records or encoded characters; bytes are not parsed or joined.',
            'Missing context is unknown; this fragment establishes no session totals, task links or delivery.']}
    return data, receipt


def write_fragment(root, relative_path, output, *, max_bytes, offset=None, tail=False,
                   protected_paths=()):
    """Publish a new private directory, writing its completion receipt last."""
    output = Path(output).expanduser()
    if output.is_symlink():
        raise PolicyDenied('fragment output must not be a symlink')
    destination = external_path(output)
    if destination.exists():
        raise PolicyDenied('fragment output already exists')
    for protected in (root, *protected_paths):
        protected = resolve_source_path(Path(protected).expanduser())
        if destination == protected or protected in destination.parents:
            raise PolicyDenied('fragment output must be outside source roots and stores')
    data, receipt = capture_fragment(root, relative_path, max_bytes=max_bytes,
                                     offset=offset, tail=tail)
    destination.mkdir(mode=0o700)
    receipt['artifact'] = 'fragment.bin'
    # If publication fails or is interrupted, retain the incomplete directory
    # without a completion receipt. Never overwrite or silently resume it.
    for name, content in (('fragment.bin', data), ('capture.pending.json', canonical(receipt) + b'\n')):
        with os.fdopen(os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    os.link(destination / 'capture.pending.json', destination / 'capture.json')
    (destination / 'capture.pending.json').unlink()
    return {'capture_path': str(destination / 'capture.json'),
        'sha256': receipt['sha256'], 'bytes_read': receipt['bytes_read'],
        'covers_observed_file': receipt['covers_observed_file'],
        'source_metadata_unchanged': receipt['source_metadata_unchanged'],
        'scope': receipt['scope'],
        'next_action': 'Inspect the private receipt and exact byte range before interpreting context; preserve whole-session coverage gaps.'}
