#!/usr/bin/env python3
"""Compatibility entry point for the shared private effect-record checker."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from retro_eval.effect_records import (DISPOSITIONS, SIGNALS, FieldError,
                                       check_record, main)

if __name__ == "__main__":
    raise SystemExit(main())
