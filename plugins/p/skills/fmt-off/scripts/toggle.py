#!/usr/bin/env python3
"""Run the shared format controller in off mode from either harness."""

from pathlib import Path
import runpy
import sys


controller = Path(__file__).resolve().parents[3] / "bin" / "format-ctl"
sys.argv = [str(controller), "off"]
runpy.run_path(str(controller), run_name="__main__")
