"""Module 3 — bi-encoder index leakage tests.

These tests pin the invariant: the bi-encoder index never contains a market
whose `first_seen >= cutoff`, and no retrieval path returns one. They use a
fake encoder (deterministic seeded vectors) and a fake repository so they
require neither `sentence-transformers` nor `torch` nor `faiss` to run.

Run with: python manage.py test tests.test_ranker_biencoder
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import numpy as np

from chaoswing.ml._types import MarketDoc, TemporalCutoff
from chaoswing.ml.leakage import LeakageError

from apps.ranker.services.biencoder import (
    BiEncoderIndexBuilder,
    BiEncoderIndexConfig,
    BiEncoderReranker,
    NumpyBackend,
    evaluate_recall_at_k,
    load_index,
)


CUTOFF = TemporalCutoff(
    timestamp=datetime(2025, 6, 1, tzinfo=timezone.utc),
    label="index_cutoff",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeEncoder:
    """Deterministic, hash-seeded vectors. No torch / sentence-transformers."""

    model_name = "fake-encoder"
    embedding_dim = 8

    def _vec(self, text: str) -> np.ndarray:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(h[:8], "big") % (2**32)
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(self.embedding_dim).astype(np.float32)
        norm = float(np.linalg.norm(v))
        return v / norm if norm > 0 else v

    def encode_query(self, text: str) -> np.ndarray:
        return self._vec(f"q::{text}")

    def encode_passages(self, texts: Sequence[str]) -> np.ndarray:
        return np.stack([self._vec(f"p::{t}") for t in texts]).astype(np.float32)


@dataclass(slots=True)
class _MarketFixture:
    doc: MarketDoc


class FakeSnapshotRepository:
    """In-memory `SnapshotRepository` honoring the cutoff filter."""

    def __init__(self, fixtures: Iterable[_MarketFixture]):
        self._fixtures = list(fixtures)

    def iter_eligible_markets(self, *, cutoff: TemporalCutoff):
        for fx in self._fixtures:
            if fx.doc.first_seen is None:
                continue
            if fx.doc.first_seen < cutoff.timestamp:
                yield fx.doc

    def load_probability_series(self, market_id: str, *, cutoff: TemporalCutoff):
        return [], []

    def first_seen(self, market_id: str):
        for fx in self._fixtures:
            if fx.doc.market_id == market_id:
                return fx.doc.first_seen
        return None


def _make_doc(market_id: str, *, first_seen: datetime, title: str | None = None) -> MarketDoc:
    return MarketDoc(
        market_id=market_id,
        title=title or market_id.replace("_", " ").title(),
        first_seen=first_seen,
        event_family=market_id,
    )


def _make_corpus() -> list[_MarketFixture]:
    pre = CUTOFF.timestamp - timedelta(days=30)
    post = CUTOFF.timestamp + timedelta(days=1)
    return [
        _MarketFixture(_make_doc("pre_a", first_seen=pre, title="Fed hike March")),
        _MarketFixture(_make_doc("pre_b", first_seen=pre, title="Fed hike June")),
        _MarketFixture(_make_doc("post_a", first_seen=post, title="Future market A")),
        _MarketFixture(_make_doc("post_b", first_seen=post, title="Future market B")),
    ]


def _builder() -> BiEncoderIndexBuilder:
    return BiEncoderIndexBuilder(
        encoder=FakeEncoder(),
        repository=FakeSnapshotRepository(_make_corpus()),
        config=BiEncoderIndexConfig(model_name="fake-encoder", prefer_faiss=False),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class IndexLeakageTests(unittest.TestCase):
    """The post-cutoff invariant at every entry point."""

    def test_index_excludes_post_cutoff_markets(self) -> None:
        """`build_doc_set` drops markets whose `first_seen >= cutoff`."""
        doc_set = _builder().build_doc_set(CUTOFF)
        self.assertEqual(set(doc_set.market_ids), {"pre_a", "pre_b"})
        self.assertEqual(doc_set.vectors.shape, (2, FakeEncoder.embedding_dim))
        for ts in doc_set.first_seen:
            self.assertLess(ts, CUTOFF.timestamp)

    def test_persisted_meta_only_lists_pre_cutoff(self) -> None:
        """The sidecar JSON -- the audit trail -- agrees with the doc set."""
        with tempfile.TemporaryDirectory() as tmp:
            handle = _builder().build_and_persist(CUTOFF, path=Path(tmp) / "idx")
            self.assertEqual(set(handle.market_ids), {"pre_a", "pre_b"})
            reloaded = load_index(handle.meta_path)
            self.assertEqual(set(reloaded.market_ids), {"pre_a", "pre_b"})
            for ts in reloaded.first_seen:
                self.assertLess(ts, CUTOFF.timestamp)

    def test_directly_indexing_post_cutoff_raises(self) -> None:
        """Bypass the repository: `_add_doc` itself is the per-row chokepoint."""
        builder = _builder()
        post_doc = _make_doc(
            "post_x",
            first_seen=CUTOFF.timestamp + timedelta(minutes=1),
        )
        market_ids: list[str] = []
        first_seen: list[datetime] = []
        titles: list[str] = []
        texts: list[str] = []
        with self.assertRaises(LeakageError):
            builder._add_doc(post_doc, CUTOFF, market_ids, first_seen, titles, texts)
        self.assertEqual(market_ids, [])  # nothing leaked into the buffer

    def test_retrieve_never_returns_post_cutoff(self) -> None:
        """End-to-end: query the built index, verify no post-cutoff id surfaces."""
        with tempfile.TemporaryDirectory() as tmp:
            handle = _builder().build_and_persist(CUTOFF, path=Path(tmp) / "idx")
            reranker = BiEncoderReranker(
                encoder=FakeEncoder(),
                index=handle,
                model_name="fake-encoder",
            )
            query = _make_doc("query", first_seen=CUTOFF.timestamp - timedelta(days=1))
            results = reranker.retrieve(query, top_k=10, cutoff=CUTOFF)
            self.assertGreater(len(results), 0)
            for hit in results:
                self.assertIn(hit.market_id, {"pre_a", "pre_b"})
                self.assertNotIn(hit.market_id, {"post_a", "post_b"})

    def test_query_cutoff_before_build_raises(self) -> None:
        """If the index was built later than the query cutoff allows, raise."""
        with tempfile.TemporaryDirectory() as tmp:
            handle = _builder().build_and_persist(CUTOFF, path=Path(tmp) / "idx")
            reranker = BiEncoderReranker(
                encoder=FakeEncoder(),
                index=handle,
                model_name="fake-encoder",
            )
            earlier_cutoff = TemporalCutoff(
                timestamp=CUTOFF.timestamp - timedelta(days=10),
                label="earlier",
            )
            with self.assertRaises(LeakageError):
                reranker.retrieve(
                    _make_doc("q", first_seen=earlier_cutoff.timestamp - timedelta(days=1)),
                    top_k=5,
                    cutoff=earlier_cutoff,
                )

    def test_rerank_rejects_post_cutoff_candidate(self) -> None:
        """`rerank` is the score-an-arbitrary-list path; it must gate too."""
        reranker = BiEncoderReranker(encoder=FakeEncoder(), model_name="fake-encoder")
        source = _make_doc("src", first_seen=CUTOFF.timestamp - timedelta(days=1))
        post_cand = _make_doc("future", first_seen=CUTOFF.timestamp + timedelta(days=1))
        with self.assertRaises(LeakageError):
            reranker.rerank(source, [post_cand], cutoff=CUTOFF)


class NumpyBackendTests(unittest.TestCase):
    """Sanity checks on the fallback backend."""

    def test_empty_index_returns_no_hits(self) -> None:
        backend = NumpyBackend(np.zeros((0, 4), dtype=np.float32))
        self.assertEqual(backend.search(np.zeros(4, dtype=np.float32), top_k=10), [])

    def test_top_k_ordering(self) -> None:
        v = np.array(
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.9, 0.1, 0.0, 0.0],
                [0.5, 0.5, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
            ],
            dtype=np.float32,
        )
        backend = NumpyBackend(v)
        hits = backend.search(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32), top_k=3)
        self.assertEqual([h[0] for h in hits], [0, 1, 2])


class RecallReportTests(unittest.TestCase):
    """The eval helper aggregates per-source Recall@k correctly."""

    def test_recall_counts_only_positives(self) -> None:
        corpus = _make_corpus()
        # Add a third pre-cutoff market so we have something to retrieve besides
        # the two positives.
        corpus.append(
            _MarketFixture(_make_doc("pre_c", first_seen=CUTOFF.timestamp - timedelta(days=10)))
        )
        repository = FakeSnapshotRepository(corpus)
        builder = BiEncoderIndexBuilder(
            encoder=FakeEncoder(),
            repository=repository,
            config=BiEncoderIndexConfig(model_name="fake-encoder", prefer_faiss=False),
        )
        with tempfile.TemporaryDirectory() as tmp:
            handle = builder.build_and_persist(CUTOFF, path=Path(tmp) / "idx")
            reranker = BiEncoderReranker(
                encoder=FakeEncoder(), index=handle, model_name="fake-encoder"
            )
            source_doc = _make_doc("query", first_seen=CUTOFF.timestamp - timedelta(days=1))
            docs_by_id = {"query": source_doc}
            examples = [
                {
                    "source_market_id": "query",
                    "candidates": [
                        {"candidate_market_id": "pre_a", "relevance": 3},
                        {"candidate_market_id": "pre_b", "relevance": 2},
                        {"candidate_market_id": "pre_c", "relevance": 0},
                    ],
                }
            ]
            report = evaluate_recall_at_k(
                reranker,
                examples=examples,
                docs_by_id=docs_by_id,
                cutoff=CUTOFF,
                k=3,
            )
            self.assertEqual(report.n_sources, 1)
            # The two positives are in the corpus, so they must be retrievable.
            self.assertAlmostEqual(report.recall_at_100, 1.0)
