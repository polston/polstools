"""Small, dependency-free statistics used by deterministic scorers."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def confusion_metrics(
    truth: Iterable[bool], predicted: Iterable[bool | None]
) -> dict[str, int | float | None]:
    truth_values = list(truth)
    predicted_values = list(predicted)
    if len(truth_values) != len(predicted_values):
        raise ValueError("truth and predicted must have the same population")
    pairs = [(actual, guess) for actual, guess in zip(truth_values, predicted_values)
             if guess is not None]
    tp = sum(actual and guess is True for actual, guess in pairs)
    fp = sum(not actual and guess is True for actual, guess in pairs)
    tn = sum(not actual and guess is False for actual, guess in pairs)
    fn = sum(actual and guess is False for actual, guess in pairs)
    scored = len(pairs)
    agreement = _ratio(tp + tn, scored)
    actual_positive = tp + fn
    actual_negative = tn + fp
    predicted_positive = tp + fp
    predicted_negative = tn + fn
    if scored:
        expected = ((actual_positive * predicted_positive)
                    + (actual_negative * predicted_negative)) / (scored * scored)
        kappa = None if expected == 1 else (agreement - expected) / (1 - expected)
    else:
        kappa = None
    return {
        "population": len(truth_values),
        "scored": scored,
        "abstained": len(truth_values) - scored,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "specificity": _ratio(tn, tn + fp),
        "f1": _ratio(2 * tp, 2 * tp + fp + fn),
        "prevalence": _ratio(actual_positive, scored),
        "agreement": agreement,
        "kappa": kappa,
    }


def wilson_interval(successes: int, population: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if population <= 0:
        raise ValueError("population must be positive")
    if not 0 <= successes <= population:
        raise ValueError("successes must be within population")
    rate = successes / population
    denominator = 1 + z * z / population
    centre = (rate + z * z / (2 * population)) / denominator
    margin = (z / denominator) * math.sqrt(
        rate * (1 - rate) / population + z * z / (4 * population * population))
    return max(0.0, centre - margin), min(1.0, centre + margin)


def paired_effect_interval(control, candidate, *, backend, statistic="mean",
                           confidence=0.95, seed=0):
    control = tuple(control)
    candidate = tuple(candidate)
    if len(control) != len(candidate) or not control:
        raise ValueError("paired samples must have the same nonzero population")
    differences = tuple(after - before for before, after in zip(control, candidate))
    functions = {"mean": statistics.fmean, "median": statistics.median}
    try:
        reducer = functions[statistic]
    except KeyError as exc:
        raise ValueError("unsupported paired statistic") from exc
    return {
        "population": len(differences),
        "effect": reducer(differences),
        "interval": tuple(backend.interval(differences, statistic, confidence, seed)),
        "confidence": confidence,
        "backend": str(backend.name),
    }
