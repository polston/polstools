"""Optional SciPy implementation of bias-corrected accelerated intervals."""

from __future__ import annotations


class ScipyBcaBackend:
    name = "scipy-bca"

    def __init__(self, *, resamples=9999):
        if resamples < 100:
            raise ValueError("resamples must be at least 100")
        self.resamples = resamples

    def interval(self, values, statistic, confidence, seed):
        try:
            import numpy
            from scipy.stats import bootstrap
        except ImportError as exc:
            raise RuntimeError("SciPy BCa backend requires numpy and scipy") from exc
        reducers = {
            "mean": lambda sample: numpy.mean(sample),
            "median": lambda sample: numpy.median(sample),
        }
        try:
            reducer = reducers[statistic]
        except KeyError as exc:
            raise ValueError("unsupported paired statistic") from exc
        result = bootstrap(
            (numpy.asarray(values, dtype=float),), reducer,
            confidence_level=confidence, n_resamples=self.resamples,
            method="BCa", random_state=seed, vectorized=False,
        )
        return float(result.confidence_interval.low), float(result.confidence_interval.high)
