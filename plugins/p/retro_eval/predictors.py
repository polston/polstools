"""Configurable deterministic predictors for external annotation packets."""

from __future__ import annotations

import importlib
import importlib.util
from functools import lru_cache
from pathlib import Path


def load_predictor(specification: str):
    """Load a configured ``module:callable`` without editing CLI dispatch code."""
    module_name, separator, attribute = str(specification).partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("predictor must use module:callable syntax")
    try:
        predictor = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as exc:
        raise ValueError("configured predictor cannot be loaded") from exc
    if not callable(predictor):
        raise ValueError("configured predictor is not callable")
    return predictor


@lru_cache(maxsize=1)
def _legacy_module():
    path = Path(__file__).resolve().parents[1] / "bin" / "retro.py"
    spec = importlib.util.spec_from_file_location("retro_eval_heldout_legacy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def legacy_turn_friction_v1(row):
    """Project the current legacy rule over a redacted annotation row."""
    module = _legacy_module()
    try:
        prior_chars = int(row.get("context_chars") or len(row.get("context") or ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("annotation row has invalid context length") from exc
    return module.classify_user_turn(
        str(row.get("user_turn") or "").strip(), prior_chars) or "none"
