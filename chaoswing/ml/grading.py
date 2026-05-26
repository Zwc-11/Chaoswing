"""Co-movement metrics → 0-3 graded relevance label.

The rubric mirrors the table in `chaoswing-rebuild-plan.md` §3. Thresholds are
exposed as a dataclass so they can be tuned on a held-out slice and reported
alongside benchmark numbers.

Defending "why these thresholds" matters more in interviews than the exact
values — the build plan says so explicitly. Treat `GradingThresholds.notes`
as the place to record that justification when you tune.
"""
from __future__ import annotations

from dataclasses import dataclass

from chaoswing.ml._types import Relevance


@dataclass(frozen=True, slots=True)
class GradingThresholds:
    """Grading knobs.

    All thresholds are inclusive lower bounds unless documented otherwise.
    `granger_p_strong` is an upper bound on the Granger p-value (smaller = more
    causal). `peak_xcorr_*` are taken on absolute value, so a strong negative
    correlation counts the same as a strong positive one.
    """

    peak_xcorr_strong: float = 0.60
    granger_p_strong: float = 0.01
    min_lag_for_strong: int = 1

    peak_xcorr_related: float = 0.40

    peak_xcorr_weak: float = 0.20
    shock_co_fraction_weak: float = 0.30

    notes: str = "default thresholds — tune on the val slice before reporting"


def default_thresholds() -> GradingThresholds:
    return GradingThresholds()


def grade_co_movement(
    *,
    peak_xcorr: float,
    best_lag_steps: int,
    granger_p: float | None,
    shock_fraction: float,
    thresholds: GradingThresholds | None = None,
) -> Relevance:
    """Apply the rubric and return a `Relevance` label.

    Highest-match wins:
      3 — |xcorr| >= peak_xcorr_strong at |lag| >= min_lag_for_strong AND
          granger_p <= granger_p_strong
      2 — |xcorr| >= peak_xcorr_related (any lag)
      1 — |xcorr| >= peak_xcorr_weak OR shock_fraction >= shock_co_fraction_weak
      0 — otherwise
    """
    t = thresholds or default_thresholds()
    abs_xcorr = abs(peak_xcorr)
    abs_lag = abs(best_lag_steps)

    strong_xcorr = abs_xcorr >= t.peak_xcorr_strong and abs_lag >= t.min_lag_for_strong
    strong_granger = granger_p is not None and granger_p <= t.granger_p_strong
    if strong_xcorr and strong_granger:
        return Relevance.STRONG

    if abs_xcorr >= t.peak_xcorr_related:
        return Relevance.RELATED

    if abs_xcorr >= t.peak_xcorr_weak or shock_fraction >= t.shock_co_fraction_weak:
        return Relevance.WEAK

    return Relevance.UNRELATED
