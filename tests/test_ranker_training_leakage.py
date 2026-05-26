"""Defensive: `validate_training_examples` is the trainer's pre-flight gate.

Module 2's `compute_splits` already enforces "no train record has window_end
past the train cutoff." This test pins the *symmetric* check on the trainer
side: even if a corrupt RankingExample somehow lands in the train slice
(database hand-edit, faulty migration, whatever), the trainer must refuse
to start.

The test builds `ListwiseExample`s directly — no Django, no torch — and
calls the validator. If this ever passes silently on a post-cutoff source,
training is unsafe.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from chaoswing.ml._types import MarketDoc, TemporalCutoff
from chaoswing.ml.data import ListwiseExample
from chaoswing.ml.leakage import LeakageError
from chaoswing.ml.train import validate_training_examples


CUTOFF = TemporalCutoff(
    timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
    label="train_cutoff",
)


def _doc(market_id: str, *, first_seen: datetime | None) -> MarketDoc:
    return MarketDoc(
        market_id=market_id,
        title=market_id,
        first_seen=first_seen,
        event_family=market_id,
    )


def _example(*, source: MarketDoc, candidates: list[MarketDoc]) -> ListwiseExample:
    return ListwiseExample(
        source=source,
        candidates=candidates,
        relevances=[3 if i == 0 else 0 for i in range(len(candidates))],
    )


class TrainingLeakageGateTests(unittest.TestCase):
    def test_clean_examples_pass(self) -> None:
        pre = CUTOFF.timestamp - timedelta(days=30)
        ex = _example(
            source=_doc("src", first_seen=pre),
            candidates=[_doc("c1", first_seen=pre), _doc("c2", first_seen=pre)],
        )
        validate_training_examples([ex], CUTOFF)  # no exception

    def test_source_at_cutoff_is_rejected(self) -> None:
        """Strict inequality: a source first_seen *equal* to the cutoff leaks."""
        ex = _example(
            source=_doc("src", first_seen=CUTOFF.timestamp),
            candidates=[_doc("c", first_seen=CUTOFF.timestamp - timedelta(days=1))],
        )
        with self.assertRaises(LeakageError):
            validate_training_examples([ex], CUTOFF)

    def test_source_after_cutoff_is_rejected(self) -> None:
        ex = _example(
            source=_doc("src", first_seen=CUTOFF.timestamp + timedelta(minutes=1)),
            candidates=[_doc("c", first_seen=CUTOFF.timestamp - timedelta(days=1))],
        )
        with self.assertRaises(LeakageError):
            validate_training_examples([ex], CUTOFF)

    def test_source_without_first_seen_is_rejected(self) -> None:
        """No timestamp = no proof of safety. Refuse rather than guess."""
        ex = _example(
            source=_doc("src", first_seen=None),
            candidates=[_doc("c", first_seen=CUTOFF.timestamp - timedelta(days=1))],
        )
        with self.assertRaises(LeakageError):
            validate_training_examples([ex], CUTOFF)

    def test_post_cutoff_candidate_is_rejected(self) -> None:
        pre = CUTOFF.timestamp - timedelta(days=30)
        ex = _example(
            source=_doc("src", first_seen=pre),
            candidates=[
                _doc("c1", first_seen=pre),
                _doc("future_candidate", first_seen=CUTOFF.timestamp + timedelta(days=1)),
            ],
        )
        with self.assertRaises(LeakageError):
            validate_training_examples([ex], CUTOFF)

    def test_candidate_without_first_seen_is_tolerated(self) -> None:
        """Text-only training; a candidate without a timestamp is allowed."""
        pre = CUTOFF.timestamp - timedelta(days=30)
        ex = _example(
            source=_doc("src", first_seen=pre),
            candidates=[
                _doc("c1", first_seen=pre),
                _doc("c2", first_seen=None),
            ],
        )
        validate_training_examples([ex], CUTOFF)  # no exception
