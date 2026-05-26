"""Leakage invariant tests for the ranker pipeline.

The golden rule from CLAUDE.md: no labels or features may use data on or
after the temporal cutoff. These tests pin that invariant at the chokepoint
(`chaoswing.ml.leakage`) and at the level of mined records / split
assignments. If any of these fail, mining is unsafe — do not ship benchmark
numbers from it.

Run with: python manage.py test tests.test_ranker_leakage
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from chaoswing.ml._types import Relevance, RelevanceRecord, TemporalCutoff
from chaoswing.ml.leakage import (
    LeakageError,
    assert_before_cutoff,
    assert_record_respects_cutoff,
    audit_records,
    filter_array_before_cutoff,
)
from chaoswing.ml.splits import TemporalSplitConfig, compute_splits


CUTOFF = TemporalCutoff(
    timestamp=datetime(2025, 6, 1, tzinfo=timezone.utc),
    label="test_cutoff",
)


class LeakageChokepointTests(unittest.TestCase):
    """Direct tests on `chaoswing.ml.leakage` helpers."""

    def test_assert_before_cutoff_rejects_equal(self) -> None:
        with self.assertRaises(LeakageError):
            assert_before_cutoff(CUTOFF.timestamp, CUTOFF, what="t")

    def test_assert_before_cutoff_rejects_after(self) -> None:
        after = CUTOFF.timestamp + timedelta(minutes=1)
        with self.assertRaises(LeakageError):
            assert_before_cutoff(after, CUTOFF, what="t")

    def test_assert_before_cutoff_accepts_before(self) -> None:
        before = CUTOFF.timestamp - timedelta(seconds=1)
        assert_before_cutoff(before, CUTOFF, what="t")  # must not raise

    def test_assert_before_cutoff_allows_equal_opt_in(self) -> None:
        assert_before_cutoff(CUTOFF.timestamp, CUTOFF, what="t", allow_equal=True)

    def test_filter_array_before_cutoff_strict_inequality(self) -> None:
        timestamps = np.array(
            [
                np.datetime64(CUTOFF.timestamp.replace(tzinfo=None) - timedelta(minutes=2), "ns"),
                np.datetime64(CUTOFF.timestamp.replace(tzinfo=None), "ns"),
                np.datetime64(CUTOFF.timestamp.replace(tzinfo=None) + timedelta(minutes=2), "ns"),
            ],
            dtype="datetime64[ns]",
        )
        values = np.array([1.0, 2.0, 3.0])
        out_ts, out_v = filter_array_before_cutoff(timestamps, values, CUTOFF)
        self.assertEqual(out_v.tolist(), [1.0])
        self.assertEqual(out_ts.shape, (1,))


class RecordValidationTests(unittest.TestCase):
    """Per-row checks the label miner runs before emitting JSONL."""

    def test_record_must_have_window_end(self) -> None:
        record = RelevanceRecord(
            source_id="A",
            candidate_id="B",
            relevance=Relevance.RELATED,
            window_end=None,
            event_family="fam",
        )
        with self.assertRaises(LeakageError):
            assert_record_respects_cutoff(record, CUTOFF)

    def test_record_window_end_after_cutoff_is_rejected(self) -> None:
        bad = RelevanceRecord(
            source_id="A",
            candidate_id="B",
            relevance=Relevance.STRONG,
            window_end=CUTOFF.timestamp,  # equal -> leakage
            event_family="fam",
        )
        with self.assertRaises(LeakageError):
            assert_record_respects_cutoff(bad, CUTOFF)

    def test_record_window_end_strictly_before_is_accepted(self) -> None:
        good = RelevanceRecord(
            source_id="A",
            candidate_id="B",
            relevance=Relevance.WEAK,
            window_end=CUTOFF.timestamp - timedelta(microseconds=1),
            event_family="fam",
        )
        assert_record_respects_cutoff(good, CUTOFF)
        audit_records([good], CUTOFF)


class TemporalSplitTests(unittest.TestCase):
    """Module 2: splits must dedupe by event_family and forbid train-side leakage."""

    train_cutoff = TemporalCutoff(
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        label="train_cutoff",
    )
    val_cutoff = TemporalCutoff(
        timestamp=datetime(2025, 6, 1, tzinfo=timezone.utc),
        label="val_cutoff",
    )

    def test_split_config_rejects_inverted_cutoffs(self) -> None:
        with self.assertRaises(ValueError):
            TemporalSplitConfig(
                train_cutoff=self.val_cutoff,
                val_cutoff=self.train_cutoff,
            )

    def test_train_record_window_end_must_be_before_train_cutoff(self) -> None:
        config = TemporalSplitConfig(
            train_cutoff=self.train_cutoff,
            val_cutoff=self.val_cutoff,
        )
        record = RelevanceRecord(
            source_id="A",
            candidate_id="B",
            relevance=Relevance.STRONG,
            source_first_seen=datetime(2024, 6, 1, tzinfo=timezone.utc),  # -> train
            window_end=datetime(2025, 2, 1, tzinfo=timezone.utc),  # past train cutoff
            event_family="fed-rates-2025",
        )
        with self.assertRaises(LeakageError):
            compute_splits([record], config)

    def test_dedup_by_event_family(self) -> None:
        """Two records with the same event_family must land in the same split.

        Random splits would let near-twin markets straddle the boundary; the
        family-based rule prevents it.
        """
        config = TemporalSplitConfig(
            train_cutoff=self.train_cutoff,
            val_cutoff=self.val_cutoff,
        )
        early = RelevanceRecord(
            source_id="A",
            candidate_id="X",
            relevance=Relevance.STRONG,
            source_first_seen=datetime(2024, 6, 1, tzinfo=timezone.utc),
            window_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            event_family="fed-rates",
        )
        late = RelevanceRecord(
            source_id="B",
            candidate_id="Y",
            relevance=Relevance.RELATED,
            source_first_seen=datetime(2025, 8, 1, tzinfo=timezone.utc),
            # Real records mined under the same cutoff share a cutoff-respecting
            # window_end; once the family pins to 'train', `late` inherits that
            # constraint. Anything later would be leakage and would correctly fail.
            window_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            event_family="fed-rates",  # same family as `early`
        )
        assignment = compute_splits([early, late], config)
        # Family is pinned by its earliest source_first_seen (2024-06-01 -> train).
        self.assertEqual(assignment.family_to_split["fed-rates"], "train")
        self.assertEqual(assignment.record_to_split[("A", "X")], "train")
        self.assertEqual(assignment.record_to_split[("B", "Y")], "train")
