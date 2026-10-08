"""The environment variables that name a home directory on this platform.

On Windows, Python's Path.home() and os.path.expanduser read USERPROFILE
(then HOMEDRIVE plus HOMEPATH), never HOME, so a test environment that sets
only HOME leaves a child process with no home directory at all.
"""

import os


def home_vars(path):
    """Return every variable that must name ``path`` as the home directory."""
    path = str(path)
    found = {"HOME": path}
    if os.name == "nt":
        drive, rest = os.path.splitdrive(path)
        found.update(USERPROFILE=path, HOMEDRIVE=drive, HOMEPATH=rest or "\\")
    return found
