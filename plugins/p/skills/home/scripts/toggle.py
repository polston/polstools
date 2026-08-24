#!/usr/bin/env python3
"""Run the shared skill-profile controller in home mode."""

from pathlib import Path
import runpy
import sys


controller = Path(__file__).resolve().parents[3] / "bin" / "skill-profile-ctl"
sys.argv = [str(controller), "home"]
runpy.run_path(str(controller), run_name="__main__")
