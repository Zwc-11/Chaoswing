"""Inference-side tests for `FineTunedCrossEncoder`.

Two things to pin without loading an actual model checkpoint:

  1. `rerank` rejects post-cutoff candidates *before* any model call. The
     leakage gate lives in our code, not in transformers.
  2. `rerank` orders candidates by score descending — and that ordering
     comes from the underlying model output.

For #2 we monkey-patch `_score_pairs` to return fixed scores. That keeps the
test out of torch/transformers' way while still exercising the real
public surface.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from chaoswing.ml._types import MarketDoc, TemporalCutoff
from chaoswing.ml.leakage import LeakageError

from apps.ranker.services.cross_encoder import FineTunedCrossEncoder


CUTOFF = TemporalCutoff(
    timestamp=datetime(2025, 6, 1, tzinfo=timezone.utc),
    label="rerank_cutoff",
)


def _doc(market_id: str, *, first_seen: datetime, title: str | None = None) -> MarketDoc:
    return MarketDoc(
        market_id=market_id,
        title=title or market_id,
        first_seen=first_seen,
        event_family=market_id,
    )


def _pre(market_id: str, *, days: int = 30, title: str | None = None) -> MarketDoc:
    return _doc(market_id, first_seen=CUTOFF.timestamp - timedelta(days=days), title=title)


def _post(market_id: str, *, days: int = 1, title: str | None = None) -> MarketDoc:
    return _doc(market_id, first_seen=CUTOFF.timestamp + timedelta(days=days), title=title)


class CrossEncoderRerankLeakageTests(unittest.TestCase):
    def test_post_cutoff_candidate_raises_before_load(self) -> None:
        """The cutoff gate must run before we ever touch the (potentially
        missing) checkpoint. We point at a non-existent directory to prove
        `load()` was never called."""
        ce = FineTunedCrossEncoder(checkpoint_dir=Path("/nonexistent/cross_encoder"))
        source = _pre("source")
        bad = _post("future_market")
        with self.assertRaises(LeakageError):
            ce.rerank(source, [bad], cutoff=CUTOFF)
        # If the gate ran first, the model is still unloaded.
        self.assertIsNone(ce._model)
        self.assertIsNone(ce._tokenizer)

    def test_empty_candidate_list_returns_empty(self) -> None:
        ce = FineTunedCrossEncoder(checkpoint_dir=Path("/nonexistent/cross_encoder"))
        self.assertEqual(ce.rerank(_pre("s"), [], cutoff=CUTOFF), [])

    def test_candidate_without_first_seen_is_tolerated(self) -> None:
        """`first_seen=None` candidates skip the gate (no timestamp to check).

        We still don't reach the model — `_score_pairs` is monkey-patched.
        """
        ce = FineTunedCrossEncoder(checkpoint_dir=Path("/nonexistent/cross_encoder"))
        ce._score_pairs = lambda source, candidates: np.array([0.5], dtype=np.float32)  # type: ignore[method-assign]
        source = _pre("s")
        cand = MarketDoc(market_id="cand", title="C", first_seen=None, event_family="cand")
        result = ce.rerank(source, [cand], cutoff=CUTOFF)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].market_id, "cand")


class CrossEncoderRerankOrderingTests(unittest.TestCase):
    def test_results_sorted_by_score_descending(self) -> None:
        ce = FineTunedCrossEncoder(checkpoint_dir=Path("/nonexistent/cross_encoder"))
        candidates = [_pre("a"), _pre("b"), _pre("c"), _pre("d")]
        # Monkey-patched scorer: middle candidate should win, last lose.
        ce._score_pairs = lambda source, cands: np.array(  # type: ignore[method-assign]
            [0.2, 0.5, 0.9, 0.1], dtype=np.float32
        )
        result = ce.rerank(_pre("source"), candidates, cutoff=CUTOFF)
        self.assertEqual([r.market_id for r in result], ["c", "b", "a", "d"])
        self.assertEqual([r.rank for r in result], [1, 2, 3, 4])
        self.assertGreater(result[0].score, result[-1].score)
