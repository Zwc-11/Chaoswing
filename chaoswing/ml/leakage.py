"""The leakage invariant.

Golden rule #1 from CLAUDE.md: labels and features use only data *before* the
relevant cutoff timestamp. This module is the single place that rule is
enforced. Every leakage-sensitive call site should route through one of these
helpers; that makes the invariant grep-able and the rule auditable.

If a code path needs raw timestamp comparisons against a cutoff and does not
use this module, that is a review red flag.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterable, Iterator, Sequence, TYPE_CHECKING

from chaoswing.ml._types import RelevanceRecord, TemporalCutoff, _ensure_utc

if TYPE_CHECKING:
    import numpy as np


class LeakageError(ValueError):
    """Raised when an operation would let post-cutoff data into a label or feature.

    This is a hard error, not a warning. The build plan and CLAUDE.md both
    treat leakage as non-negotiable; if you find yourself catching
    `LeakageError` to "make it work," stop and fix the upstream call.
    """


def assert_before_cutoff(
    moment: datetime,
    cutoff: TemporalCutoff,
    *,
    what: str = "observation",
    allow_equal: bool = False,
) -> None:
    """Raise `LeakageError` if `moment` is on or after `cutoff`."""
    moment_utc = _ensure_utc(moment)
    threshold_ok = moment_utc <= cutoff.timestamp if allow_equal else moment_utc < cutoff.timestamp
    if not threshold_ok:
        raise LeakageError(
            f"{what} at {moment_utc.isoformat()} is not strictly before "
            f"{cutoff.label}={cutoff.timestamp.isoformat()}"
        )


def assert_all_before_cutoff(
    moments: Iterable[datetime],
    cutoff: TemporalCutoff,
    *,
    what: str = "observation",
) -> None:
    for moment in moments:
        assert_before_cutoff(moment, cutoff, what=what)


def filter_array_before_cutoff(
    timestamps: "np.ndarray",
    values: "np.ndarray",
    cutoff: TemporalCutoff,
) -> tuple["np.ndarray", "np.ndarray"]:
    """Return `(timestamps, values)` filtered to entries strictly before `cutoff`.

    The arrays must be the same length. Both are returned as new arrays; the
    inputs are not mutated.
    """
    import numpy as np

    if timestamps.shape[0] != values.shape[0]:
        raise ValueError("timestamps and values must have the same length")

    cutoff_np = np.datetime64(cutoff.timestamp.replace(tzinfo=None), "ns")
    if timestamps.dtype.kind == "M":
        mask = timestamps < cutoff_np
    else:
        coerced = np.array(
            [np.datetime64(_ensure_utc(t).replace(tzinfo=None), "ns") for t in timestamps],
            dtype="datetime64[ns]",
        )
        mask = coerced < cutoff_np
        timestamps = coerced
    return timestamps[mask], values[mask]


def assert_record_respects_cutoff(record: RelevanceRecord, cutoff: TemporalCutoff) -> None:
    """Sanity-check a mined label: its `window_end` must be < `cutoff`."""
    if record.window_end is None:
        raise LeakageError(
            f"record source={record.source_id} candidate={record.candidate_id} "
            f"has no window_end; cannot prove it respects {cutoff.label}"
        )
    assert_before_cutoff(record.window_end, cutoff, what="window_end")


@contextmanager
def temporal_context(cutoff: TemporalCutoff) -> Iterator[TemporalCutoff]:
    """Readability aid: `with temporal_context(cutoff) as t: ...`."""
    yield cutoff


def audit_records(records: Sequence[RelevanceRecord], cutoff: TemporalCutoff) -> None:
    """Validate a whole batch. Used by tests and by the miner after writing."""
    for record in records:
        assert_record_respects_cutoff(record, cutoff)
