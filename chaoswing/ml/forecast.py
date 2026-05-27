"""Forecasting probe math.

Pure numpy / python. No Django, no torch. Two responsibilities:

  1. Aggregate top-k neighbor probability series into a fixed-length feature
     vector for one forecast time `t`. Every series is filtered through the
     leakage chokepoint before contributing.
  2. Score metrics — Brier, log loss, calibration — on (target, prediction)
     pairs.

The expanding-window logistic itself is reused from
`apps.web.services.ml_hooks.BinaryLogisticRegression`; no point rewriting it.
Ranker.md says "reuse the existing math, do not rewrite."
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timedelta

import numpy as np

from chaoswing.ml._types import TemporalCutoff, _ensure_utc
from chaoswing.ml.leakage import filter_array_before_cutoff
from chaoswing.ml.timeseries import to_numpy_series


NEIGHBOR_FEATURE_NAMES = (
    "neighbor_mean_probability",
    "neighbor_mean_momentum",
    "neighbor_dispersion",
    "neighbor_extreme_count",
    "neighbor_count",
)


def aggregate_neighbor_features(
    neighbors: Sequence[tuple[Sequence[datetime], Sequence[float]]],
    *,
    forecast_time: datetime,
    momentum_window: timedelta = timedelta(hours=24),
    extreme_low: float = 0.3,
    extreme_high: float = 0.7,
) -> list[float]:
    """Aggregate `(timestamps, probabilities)` series into 5 features.

    Strict pre-`forecast_time` discipline: every neighbor's series is filtered
    through `chaoswing.ml.leakage.filter_array_before_cutoff` against a
    `TemporalCutoff(forecast_time)`. Anything at or after `t` is dropped
    before any aggregation touches it.

    Features (in the order `NEIGHBOR_FEATURE_NAMES` lists):
      * mean_probability — mean of each neighbor's last pre-t probability
      * mean_momentum — mean change in probability over `momentum_window`
      * dispersion — stddev of last pre-t probabilities (signal disagreement)
      * extreme_count — neighbors whose last pre-t probability is outside
        [extreme_low, extreme_high]; a "many neighbors are confident" signal
      * neighbor_count — count of neighbors with any usable pre-t data

    Returns a zero vector when no neighbor has pre-t data.
    """
    cutoff = TemporalCutoff(timestamp=forecast_time, label="forecast_t")
    window_start_dt = _ensure_utc(forecast_time) - momentum_window
    window_start_np = np.datetime64(window_start_dt.replace(tzinfo=None), "ns")

    latest_probs: list[float] = []
    momentums: list[float] = []
    extreme_hits = 0

    for timestamps, probs in neighbors:
        ts_arr, p_arr = to_numpy_series(timestamps, probs)
        ts_arr, p_arr = filter_array_before_cutoff(ts_arr, p_arr, cutoff)
        if p_arr.size == 0:
            continue
        latest = float(p_arr[-1])
        latest_probs.append(latest)
        # Momentum over the window: latest minus earliest within [window_start, t).
        window_mask = ts_arr >= window_start_np
        window_p = p_arr[window_mask]
        if window_p.size >= 2:
            momentums.append(float(window_p[-1] - window_p[0]))
        else:
            momentums.append(0.0)
        if latest <= extreme_low or latest >= extreme_high:
            extreme_hits += 1

    if not latest_probs:
        return [0.0] * len(NEIGHBOR_FEATURE_NAMES)

    arr = np.asarray(latest_probs, dtype=np.float64)
    momentum_mean = float(np.mean(momentums)) if momentums else 0.0
    return [
        float(arr.mean()),
        momentum_mean,
        float(arr.std(ddof=0)),
        float(extreme_hits),
        float(len(latest_probs)),
    ]


# ---------------------------------------------------------------------------
# Metric math (pure functions; the existing ResolutionForecastService inlines
# the same formulas — we lift them out so the probe + tests share one source).
# ---------------------------------------------------------------------------


def _clip(p: float, eps: float = 1e-6) -> float:
    return min(max(float(p), eps), 1.0 - eps)


def brier_score(targets: Sequence[int], predictions: Sequence[float]) -> float:
    """Mean squared error between predictions and binary outcomes."""
    if not targets:
        return 0.0
    return sum(
        (float(p) - int(t)) ** 2 for t, p in zip(targets, predictions, strict=True)
    ) / len(targets)


def log_loss(targets: Sequence[int], predictions: Sequence[float]) -> float:
    """Mean binary cross-entropy. Predictions are clipped to (eps, 1-eps)."""
    if not targets:
        return 0.0
    total = 0.0
    for t, p in zip(targets, predictions, strict=True):
        bounded = _clip(p)
        total += -(int(t) * math.log(bounded) + (1 - int(t)) * math.log(1.0 - bounded))
    return total / len(targets)


def calibration_error(
    targets: Sequence[int],
    predictions: Sequence[float],
    *,
    bins: int = 5,
) -> float:
    """Expected calibration error (mean abs deviation across equal-width bins)."""
    if not targets:
        return 0.0
    bucket_size = 1.0 / bins
    sample_count = len(targets)
    error = 0.0
    for bucket_index in range(bins):
        lower = bucket_index * bucket_size
        upper = lower + bucket_size
        bucket = [
            (int(t), float(p))
            for t, p in zip(targets, predictions, strict=True)
            if lower <= p < upper or (bucket_index == bins - 1 and p == 1.0)
        ]
        if not bucket:
            continue
        target_mean = sum(t for t, _ in bucket) / len(bucket)
        pred_mean = sum(p for _, p in bucket) / len(bucket)
        error += (len(bucket) / sample_count) * abs(target_mean - pred_mean)
    return error


def positive_rate(targets: Sequence[int]) -> float:
    if not targets:
        return 0.0
    return sum(int(t) for t in targets) / len(targets)


def accuracy(targets: Sequence[int], predictions: Sequence[float]) -> float:
    if not targets:
        return 0.0
    hits = sum(
        1 for t, p in zip(targets, predictions, strict=True) if int(p >= 0.5) == int(t)
    )
    return hits / len(targets)
