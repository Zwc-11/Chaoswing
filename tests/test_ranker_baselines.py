"""Tests for the BM25 / lexical / Cohere baseline rerankers.

Every baseline must:
  * Refuse a post-cutoff candidate (the chokepoint test we apply to every
    reranker in the system).
  * Order candidates sensibly given lexically obvious inputs.

Cohere has the additional behavior of failing cleanly when no API key is
configured — the benchmark command relies on this to skip it without
crashing.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from chaoswing.ml._types import MarketDoc, TemporalCutoff
from chaoswing.ml.leakage import LeakageError

from apps.ranker.services.baselines import (
    BM25Reranker,
    CohereRerankerBaseline,
    LexicalOverlapReranker,
)


CUTOFF = TemporalCutoff(
    timestamp=datetime(2025, 6, 1, tzinfo=timezone.utc),
    label="bench_cutoff",
)


def _doc(market_id: str, title: str, *, first_seen: datetime | None = None) -> MarketDoc:
    return MarketDoc(
        market_id=market_id,
        title=title,
        first_seen=first_seen if first_seen is not None else CUTOFF.timestamp - timedelta(days=30),
        event_family=market_id,
    )


def _post_cutoff(market_id: str, title: str) -> MarketDoc:
    return _doc(market_id, title, first_seen=CUTOFF.timestamp + timedelta(days=1))


# ---------------------------------------------------------------------------
# Shared invariants
# ---------------------------------------------------------------------------


class BaselineLeakageTests(unittest.TestCase):
    """Each baseline rejects a post-cutoff candidate before any scoring."""

    def test_bm25_rejects_post_cutoff(self) -> None:
        source = _doc("source", "Federal Reserve interest rates")
        candidates = [
            _doc("a", "Federal Reserve hikes rates"),
            _post_cutoff("future", "Future market"),
        ]
        with self.assertRaises(LeakageError):
            BM25Reranker().rerank(source, candidates, cutoff=CUTOFF)

    def test_lexical_rejects_post_cutoff(self) -> None:
        source = _doc("source", "Federal Reserve interest rates")
        candidates = [
            _doc("a", "Federal Reserve hikes rates"),
            _post_cutoff("future", "Future market"),
        ]
        with self.assertRaises(LeakageError):
            LexicalOverlapReranker().rerank(source, candidates, cutoff=CUTOFF)

    def test_cohere_rejects_post_cutoff_before_api_call(self) -> None:
        """Gate runs before any network. We deliberately pass no API key —
        if the gate runs first, the leakage error fires, not the API error."""
        source = _doc("source", "Federal Reserve interest rates")
        candidates = [
            _doc("a", "Federal Reserve hikes rates"),
            _post_cutoff("future", "Future market"),
        ]
        with self.assertRaises(LeakageError):
            CohereRerankerBaseline(api_key=None).rerank(source, candidates, cutoff=CUTOFF)


# ---------------------------------------------------------------------------
# BM25 ordering
# ---------------------------------------------------------------------------


class BM25OrderingTests(unittest.TestCase):
    def test_lexical_overlap_wins(self) -> None:
        source = _doc("source", "Federal Reserve interest rates decision")
        candidates = [
            _doc("a", "Federal Reserve hikes interest rates"),
            _doc("b", "Recipe for chocolate chip cookies"),
            _doc("c", "Federal Reserve announces rate decision"),
            _doc("d", "Soccer match recap: weekend roundup"),
        ]
        result = BM25Reranker().rerank(source, candidates, cutoff=CUTOFF)
        top_two = {result[0].market_id, result[1].market_id}
        self.assertEqual(top_two, {"a", "c"})
        # The pure noise candidates should rank below.
        bottom_two = {result[2].market_id, result[3].market_id}
        self.assertEqual(bottom_two, {"b", "d"})

    def test_empty_candidates_returns_empty(self) -> None:
        result = BM25Reranker().rerank(_doc("s", "anything"), [], cutoff=CUTOFF)
        self.assertEqual(result, [])

    def test_python_fallback_matches_ordering(self) -> None:
        """If rank_bm25 isn't installed, the hand-rolled BM25 must still pick
        the obvious winner."""
        scorer = BM25Reranker()
        query = ["interest", "rates"]
        docs = [
            ["federal", "reserve", "interest", "rates"],
            ["chocolate", "chip", "cookies"],
            ["federal", "reserve", "rates"],
        ]
        scores = scorer._bm25_python(query, docs)
        self.assertEqual(len(scores), 3)
        self.assertGreater(scores[0], scores[1])  # obviously relevant > unrelated


# ---------------------------------------------------------------------------
# Lexical overlap ordering
# ---------------------------------------------------------------------------


class LexicalOverlapTests(unittest.TestCase):
    def test_higher_jaccard_ranks_first(self) -> None:
        source = _doc("source", "Federal Reserve hikes interest rates again")
        candidates = [
            _doc("a", "Recipe for sourdough bread"),
            _doc("b", "Federal Reserve hikes interest rates"),
            _doc("c", "Sports highlights from the weekend"),
        ]
        result = LexicalOverlapReranker().rerank(source, candidates, cutoff=CUTOFF)
        self.assertEqual(result[0].market_id, "b")
        self.assertGreater(result[0].score, result[1].score)


# ---------------------------------------------------------------------------
# Cohere availability gating
# ---------------------------------------------------------------------------


class CohereAvailabilityTests(unittest.TestCase):
    def test_missing_api_key_raises_runtime_error(self) -> None:
        """No api_key arg, no env var, no Django setting — clear runtime error."""
        import os

        prior = os.environ.pop("COHERE_API_KEY", None)
        try:
            ranker = CohereRerankerBaseline(api_key=None)
            source = _doc("source", "Federal Reserve interest rates")
            candidates = [_doc("a", "Federal Reserve hikes")]
            with self.assertRaises(RuntimeError) as ctx:
                ranker.rerank(source, candidates, cutoff=CUTOFF)
            self.assertIn("Cohere API key not configured", str(ctx.exception))
        finally:
            if prior is not None:
                os.environ["COHERE_API_KEY"] = prior

    def test_resolve_api_key_prefers_explicit_arg(self) -> None:
        ranker = CohereRerankerBaseline(api_key="explicit-key")
        self.assertEqual(ranker._resolve_api_key(), "explicit-key")
