"""Ordered native discovery behind the same callback seam as other sources."""
from pathlib import Path
import os


def _jsonl(root, *, prefix='', newest_dates=False, stopped=lambda: False, on_error=None):
    for directory, children, filenames in os.walk(root, followlinks=False, onerror=on_error):
        if stopped():
            return
        if newest_dates:
            # The native date hierarchy sorts lexically, newest first.
            children.sort(reverse=True)
        else:
            def changed(child):
                try:
                    return ((Path(directory) / child).stat().st_mtime_ns, child)
                except OSError:
                    return (0, child)
            children.sort(key=changed, reverse=True)
        # Keep matching non-files visible to callers such as extraction, which
        # reports them as unreadable. Workflow inventory checks file type itself.
        candidates = [Path(directory) / filename for filename in filenames + children
                      if filename.endswith('.jsonl') and filename.startswith(prefix)]
        def changed_file(path):
            try:
                return (path.stat().st_mtime_ns, path.name)
            except OSError:
                return (0, path.name)
        yield from sorted(candidates, key=changed_file, reverse=True)


def claude(root, *, stopped=lambda: False, on_error=None):
    yield from _jsonl(root, stopped=stopped, on_error=on_error)


def codex(root, *, stopped=lambda: False, on_error=None):
    yield from _jsonl(root, prefix='rollout-', newest_dates=True,
                      stopped=stopped, on_error=on_error)
