"""Bounded advisory coordination for local annotation packet writers."""

from contextlib import contextmanager
import errno
import hashlib
import math
import os
from pathlib import Path
import stat
import time
import unicodedata

from .annotation import _external


class AnnotationBusy(RuntimeError):
    """Another cooperating writer still holds the packet lock."""


def packet_lock_path(source):
    """Bounded filename; case and Unicode aliases share a conservative lock."""
    source = Path(source).resolve()
    name = unicodedata.normalize("NFC", source.name).casefold()
    name = unicodedata.normalize("NFC", name)
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return source.with_name(".annotation-" + digest + ".lock")


@contextmanager
def packet_write_lock(source, timeout=5.0, local_lock=None):
    """Serialize cooperating saves; external editors do not honor this lock."""
    try:
        valid_timeout = (type(timeout) in (int, float)
                         and math.isfinite(timeout) and timeout >= 0)
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        raise ValueError("packet lock timeout must be finite and nonnegative")
    deadline = time.monotonic() + timeout
    source = Path(source).resolve()
    _external(source)
    # Keep this inode in place: deleting it would permit independent locks.
    path = packet_lock_path(source)
    _external(path)
    if path.is_symlink():
        raise ValueError("packet lock must not be a symlink")
    open_flags = (os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                  | getattr(os, "O_NONBLOCK", 0))
    fd = os.open(path, open_flags, 0o600)
    acquired = False
    local_acquired = False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("packet lock must be a regular file")
        if os.name == "nt":
            import msvcrt

            def acquire():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

            def release():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(fd, fcntl.LOCK_UN)
        while True:
            try:
                acquire()
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                if time.monotonic() >= deadline:
                    raise AnnotationBusy(
                        "Another packet writer is still saving; try again shortly.") from exc
                time.sleep(min(0.025, max(0, deadline - time.monotonic())))
        if local_lock is not None:
            local_acquired = local_lock.acquire(
                timeout=max(0, deadline - time.monotonic()))
            if not local_acquired:
                raise AnnotationBusy(
                    "The packet is still being read; try again shortly.")
        yield
    finally:
        try:
            if local_acquired:
                local_lock.release()
            if acquired:
                release()
        finally:
            os.close(fd)
