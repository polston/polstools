"""Configurable deterministic predictors for external annotation packets."""

from __future__ import annotations

import importlib

from .text_rules import classify_user_turn


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


def legacy_turn_friction_v1(row):
    """Project the current legacy rule over a redacted annotation row."""
    try:
        prior_chars = int(row.get("context_chars") or len(row.get("context") or ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("annotation row has invalid context length") from exc
    return classify_user_turn(
        str(row.get("user_turn") or "").strip(), prior_chars) or "none"
