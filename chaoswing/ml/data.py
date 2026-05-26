"""Torch Dataset classes + materializers for the cross-encoder trainer.

Two dataset shapes:

* `CrossEncoderDataset` — one row per (source, candidate, label). Used for
  4-class CE (not the headline) and ad-hoc debugging.
* `ListwiseDataset` — one row per source, holding a *fixed-size* list of `k`
  candidates and their relevances. The trainer reshapes the resulting
  `(batch, k, seq_len)` tensors to `(batch * k, seq_len)` for the encoder
  and back to `(batch, k)` for the listwise loss.

Sources are required to have at least one positive (relevance >= 2) and one
negative (relevance < 2) candidate. Ragged lists are padded to `k` by
sampling-with-replacement from the available negatives, which keeps the
batch shape clean without introducing fake positives.

Torch is imported lazily; modules that only need the dataclasses (e.g. tests
without a torch install) can still import this file safely.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from chaoswing.ml._types import MarketDoc, RelevanceRecord

if TYPE_CHECKING:
    from torch.utils.data import Dataset
else:
    Dataset = object  # type: ignore[assignment,misc]


POSITIVE_THRESHOLD = 2  # relevance >= this counts as a positive


@dataclass(slots=True)
class CrossEncoderExample:
    source: MarketDoc
    candidate: MarketDoc
    relevance: int


@dataclass(slots=True)
class ListwiseExample:
    source: MarketDoc
    candidates: list[MarketDoc]
    relevances: list[int]


# ---------------------------------------------------------------------------
# Materializers
# ---------------------------------------------------------------------------


def materialize_examples(
    records: Sequence[RelevanceRecord],
    docs_by_id: dict[str, MarketDoc],
) -> list[CrossEncoderExample]:
    """Join mined label records with their `MarketDoc`s into pair examples."""
    out: list[CrossEncoderExample] = []
    for record in records:
        source = docs_by_id.get(record.source_id)
        candidate = docs_by_id.get(record.candidate_id)
        if source is None or candidate is None:
            continue
        out.append(
            CrossEncoderExample(
                source=source,
                candidate=candidate,
                relevance=int(record.relevance),
            )
        )
    return out


def _sample_candidates(
    candidates: list[dict],
    *,
    k: int,
    rng: random.Random,
) -> list[dict]:
    """Pick `k` candidates: positives first, then hard, then easy.

    If fewer than `k` candidates exist after deduping, the negative pool is
    sampled with replacement to fill. We never duplicate positives, so the
    score distribution the listwise loss sees stays honest.
    """
    positives = [c for c in candidates if int(c.get("relevance", 0)) >= POSITIVE_THRESHOLD]
    hard = [c for c in candidates if c.get("negative_kind") == "hard"]
    easy = [c for c in candidates if c.get("negative_kind") == "easy"]
    rest = [
        c
        for c in candidates
        if int(c.get("relevance", 0)) < POSITIVE_THRESHOLD
        and c.get("negative_kind") not in {"hard", "easy"}
    ]

    out: list[dict] = []
    out.extend(positives[:k])
    if len(out) < k:
        rng.shuffle(hard)
        out.extend(hard[: k - len(out)])
    if len(out) < k:
        rng.shuffle(easy)
        out.extend(easy[: k - len(out)])
    if len(out) < k:
        rng.shuffle(rest)
        out.extend(rest[: k - len(out)])
    if len(out) < k:
        # Pad by sampling with replacement from the negatives we have.
        negatives = hard + easy + rest
        if not negatives:
            return out  # caller will skip this row
        while len(out) < k:
            out.append(rng.choice(negatives))
    return out


def materialize_listwise_examples(
    example_rows: Sequence[dict],
    docs_by_id: dict[str, MarketDoc],
    *,
    max_candidates: int = 16,
    seed: int = 1729,
) -> list[ListwiseExample]:
    """Build fixed-size listwise rows from `RankingExample.candidates` dicts.

    Each `row` is shaped like::

        {
            "source_market_id": str,
            "candidates": [
                {"candidate_market_id": str, "relevance": int, "negative_kind": str},
                ...
            ],
        }

    Sources missing both a positive and a negative are skipped (the listwise
    loss is degenerate when all labels are equal).
    """
    rng = random.Random(seed)
    out: list[ListwiseExample] = []
    for row in example_rows:
        source = docs_by_id.get(row["source_market_id"])
        if source is None:
            continue
        sampled = _sample_candidates(list(row.get("candidates", [])), k=max_candidates, rng=rng)
        if len(sampled) < max_candidates:
            continue
        cand_docs: list[MarketDoc] = []
        relevances: list[int] = []
        for c in sampled:
            doc = docs_by_id.get(c["candidate_market_id"])
            if doc is None:
                cand_docs = []
                break
            cand_docs.append(doc)
            relevances.append(int(c.get("relevance", 0)))
        if len(cand_docs) != max_candidates:
            continue
        if max(relevances) < POSITIVE_THRESHOLD or min(relevances) >= POSITIVE_THRESHOLD:
            continue  # need at least one positive and one negative
        out.append(ListwiseExample(source=source, candidates=cand_docs, relevances=relevances))
    return out


# ---------------------------------------------------------------------------
# Torch datasets
# ---------------------------------------------------------------------------


class CrossEncoderDataset(Dataset):  # type: ignore[misc]
    """`torch.utils.data.Dataset` over (source, candidate, label) examples."""

    def __init__(self, examples: Sequence[CrossEncoderExample], tokenizer, *, max_length: int = 128):
        self.examples = list(examples)
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, idx: int) -> dict:
        import torch

        ex = self.examples[idx]
        enc = self.tokenizer(
            ex.source.text,
            ex.candidate.text,
            padding="max_length",
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        return {
            "input_ids": enc["input_ids"].squeeze(0),
            "attention_mask": enc["attention_mask"].squeeze(0),
            "labels": torch.tensor(ex.relevance, dtype=torch.float32),
        }


class ListwiseDataset(Dataset):  # type: ignore[misc]
    """One row per source with a fixed-size candidate list.

    `__getitem__` returns tensors of shape `(k, seq_len)` for `input_ids` and
    `attention_mask` plus a `(k,)` `labels` tensor. The default torch
    `DataLoader` collate function stacks these into `(batch, k, seq_len)` and
    `(batch, k)`.
    """

    def __init__(
        self,
        examples: Sequence[ListwiseExample],
        tokenizer,
        *,
        max_length: int = 128,
    ):
        self.examples = list(examples)
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, idx: int) -> dict:
        import torch

        ex = self.examples[idx]
        sources = [ex.source.text] * len(ex.candidates)
        candidates = [c.text for c in ex.candidates]
        enc = self.tokenizer(
            sources,
            candidates,
            padding="max_length",
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        return {
            "input_ids": enc["input_ids"],
            "attention_mask": enc["attention_mask"],
            "labels": torch.tensor(ex.relevances, dtype=torch.float32),
        }
