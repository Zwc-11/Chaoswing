"""Module 4 — fine-tuned cross-encoder reranker (the headline).

Inference-side. Training lives in `chaoswing.ml.train`. The class shape and
registration are pinned so the benchmark, the resume bullet, and the README
all reference the same `cross-encoder-finetuned` name.

Input format: `[CLS] source.text [SEP] candidate.text [SEP]`. No timestamps
reach the model. Leakage-safety reduces to:
  * training-data selection — handled by Module 2 (temporal splits)
  * inference candidate filtering — gated here in `rerank()` against the
    `TemporalCutoff` argument (same chokepoint as the bi-encoder)
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from chaoswing.ml._types import MarketDoc, ScoredCandidate, TemporalCutoff
from chaoswing.ml.leakage import assert_before_cutoff

from apps.ranker.services._registry import register


logger = logging.getLogger(__name__)


DEFAULT_BACKBONE = "microsoft/deberta-v3-base"
DEFAULT_MAX_LENGTH = 128


@register("cross-encoder-finetuned")
class FineTunedCrossEncoder:
    """The headline reranker, fine-tuned end-to-end on mined labels."""

    requires_training = True

    def __init__(
        self,
        *,
        checkpoint_dir: Path = Path("models/cross_encoder"),
        device: str | None = None,
        max_length: int = DEFAULT_MAX_LENGTH,
        batch_size: int = 32,
    ):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.device = device
        self.max_length = max_length
        self.batch_size = batch_size
        self._model = None
        self._tokenizer = None

    # ----- model loading -------------------------------------------------

    def load(self) -> None:
        """Materialize tokenizer + model from `checkpoint_dir`.

        Idempotent: a second call is a no-op once the model is in memory.
        """
        if self._model is not None and self._tokenizer is not None:
            return
        import torch  # lazy
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        if self.device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._tokenizer = AutoTokenizer.from_pretrained(self.checkpoint_dir)
        self._model = AutoModelForSequenceClassification.from_pretrained(
            self.checkpoint_dir
        ).to(self.device)
        self._model.eval()
        logger.info(
            "FineTunedCrossEncoder: loaded checkpoint=%s device=%s",
            self.checkpoint_dir, self.device,
        )

    @property
    def tokenizer(self):
        if self._tokenizer is None:
            self.load()
        return self._tokenizer

    @property
    def model(self):
        if self._model is None:
            self.load()
        return self._model

    # ----- inference -----------------------------------------------------

    def rerank(
        self,
        source: MarketDoc,
        candidates: Sequence[MarketDoc],
        *,
        cutoff: TemporalCutoff,
    ) -> list[ScoredCandidate]:
        """Score every candidate against `source` and return sorted descending.

        Per-candidate cutoff gate: any candidate with `first_seen >= cutoff`
        raises `LeakageError` *before* any model call. The model itself is
        text-only and can't peek at timestamps; this gate is the architectural
        promise that callers can't accidentally rerank future markets.
        """
        for cand in candidates:
            if cand.first_seen is not None:
                assert_before_cutoff(
                    cand.first_seen, cutoff, what=f"candidate {cand.market_id}.first_seen"
                )
        if not candidates:
            return []

        scores = self._score_pairs(source, candidates)
        order = np.argsort(-scores, kind="stable")
        return [
            ScoredCandidate(
                market_id=candidates[int(i)].market_id,
                score=float(scores[int(i)]),
                rank=rank + 1,
            )
            for rank, i in enumerate(order)
        ]

    def _score_pairs(self, source: MarketDoc, candidates: Sequence[MarketDoc]) -> np.ndarray:
        """Tokenize and forward `(source, candidate)` pairs in batches."""
        import torch  # lazy

        sources = [source.text] * len(candidates)
        cand_texts = [c.text for c in candidates]
        scores = np.zeros(len(candidates), dtype=np.float32)
        with torch.no_grad():
            for start in range(0, len(candidates), self.batch_size):
                end = min(start + self.batch_size, len(candidates))
                enc = self.tokenizer(
                    sources[start:end],
                    cand_texts[start:end],
                    padding=True,
                    truncation=True,
                    max_length=self.max_length,
                    return_tensors="pt",
                ).to(self.device)
                logits = self.model(**enc).logits.squeeze(-1)
                scores[start:end] = logits.detach().cpu().numpy().astype(np.float32)
        return scores
