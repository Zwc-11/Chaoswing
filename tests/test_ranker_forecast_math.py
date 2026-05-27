"""Unit tests for `chaoswing.ml.forecast`.

The pure-math layer behind the forecasting probe. Two invariants pinned here:

  1. `aggregate_neighbor_features` cannot accept post-cutoff data. Every
     observation goes through `chaoswing.ml.leakage.filter_array_before_cutoff`
     before it contributes to any aggregate.
  2. `brier_score`, `log_loss`, and `calibration_error` agree with the
     existing ResolutionForecastService formulas on hand-computed inputs.

These tests run without Django state — they hit the pure-numpy functions.
"""
from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta, timezone

from chaoswing.ml.forecast import (
    NEIGHBOR_FEATURE_NAMES,
    accuracy,
    aggregate_neighbor_features,
    brier_score,
    calibration_error,
    log_loss,
    positive_rate,
)


FORECAST_T = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def _series(*offsets_with_values: tuple[timedelta, float]) -> tuple[list[datetime], list[float]]:
    timestamps = [FORECAST_T + offset for offset, _ in offsets_with_values]
    values = [val for _, val in offsets_with_values]
    return timestamps, values


class AggregateNeighborFeaturesLeakageTests(unittest.TestCase):
    def test_post_cutoff_observations_dropped(self) -> None:
        """A neighbor whose only observations are at-or-after `t` contributes nothing."""
        post_only = _series(
            (timedelta(0), 0.99),
            (HOUR, 0.99),
        )
        valid = _series(
            (-2 * HOUR, 0.4),
            (-HOUR, 0.5),
        )
        features = aggregate_neighbor_features(
            [post_only, valid],
            forecast_time=FORECAST_T,
        )
        names = dict(zip(NEIGHBOR_FEATURE_NAMES, features, strict=True))
        # Only the valid neighbor counts; mean prob is its latest pre-t value.
        self.assertEqual(names["neighbor_count"], 1.0)
        self.assertAlmostEqual(names["neighbor_mean_probability"], 0.5)

    def test_empty_neighbors_returns_zero_vector(self) -> None:
        features = aggregate_neighbor_features([], forecast_time=FORECAST_T)
        self.assertEqual(features, [0.0] * len(NEIGHBOR_FEATURE_NAMES))

    def test_neighbor_with_no_pre_t_data_is_skipped(self) -> None:
        post_only = _series((timedelta(0), 0.99))
        features = aggregate_neighbor_features(
            [post_only], forecast_time=FORECAST_T
        )
        names = dict(zip(NEIGHBOR_FEATURE_NAMES, features, strict=True))
        self.assertEqual(names["neighbor_count"], 0.0)
        self.assertEqual(names["neighbor_mean_probability"], 0.0)

    def test_momentum_over_window(self) -> None:
        """Within the momentum window, momentum is latest minus earliest."""
        neighbor = _series(
            (-12 * HOUR, 0.3),  # in-window earliest
            (-HOUR, 0.7),       # in-window latest
        )
        features = aggregate_neighbor_features(
            [neighbor],
            forecast_time=FORECAST_T,
            momentum_window=timedelta(hours=24),
        )
        names = dict(zip(NEIGHBOR_FEATURE_NAMES, features, strict=True))
        self.assertAlmostEqual(names["neighbor_mean_momentum"], 0.4)

    def test_dispersion_and_extreme_count(self) -> None:
        """One extreme-high, one extreme-low, one centrist => dispersion > 0, extreme_count=2."""
        n1 = _series((-HOUR, 0.85))  # extreme high
        n2 = _series((-HOUR, 0.15))  # extreme low
        n3 = _series((-HOUR, 0.5))   # centrist
        features = aggregate_neighbor_features(
            [n1, n2, n3], forecast_time=FORECAST_T
        )
        names = dict(zip(NEIGHBOR_FEATURE_NAMES, features, strict=True))
        self.assertEqual(names["neighbor_count"], 3.0)
        self.assertAlmostEqual(names["neighbor_mean_probability"], 0.5, places=6)
        self.assertGreater(names["neighbor_dispersion"], 0.0)
        self.assertEqual(names["neighbor_extreme_count"], 2.0)


class MetricTests(unittest.TestCase):
    def test_brier_score_perfect_predictions_is_zero(self) -> None:
        self.assertEqual(brier_score([1, 0, 1], [1.0, 0.0, 1.0]), 0.0)

    def test_brier_score_constant_half_on_balanced(self) -> None:
        self.assertAlmostEqual(
            brier_score([1, 0, 1, 0], [0.5, 0.5, 0.5, 0.5]), 0.25, places=6
        )

    def test_log_loss_clipped_extremes(self) -> None:
        """Predictions of 0 or 1 against the opposite target must be finite, not -inf."""
        loss = log_loss([1, 0], [0.0, 1.0])
        self.assertTrue(math.isfinite(loss))
        # log(1e-6) ~= -13.815; mean over 2 examples is the same.
        self.assertGreater(loss, 10.0)

    def test_log_loss_perfect_predictions_is_near_zero(self) -> None:
        # Cannot be exactly zero because of clipping, but should be tiny.
        loss = log_loss([1, 0], [1.0, 0.0])
        self.assertLess(loss, 1e-4)

    def test_calibration_error_zero_when_perfectly_calibrated(self) -> None:
        # 100 examples, predictions at 0.5 split 50/50 => zero deviation.
        targets = [1] * 50 + [0] * 50
        preds = [0.5] * 100
        self.assertAlmostEqual(calibration_error(targets, preds), 0.0, places=6)

    def test_calibration_error_nonzero_when_miscalibrated(self) -> None:
        targets = [0] * 100  # all zero
        preds = [0.9] * 100  # all over-confident
        self.assertAlmostEqual(calibration_error(targets, preds), 0.9, places=6)

    def test_positive_rate_and_accuracy(self) -> None:
        self.assertEqual(positive_rate([1, 1, 0, 0]), 0.5)
        self.assertEqual(accuracy([1, 0, 1, 0], [0.9, 0.1, 0.8, 0.2]), 1.0)
        self.assertEqual(accuracy([1, 1, 1, 1], [0.4, 0.4, 0.4, 0.4]), 0.0)

    def test_empty_inputs_return_zero(self) -> None:
        self.assertEqual(brier_score([], []), 0.0)
        self.assertEqual(log_loss([], []), 0.0)
        self.assertEqual(calibration_error([], []), 0.0)
        self.assertEqual(positive_rate([]), 0.0)
        self.assertEqual(accuracy([], []), 0.0)
