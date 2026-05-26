"""Module 6 — baselines for the benchmark table.

BM25 (lexical floor), lexical overlap (the existing heuristic), and the
commercial Cohere reranker. The bi-encoder cosine baseline lives in
`biencoder.py`; the fine-tuned cross-encoder is the headline in
`cross_encoder.py`.

All three register themselves with the registry at import time so
`run_rerank_benchmark` can discover them. Every reranker honors the
per-candidate cutoff gate; even text-only methods route through
`assert_before_cutoff` so a future contributor cannot smuggle a post-cutoff
candidate into the table.

Per Ranker.md §"Metrics": Cohere is eval-only. The benchmark reports its
number, but the headline is the fine-tuned cross-encoder.
"""
from __future__ import annotations

import logging
import math
import os
import re
from collections import Counter
from collections.abc import Sequence

from chaoswing.ml._types import MarketDoc, ScoredCandidate, TemporalCutoff
from chaoswing.ml.leakage import assert_before_cutoff

from apps.ranker.services._registry import register


logger = logging.getLogger(__name__)


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _tokenize(*texts: str) -> list[str]:
    """Lowercase alphanumeric tokens. Cheap, dependency-free."""
    tokens: list[str] = []
    for text in texts:
        for match in _TOKEN_RE.findall(text or ""):
            tokens.append(match.lower())
    return tokens


def _gate_candidates(candidates: Sequence[MarketDoc], cutoff: TemporalCutoff) -> None:
    """Per-candidate cutoff check. Shared by all baselines so the chokepoint
    pattern is uniform across the layer."""
    for cand in candidates:
        if cand.first_seen is not None:
            assert_before_cutoff(
                cand.first_seen, cutoff, what=f"candidate {cand.market_id}.first_seen"
            )


def _scored_descending(
    candidates: Sequence[MarketDoc], scores: Sequence[float]
) -> list[ScoredCandidate]:
    """Stable descending sort of `candidates` by `scores`."""
    indexed = list(enumerate(scores))
    indexed.sort(key=lambda x: -x[1])  # stable thanks to sort's stability
    return [
        ScoredCandidate(
            market_id=candidates[idx].market_id, score=float(score), rank=rank + 1
        )
        for rank, (idx, score) in enumerate(indexed)
    ]


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------


@register("bm25")
class BM25Reranker:
    """BM25 over `title + description`.

    Tries `rank_bm25.BM25Okapi` first (the package is in the `[ml]` extra);
    falls back to a hand-rolled BM25 if the library isn't installed. The
    hand-rolled version uses the standard Okapi formula and produces
    identical orderings up to numerical noise — fine for a lexical floor.
    """

    requires_training = False

    def __init__(self, *, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

    def rerank(
        self,
        source: MarketDoc,
        candidates: Sequence[MarketDoc],
        *,
        cutoff: TemporalCutoff,
    ) -> list[ScoredCandidate]:
        _gate_candidates(candidates, cutoff)
        if not candidates:
            return []
        query_tokens = _tokenize(source.text)
        doc_tokens = [_tokenize(c.text) for c in candidates]
        scores = self._bm25_scores(query_tokens, doc_tokens)
        return _scored_descending(candidates, scores)

    def _bm25_scores(self, query: list[str], docs: list[list[str]]) -> list[float]:
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            return self._bm25_python(query, docs)
        scorer = BM25Okapi([d if d else [""] for d in docs], k1=self.k1, b=self.b)
        return [float(s) for s in scorer.get_scores(query)]

    def _bm25_python(self, query: list[str], docs: list[list[str]]) -> list[float]:
        """Fallback BM25 implementation. Standard Okapi formula."""
        n_docs = len(docs)
        if n_docs == 0:
            return []
        doc_lens = [len(d) for d in docs]
        avg_len = sum(doc_lens) / n_docs if n_docs else 0.0
        # Document frequencies for query terms only — cheaper than full DF table.
        df: dict[str, int] = {}
        for term in set(query):
            df[term] = sum(1 for d in docs if term in d)
        # Pre-tokenize term frequencies per doc.
        tf: list[Counter] = [Counter(d) for d in docs]
        scores: list[float] = []
        for i in range(n_docs):
            score = 0.0
            for term in query:
                if df.get(term, 0) == 0:
                    continue
                idf = math.log((n_docs - df[term] + 0.5) / (df[term] + 0.5) + 1.0)
                freq = tf[i].get(term, 0)
                if freq == 0:
                    continue
                norm = freq * (self.k1 + 1) / (
                    freq + self.k1 * (1 - self.b + self.b * (doc_lens[i] / max(avg_len, 1e-9)))
                )
                score += idf * norm
            scores.append(score)
        return scores


# ---------------------------------------------------------------------------
# Lexical overlap
# ---------------------------------------------------------------------------


@register("lexical-overlap")
class LexicalOverlapReranker:
    """Token Jaccard between source and candidate text.

    The build plan calls this "your existing heuristic" — it's the
    overlap-based ranking the codebase used pre-rebuild. We reuse the token
    tooling from `apps.web.services.leadlag` so the baseline matches what the
    old system produced; if a future contributor wants a fresh tokenizer,
    swap it in here without touching the registry.
    """

    requires_training = False

    def rerank(
        self,
        source: MarketDoc,
        candidates: Sequence[MarketDoc],
        *,
        cutoff: TemporalCutoff,
    ) -> list[ScoredCandidate]:
        _gate_candidates(candidates, cutoff)
        if not candidates:
            return []
        source_tokens = self._topic_tokens(source)
        scores = [self._jaccard(source_tokens, self._topic_tokens(c)) for c in candidates]
        return _scored_descending(candidates, scores)

    def _topic_tokens(self, doc: MarketDoc) -> set[str]:
        # Reuse the existing helper if available so the baseline matches the
        # legacy ranking surface; fall back to a simple tokenizer otherwise.
        try:
            from apps.web.services.leadlag import _topic_tokens
        except ImportError:
            return set(_tokenize(doc.text))
        return _topic_tokens(doc.title, doc.description)

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        union = left | right
        if not union:
            return 0.0
        return len(left & right) / len(union)


# ---------------------------------------------------------------------------
# Cohere — commercial reference, eval-only
# ---------------------------------------------------------------------------


@register("cohere-rerank")
class CohereRerankerBaseline:
    """Commercial reference. Eval-only; never the headline.

    Reads its API key from `settings.CHAOSWING_COHERE_API_KEY` or the
    `COHERE_API_KEY` env var. If neither is set, `rerank` raises a clear
    runtime error rather than silently producing nonsense — the benchmark
    command catches this and reports the method as "unavailable" in its
    table.
    """

    requires_training = False

    def __init__(self, *, model: str = "rerank-english-v3.0", api_key: str | None = None):
        self.model = model
        self._api_key = api_key
        self._client = None

    def rerank(
        self,
        source: MarketDoc,
        candidates: Sequence[MarketDoc],
        *,
        cutoff: TemporalCutoff,
    ) -> list[ScoredCandidate]:
        _gate_candidates(candidates, cutoff)
        if not candidates:
            return []
        api_key = self._resolve_api_key()
        if not api_key:
            raise RuntimeError(
                "Cohere API key not configured. Set CHAOSWING_COHERE_API_KEY in "
                "Django settings or COHERE_API_KEY in the environment."
            )
        client = self._get_client(api_key)
        # Cohere preserves input order on the response via `index` -- map it back.
        docs = [c.text for c in candidates]
        try:
            response = client.rerank(
                model=self.model,
                query=source.text,
                documents=docs,
                top_n=len(docs),
            )
        except Exception as exc:  # network / quota / model errors
            raise RuntimeError(f"Cohere rerank call failed: {exc}") from exc
        results = list(response.results) if hasattr(response, "results") else list(response)
        ordered: list[ScoredCandidate] = []
        for rank, item in enumerate(results):
            idx = int(getattr(item, "index", rank))
            score = float(getattr(item, "relevance_score", 0.0))
            cand = candidates[idx]
            ordered.append(ScoredCandidate(market_id=cand.market_id, score=score, rank=rank + 1))
        return ordered

    def _resolve_api_key(self) -> str | None:
        if self._api_key:
            return self._api_key
        try:
            from django.conf import settings
            key = getattr(settings, "CHAOSWING_COHERE_API_KEY", "")
            if key:
                return key
        except Exception:
            pass
        return os.environ.get("COHERE_API_KEY")

    def _get_client(self, api_key: str):
        if self._client is not None:
            return self._client
        try:
            import cohere
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "cohere is not installed. Add the `[ml]` extra: "
                "pip install -e .[ml]"
            ) from exc
        self._client = cohere.Client(api_key)
        return self._client
