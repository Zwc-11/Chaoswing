"""Module 3 — bi-encoder retriever (stage 1).

The pipeline:

    BiEncoderIndexBuilder.build(cutoff)
        -> iter_eligible_markets(cutoff)        # repository chokepoint
        -> assert_before_cutoff(first_seen)     # per-row gate
        -> encoder.encode_passages(texts)       # SentenceTransformers
        -> IndexHandle(market_ids, vecs, ...)
        -> write FAISS + sidecar JSON

    BiEncoderReranker.retrieve(source, cutoff)
        -> assert query cutoff respects index build cutoff
        -> encoder.encode_query(source.text)
        -> backend.search(top_k=100)

Two backends: `FaissBackend` (production, lazy faiss import) and
`NumpyBackend` (fallback when faiss isn't installed; also useful for tests).
Both score with inner-product over L2-normalized vectors -- equivalent to
cosine similarity, simple and exact at our corpus size (tens of thousands of
markets).

Leakage discipline (Ranker.md §Leakage discipline):
  * Cutoff routing goes through `TemporalCutoff` and the helpers in
    `chaoswing.ml.leakage`. There are no raw `timestamp < cutoff` comparisons
    in this module.
  * The sidecar JSON (`<index>.meta.json`) records the build cutoff. Every
    retrieval call compares its query cutoff to the build cutoff via
    `assert_before_cutoff(build_cutoff, query_cutoff, allow_equal=True)`.
  * Any path that adds a `MarketDoc` to the index runs through `_add_doc`,
    which calls `assert_before_cutoff(doc.first_seen, cutoff)`. A misbehaving
    repository cannot smuggle a post-cutoff market into the index.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

import numpy as np

from chaoswing.ml._types import MarketDoc, ScoredCandidate, TemporalCutoff, _ensure_utc
from chaoswing.ml.leakage import assert_before_cutoff

from apps.ranker.services._registry import register
from apps.ranker.services._repository import DjangoSnapshotRepository, SnapshotRepository


logger = logging.getLogger(__name__)


DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
# BGE asks for an instruction prefix on queries for retrieval. MiniLM does not.
# Keep the mapping data-driven so swapping models is a CLI flag, not a code change.
QUERY_PREFIX_BY_MODEL: dict[str, str] = {
    "BAAI/bge-small-en-v1.5": "Represent this sentence for searching relevant passages: ",
    "BAAI/bge-base-en-v1.5": "Represent this sentence for searching relevant passages: ",
}


# ---------------------------------------------------------------------------
# Text encoders
# ---------------------------------------------------------------------------


class TextEncoder(Protocol):
    """The narrow interface the index builder needs.

    A test can stand in a `FakeEncoder` with deterministic vectors without
    pulling in `sentence-transformers` or torch.
    """

    model_name: str
    embedding_dim: int

    def encode_query(self, text: str) -> np.ndarray: ...

    def encode_passages(self, texts: Sequence[str]) -> np.ndarray: ...


class SentenceTransformerEncoder:
    """Production encoder. Lazy-imports `sentence_transformers`.

    Outputs are L2-normalized so we can use inner-product as cosine.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL, *, device: str | None = None):
        from sentence_transformers import SentenceTransformer  # lazy import

        self.model_name = model_name
        self._model = SentenceTransformer(model_name, device=device)
        self.embedding_dim = int(self._model.get_sentence_embedding_dimension())
        self.query_prefix = QUERY_PREFIX_BY_MODEL.get(model_name, "")

    def encode_query(self, text: str) -> np.ndarray:
        prefixed = (self.query_prefix + text) if self.query_prefix else text
        vec = self._model.encode(prefixed, normalize_embeddings=True)
        return np.asarray(vec, dtype=np.float32).reshape(-1)

    def encode_passages(self, texts: Sequence[str]) -> np.ndarray:
        vecs = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            batch_size=64,
            show_progress_bar=False,
        )
        return np.asarray(vecs, dtype=np.float32)


# ---------------------------------------------------------------------------
# Vector search backends
# ---------------------------------------------------------------------------


class VectorSearchBackend(Protocol):
    """An ANN index, conceptually."""

    def search(self, query: np.ndarray, *, top_k: int) -> list[tuple[int, float]]: ...

    def save(self, path: Path) -> None: ...

    @classmethod
    def load(cls, path: Path, *, embedding_dim: int) -> "VectorSearchBackend": ...


class NumpyBackend:
    """Exact inner-product search via a single matmul. Used when faiss is
    unavailable and for tests. O(N) per query; fine at this corpus size.
    """

    def __init__(self, vectors: np.ndarray):
        if vectors.ndim != 2:
            raise ValueError("vectors must be a 2D matrix")
        self.vectors = np.ascontiguousarray(vectors, dtype=np.float32)

    def search(self, query: np.ndarray, *, top_k: int) -> list[tuple[int, float]]:
        if self.vectors.shape[0] == 0:
            return []
        scores = self.vectors @ query.astype(np.float32)
        top_k = min(top_k, scores.shape[0])
        # Partial sort top-k indices, then sort that small slice descending.
        idx = np.argpartition(-scores, top_k - 1)[:top_k]
        idx = idx[np.argsort(-scores[idx], kind="stable")]
        return [(int(i), float(scores[i])) for i in idx]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, self.vectors, allow_pickle=False)

    @classmethod
    def load(cls, path: Path, *, embedding_dim: int) -> "NumpyBackend":
        vectors = np.load(path, allow_pickle=False)
        if vectors.shape[1] != embedding_dim:
            raise ValueError(
                f"index dim {vectors.shape[1]} does not match expected {embedding_dim}"
            )
        return cls(vectors)


class FaissBackend:
    """Production backend. `faiss.IndexFlatIP` on L2-normalized vectors == cosine."""

    def __init__(self, index):
        self._index = index

    @classmethod
    def from_vectors(cls, vectors: np.ndarray) -> "FaissBackend":
        import faiss  # lazy

        if vectors.ndim != 2:
            raise ValueError("vectors must be a 2D matrix")
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(np.ascontiguousarray(vectors, dtype=np.float32))
        return cls(index)

    def search(self, query: np.ndarray, *, top_k: int) -> list[tuple[int, float]]:
        if self._index.ntotal == 0:
            return []
        top_k = min(top_k, self._index.ntotal)
        q = np.ascontiguousarray(query.reshape(1, -1), dtype=np.float32)
        scores, ids = self._index.search(q, top_k)
        return [(int(i), float(s)) for i, s in zip(ids[0], scores[0], strict=True) if i != -1]

    def save(self, path: Path) -> None:
        import faiss  # lazy

        path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(path))

    @classmethod
    def load(cls, path: Path, *, embedding_dim: int) -> "FaissBackend":
        import faiss  # lazy

        index = faiss.read_index(str(path))
        if index.d != embedding_dim:
            raise ValueError(
                f"index dim {index.d} does not match expected {embedding_dim}"
            )
        return cls(index)


def _select_backend(prefer_faiss: bool) -> str:
    """Pick a backend name. Falls back to numpy if faiss is missing."""
    if not prefer_faiss:
        return "numpy"
    try:
        import faiss  # noqa: F401
        return "faiss"
    except ImportError:
        logger.warning("faiss not installed; using numpy backend for bi-encoder index")
        return "numpy"


# ---------------------------------------------------------------------------
# Index data + handle
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IndexDocSet:
    """Filtered set of (id, vec, first_seen) the index will hold.

    Materialized BEFORE any backend is touched, so leakage tests can validate
    membership without importing faiss.
    """

    market_ids: list[str]
    vectors: np.ndarray  # shape (n, embedding_dim), float32, L2-normalized
    first_seen: list[datetime]
    titles: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.market_ids)


@dataclass(slots=True)
class IndexHandle:
    """A built index. The on-disk pair is `<path>.bin` + `<path>.meta.json`.

    `backend` is lazily loaded on first `search()` so cold opens are cheap.
    """

    cutoff: TemporalCutoff
    model_name: str
    embedding_dim: int
    market_ids: list[str]
    first_seen: list[datetime]
    backend_name: str
    index_path: Path
    meta_path: Path
    _backend: VectorSearchBackend | None = None

    @property
    def size(self) -> int:
        return len(self.market_ids)

    def search(self, query_vec: np.ndarray, *, top_k: int = 100) -> list[ScoredCandidate]:
        if self._backend is None:
            self._backend = self._load_backend()
        hits = self._backend.search(query_vec, top_k=top_k)
        return [
            ScoredCandidate(market_id=self.market_ids[idx], score=score, rank=rank + 1)
            for rank, (idx, score) in enumerate(hits)
        ]

    def _load_backend(self) -> VectorSearchBackend:
        if self.backend_name == "faiss":
            return FaissBackend.load(self.index_path, embedding_dim=self.embedding_dim)
        return NumpyBackend.load(self.index_path, embedding_dim=self.embedding_dim)

    def to_meta(self) -> dict:
        return {
            "cutoff": self.cutoff.timestamp.isoformat(),
            "cutoff_label": self.cutoff.label,
            "model_name": self.model_name,
            "embedding_dim": self.embedding_dim,
            "backend": self.backend_name,
            "size": self.size,
            "market_ids": self.market_ids,
            "first_seen": [t.isoformat() if t else None for t in self.first_seen],
            "index_path": str(self.index_path),
        }


def _load_index_handle(meta_path: Path) -> IndexHandle:
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    first_seen = [
        datetime.fromisoformat(t.replace("Z", "+00:00")) if t else None
        for t in payload["first_seen"]
    ]
    return IndexHandle(
        cutoff=TemporalCutoff(
            timestamp=datetime.fromisoformat(payload["cutoff"].replace("Z", "+00:00")),
            label=payload.get("cutoff_label", "index_cutoff"),
        ),
        model_name=payload["model_name"],
        embedding_dim=int(payload["embedding_dim"]),
        market_ids=list(payload["market_ids"]),
        first_seen=first_seen,
        backend_name=payload.get("backend", "numpy"),
        index_path=Path(payload["index_path"]),
        meta_path=meta_path,
    )


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BiEncoderIndexConfig:
    model_name: str = DEFAULT_MODEL
    prefer_faiss: bool = True
    encoder_batch_size: int = 64


class BiEncoderIndexBuilder:
    """Builds and persists the stage-1 retrieval index for one cutoff.

    Public surface is two methods:
      * `build_doc_set(cutoff)` -> `IndexDocSet`: the leakage chokepoint.
        Filters the corpus, runs the encoder, returns vectors + ids.
      * `persist(doc_set, cutoff, path)` -> `IndexHandle`: writes the backend
        artifact and the sidecar metadata.
    A convenience `build_and_persist` chains them.
    """

    def __init__(
        self,
        *,
        encoder: TextEncoder | None = None,
        repository: SnapshotRepository | None = None,
        config: BiEncoderIndexConfig | None = None,
    ):
        self.config = config or BiEncoderIndexConfig()
        self.repository = repository or DjangoSnapshotRepository()
        self.encoder = encoder or SentenceTransformerEncoder(self.config.model_name)

    def build_doc_set(self, cutoff: TemporalCutoff) -> IndexDocSet:
        """Pull eligible markets, gate each on the cutoff, then encode."""
        market_ids: list[str] = []
        first_seen: list[datetime] = []
        titles: list[str] = []
        texts: list[str] = []
        for doc in self.repository.iter_eligible_markets(cutoff=cutoff):
            self._add_doc(doc, cutoff, market_ids, first_seen, titles, texts)
        if not texts:
            return IndexDocSet(
                market_ids=[],
                vectors=np.zeros((0, self.encoder.embedding_dim), dtype=np.float32),
                first_seen=[],
                titles=[],
            )
        vectors = self.encoder.encode_passages(texts)
        vectors = np.ascontiguousarray(vectors, dtype=np.float32)
        if vectors.shape[0] != len(market_ids):
            raise RuntimeError(
                f"encoder returned {vectors.shape[0]} vectors for {len(market_ids)} docs"
            )
        return IndexDocSet(
            market_ids=market_ids,
            vectors=vectors,
            first_seen=first_seen,
            titles=titles,
        )

    def _add_doc(
        self,
        doc: MarketDoc,
        cutoff: TemporalCutoff,
        market_ids: list[str],
        first_seen: list[datetime],
        titles: list[str],
        texts: list[str],
    ) -> None:
        """Per-row leakage gate. Any path that adds a doc lives here."""
        if doc.first_seen is None:
            # No timestamp = cannot prove it's pre-cutoff. Skip rather than guess.
            return
        # The chokepoint. Raises LeakageError on >= cutoff.
        assert_before_cutoff(doc.first_seen, cutoff, what="market.first_seen")
        market_ids.append(doc.market_id)
        first_seen.append(_ensure_utc(doc.first_seen))
        titles.append(doc.title)
        texts.append(doc.text)

    def persist(
        self,
        doc_set: IndexDocSet,
        cutoff: TemporalCutoff,
        *,
        path: Path,
        backend_name: str | None = None,
    ) -> IndexHandle:
        backend_name = backend_name or _select_backend(self.config.prefer_faiss)
        path.parent.mkdir(parents=True, exist_ok=True)
        index_path = path.with_suffix(".bin")
        meta_path = path.with_suffix(".meta.json")

        if backend_name == "faiss":
            backend = FaissBackend.from_vectors(doc_set.vectors)
        else:
            backend = NumpyBackend(doc_set.vectors)
        backend.save(index_path)

        handle = IndexHandle(
            cutoff=cutoff,
            model_name=self.encoder.model_name,
            embedding_dim=self.encoder.embedding_dim,
            market_ids=list(doc_set.market_ids),
            first_seen=list(doc_set.first_seen),
            backend_name=backend_name,
            index_path=index_path,
            meta_path=meta_path,
            _backend=backend,
        )
        meta_path.write_text(json.dumps(handle.to_meta(), indent=2), encoding="utf-8")
        logger.info(
            "biencoder_index: persisted size=%d cutoff=%s backend=%s -> %s",
            handle.size, cutoff.timestamp, backend_name, index_path,
        )
        return handle

    def build_and_persist(
        self,
        cutoff: TemporalCutoff,
        *,
        path: Path,
    ) -> IndexHandle:
        doc_set = self.build_doc_set(cutoff)
        return self.persist(doc_set, cutoff, path=path)


def load_index(meta_path: Path) -> IndexHandle:
    """Re-hydrate an `IndexHandle` from disk."""
    return _load_index_handle(meta_path)


# ---------------------------------------------------------------------------
# Reranker (registered) + candidate provider for the label miner
# ---------------------------------------------------------------------------


@register("biencoder-cosine")
class BiEncoderReranker:
    """The retrieval-only baseline + stage-1 retriever.

    Two usage modes:
      * `retrieve(source, top_k=100, cutoff=...)` -- stage-1 retrieval over
        the index. Used by the benchmark and by the label miner's hard-
        negative provider.
      * `rerank(source, candidates, *, cutoff=...)` -- score an arbitrary
        candidate list by cosine. Lets the benchmark put "retrieval-only"
        on the same axes as the cross-encoder.
    """

    requires_training = False

    def __init__(
        self,
        *,
        encoder: TextEncoder | None = None,
        index: IndexHandle | None = None,
        model_name: str = DEFAULT_MODEL,
    ):
        self.model_name = model_name
        self._encoder = encoder
        self._index = index

    @classmethod
    def from_index_path(cls, meta_path: Path, *, encoder: TextEncoder | None = None) -> "BiEncoderReranker":
        index = load_index(meta_path)
        return cls(encoder=encoder, index=index, model_name=index.model_name)

    @property
    def encoder(self) -> TextEncoder:
        if self._encoder is None:
            self._encoder = SentenceTransformerEncoder(self.model_name)
        return self._encoder

    def retrieve(
        self,
        source: MarketDoc,
        *,
        top_k: int = 100,
        cutoff: TemporalCutoff,
    ) -> list[ScoredCandidate]:
        if self._index is None:
            raise RuntimeError(
                "BiEncoderReranker has no index. Build one with "
                "`BiEncoderIndexBuilder` or load via `from_index_path`."
            )
        # Leakage chokepoint: query cutoff must be at or after the build cutoff.
        # If the index was built with `T_build` and the caller asks at `T_query`,
        # then `T_build <= T_query` means everything in the index existed before
        # the query cutoff too -- safe. The reverse would surface post-query data.
        assert_before_cutoff(
            self._index.cutoff.timestamp,
            cutoff,
            what="index build cutoff vs query cutoff",
            allow_equal=True,
        )
        query_vec = self.encoder.encode_query(source.text)
        return self._index.search(query_vec, top_k=top_k)

    def rerank(
        self,
        source: MarketDoc,
        candidates: Sequence[MarketDoc],
        *,
        cutoff: TemporalCutoff,
    ) -> list[ScoredCandidate]:
        """Score `candidates` by cosine with `source`, no index needed."""
        for cand in candidates:
            if cand.first_seen is not None:
                assert_before_cutoff(cand.first_seen, cutoff, what="candidate.first_seen")
        if not candidates:
            return []
        query_vec = self.encoder.encode_query(source.text)
        passages = self.encoder.encode_passages([c.text for c in candidates])
        scores = passages @ query_vec.astype(np.float32)
        order = np.argsort(-scores, kind="stable")
        return [
            ScoredCandidate(
                market_id=candidates[int(i)].market_id,
                score=float(scores[int(i)]),
                rank=rank + 1,
            )
            for rank, i in enumerate(order)
        ]


class BiEncoderCandidateProvider:
    """Plug `BiEncoderReranker` into `LabelMiner` as a `CandidateProvider`.

    Yields the bi-encoder's top-k neighbors for each source. They're hinted
    as `"hard"` -- semantically-similar candidates that the label miner will
    grade for actual co-movement. The miner's `_resolve_kind` keeps the
    grader's verdict authoritative: a candidate the bi-encoder retrieved AND
    that scores as positive becomes `"positive"`, not `"hard"`.

    This is the stage-1/stage-2 coupling Ranker.md §"Label miner" requires.
    """

    def __init__(
        self,
        reranker: BiEncoderReranker,
        *,
        cutoff: TemporalCutoff,
        top_k: int = 100,
    ):
        self.reranker = reranker
        self.cutoff = cutoff
        self.top_k = top_k

    def __call__(self, source: MarketDoc, corpus: Iterable[MarketDoc]):
        retrieved = self.reranker.retrieve(source, top_k=self.top_k, cutoff=self.cutoff)
        corpus_by_id = {doc.market_id: doc for doc in corpus}
        for candidate in retrieved:
            doc = corpus_by_id.get(candidate.market_id)
            if doc is None or doc.market_id == source.market_id:
                continue
            yield doc, "hard"


# ---------------------------------------------------------------------------
# Evaluation helper -- Recall@100 of label>=2 positives on a labelled split
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RecallReport:
    split_name: str
    cutoff: datetime
    n_sources: int
    recall_at_100: float
    p95_latency_ms: float
    per_source: list[dict]


def evaluate_recall_at_k(
    reranker: BiEncoderReranker,
    *,
    examples: Sequence[dict],
    docs_by_id: dict[str, MarketDoc],
    cutoff: TemporalCutoff,
    k: int = 100,
    relevance_threshold: int = 2,
) -> RecallReport:
    """Recall@k over labelled (source, candidates) examples.

    `examples` is a list of dicts shaped like `RankingExample`:
      {"source_market_id": str, "candidates": [{"candidate_market_id": str, "relevance": int}], ...}
    `docs_by_id` provides text for every source the retriever needs to encode.

    Per-source recall is `|positives in top-k| / |positives total|`; the report
    averages across sources. Latencies are wall-clock around `retrieve()`.
    """
    if not examples:
        return RecallReport(
            split_name="",
            cutoff=cutoff.timestamp,
            n_sources=0,
            recall_at_100=0.0,
            p95_latency_ms=0.0,
            per_source=[],
        )
    latencies_ms: list[float] = []
    per_source: list[dict] = []
    recalls: list[float] = []
    for example in examples:
        source_id = example["source_market_id"]
        source = docs_by_id.get(source_id)
        if source is None:
            continue
        positive_ids = {
            c["candidate_market_id"]
            for c in example.get("candidates", [])
            if int(c.get("relevance", 0)) >= relevance_threshold
        }
        if not positive_ids:
            continue
        t0 = time.perf_counter()
        hits = reranker.retrieve(source, top_k=k, cutoff=cutoff)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        hit_ids = {h.market_id for h in hits}
        retrieved = len(positive_ids & hit_ids)
        recall = retrieved / len(positive_ids)
        recalls.append(recall)
        per_source.append(
            {
                "source_market_id": source_id,
                "positives": sorted(positive_ids),
                "retrieved_positive_count": retrieved,
                "recall_at_k": recall,
            }
        )
    mean_recall = float(np.mean(recalls)) if recalls else 0.0
    p95 = float(np.percentile(latencies_ms, 95)) if latencies_ms else 0.0
    return RecallReport(
        split_name="",
        cutoff=cutoff.timestamp,
        n_sources=len(recalls),
        recall_at_100=mean_recall,
        p95_latency_ms=p95,
        per_source=per_source,
    )
