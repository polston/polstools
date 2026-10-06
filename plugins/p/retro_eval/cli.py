"""Shared command-line conventions for the retro-eval entry points.

Every entry point exits 0 when it ran clean and flagged nothing, 1 when it ran
clean and flagged something, and 2 when it could not run. Invalid input and
unreadable files print one ``error:`` line; an unexpected fault keeps its
traceback for diagnosis but still exits 2, so 1 always means "flagged".
"""

from __future__ import annotations

import argparse
import functools
import sys
import traceback

EXIT_CLEAN, EXIT_FLAGGED, EXIT_CANNOT_RUN = 0, 1, 2


def command(main):
    """Wrap a ``main(argv)`` so every failure to run exits 2."""
    @functools.wraps(main)
    def wrapper(argv=None):
        try:
            return main(argv)
        except (ValueError, OSError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return EXIT_CANNOT_RUN
        except Exception:  # noqa: BLE001 -- an unexpected fault is still "could not run"
            traceback.print_exc()
            return EXIT_CANNOT_RUN
    return wrapper


def full_commit(value: str) -> str:
    """argparse type: a full 40- or 64-character hexadecimal commit id."""
    commit = str(value).strip().lower()
    if len(commit) not in {40, 64} or any(
            character not in "0123456789abcdef" for character in commit):
        raise argparse.ArgumentTypeError(
            "a full commit id is required (git rev-parse HEAD), not %r" % value)
    return commit
