"""Time-series primitives used by the label miner.

Everything here is pure numpy and does not touch Django. The label miner wraps
these to grade co-movement; the forecasting probe reuses the alignment helpers.

`statsmodels` is imported lazily inside `granger_causality_pvalue` because
Granger is only used when callers explicitly ask for it.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

import numpy as np

from chaoswing.ml._types import TemporalCutoff, _ensure_utc
from chaoswing.ml.leakage import filter_array_before_cutoff


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------


def to_numpy_series(
    timestamps: Sequence[datetime],
    values: Sequence[float],
) -> tuple[np.ndarray, np.ndarray]:
    """Convert python lists to sorted numpy arrays of (datetime64[ns], float64)."""
    if len(timestamps) != len(values):
        raise ValueError("timestamps and values must have the same length")
    if not timestamps:
        return (
            np.array([], dtype="datetime64[ns]"),
            np.array([], dtype=np.float64),
        )
    ts = np.array(
        [np.datetime64(_ensure_utc(t).replace(tzinfo=None), "ns") for t in timestamps],
        dtype="datetime64[ns]",
    )
    vals = np.asarray(values, dtype=np.float64)
    order = np.argsort(ts, kind="stable")
    return ts[order], vals[order]


def resample_to_grid(
    timestamps: np.ndarray,
    values: np.ndarray,
    *,
    freq: timedelta,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Forward-fill onto a uniform grid of width `freq`.

    Each grid cell takes the latest value observed at or before its tick;
    cells before the first observation are NaN.
    """
    if timestamps.shape[0] == 0:
        return (
            np.array([], dtype="datetime64[ns]"),
            np.array([], dtype=np.float64),
        )
    step = np.timedelta64(int(freq.total_seconds() * 1_000_000_000), "ns")
    grid_start = np.datetime64(_ensure_utc(start).replace(tzinfo=None), "ns") if start else timestamps[0]
    grid_end = np.datetime64(_ensure_utc(end).replace(tzinfo=None), "ns") if end else timestamps[-1]
    if grid_end < grid_start:
        return (
            np.array([], dtype="datetime64[ns]"),
            np.array([], dtype=np.float64),
        )
    grid = np.arange(grid_start, grid_end + step, step, dtype="datetime64[ns]")
    idx = np.searchsorted(timestamps, grid, side="right") - 1
    out = np.full(grid.shape, np.nan, dtype=np.float64)
    valid = idx >= 0
    out[valid] = values[idx[valid]]
    return grid, out


def align_series(
    series_a: tuple[np.ndarray, np.ndarray],
    series_b: tuple[np.ndarray, np.ndarray],
    *,
    freq: timedelta = timedelta(minutes=5),
    cutoff: TemporalCutoff | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Resample two series onto a shared grid and drop NaN rows.

    If `cutoff` is provided, both inputs are trimmed to strictly-before-cutoff
    rows first. Returns `(grid, a_values, b_values)`.
    """
    ts_a, val_a = series_a
    ts_b, val_b = series_b
    if cutoff is not None:
        ts_a, val_a = filter_array_before_cutoff(ts_a, val_a, cutoff)
        ts_b, val_b = filter_array_before_cutoff(ts_b, val_b, cutoff)
    if ts_a.size == 0 or ts_b.size == 0:
        empty = np.array([], dtype="datetime64[ns]")
        empty_v = np.array([], dtype=np.float64)
        return empty, empty_v, empty_v
    start = max(ts_a[0], ts_b[0]).astype("datetime64[ns]")
    end = min(ts_a[-1], ts_b[-1]).astype("datetime64[ns]")
    if end < start:
        empty = np.array([], dtype="datetime64[ns]")
        empty_v = np.array([], dtype=np.float64)
        return empty, empty_v, empty_v
    start_dt = start.astype("M8[us]").astype("O")
    end_dt = end.astype("M8[us]").astype("O")
    grid_a, va = resample_to_grid(ts_a, val_a, freq=freq, start=start_dt, end=end_dt)
    grid_b, vb = resample_to_grid(ts_b, val_b, freq=freq, start=start_dt, end=end_dt)
    mask = ~(np.isnan(va) | np.isnan(vb))
    return grid_a[mask], va[mask], vb[mask]


# ---------------------------------------------------------------------------
# Co-movement statistics
# ---------------------------------------------------------------------------


def _zscore(arr: np.ndarray) -> np.ndarray:
    if arr.size == 0:
        return arr
    mean = float(np.mean(arr))
    std = float(np.std(arr))
    if std == 0.0:
        return np.zeros_like(arr)
    return (arr - mean) / std


def cross_correlation(
    x: np.ndarray,
    y: np.ndarray,
    *,
    max_lag: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Pearson cross-correlation of `x` and `y` over lags in [-max_lag, +max_lag].

    Returns `(lags, xcorr)`. The inputs must already be on a uniform grid.
    """
    if x.shape != y.shape:
        raise ValueError("x and y must be the same shape")
    if max_lag < 0:
        raise ValueError("max_lag must be non-negative")
    n = x.size
    if n == 0:
        return np.array([], dtype=np.int64), np.array([], dtype=np.float64)
    max_lag = min(max_lag, n - 1)
    xz = _zscore(x)
    yz = _zscore(y)
    lags = np.arange(-max_lag, max_lag + 1, dtype=np.int64)
    out = np.zeros(lags.size, dtype=np.float64)
    for i, lag in enumerate(lags):
        if lag < 0:
            a = xz[: n + lag]
            b = yz[-lag:]
        elif lag > 0:
            a = xz[lag:]
            b = yz[: n - lag]
        else:
            a, b = xz, yz
        if a.size == 0:
            out[i] = 0.0
        else:
            out[i] = float(np.mean(a * b))
    return lags, out


def peak_lag(lags: np.ndarray, xcorr: np.ndarray) -> tuple[int, float]:
    """Return `(best_lag, peak_value)` where peak is taken on absolute value."""
    if xcorr.size == 0:
        return 0, 0.0
    idx = int(np.argmax(np.abs(xcorr)))
    return int(lags[idx]), float(xcorr[idx])


def granger_causality_pvalue(
    x: np.ndarray,
    y: np.ndarray,
    *,
    max_lag: int,
) -> float | None:
    """Granger causality p-value: does `x` help predict `y`?

    Returns `None` if statsmodels is unavailable or the test cannot be run on
    the given input (too short, constant series).
    """
    if x.size < max(20, 4 * max_lag) or x.shape != y.shape:
        return None
    if float(np.std(x)) == 0.0 or float(np.std(y)) == 0.0:
        return None
    try:
        from statsmodels.tsa.stattools import grangercausalitytests
    except ImportError:
        return None

    data = np.column_stack([y, x])
    try:
        result = grangercausalitytests(data, maxlag=max_lag, verbose=False)
    except Exception:
        return None
    p_values = [float(result[lag][0]["ssr_ftest"][1]) for lag in result]
    return float(min(p_values)) if p_values else None


def shock_co_fraction(
    x: np.ndarray,
    y: np.ndarray,
    *,
    z_threshold: float = 2.0,
) -> float:
    """Fraction of shocks in `x` that coincide with a shock in `y`."""
    if x.size < 3 or x.shape != y.shape:
        return 0.0
    dx = np.diff(x)
    dy = np.diff(y)
    if float(np.std(dx)) == 0.0 or float(np.std(dy)) == 0.0:
        return 0.0
    sx = np.abs(_zscore(dx)) > z_threshold
    sy = np.abs(_zscore(dy)) > z_threshold
    if sx.sum() == 0:
        return 0.0
    return float(np.sum(sx & sy)) / float(np.sum(sx))
