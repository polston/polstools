#!/usr/bin/env python3
"""Run the shared format controller in on mode from either harness: the
current session by default, or the durable default when called with
"default" and an optional harness name."""

from pathlib import Path
import runpy
import sys


controller = Path(__file__).resolve().parents[3] / "bin" / "format-ctl"
arguments = sys.argv[1:]
if arguments and arguments[0] == "default":
    argv = ["default", "on"]
    if len(arguments) > 1:
        argv += ["--harness", arguments[1]]
else:
    argv = ["on"]
sys.argv = [str(controller)] + argv
runpy.run_path(str(controller), run_name="__main__")
