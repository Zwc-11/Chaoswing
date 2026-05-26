"""Pairwise and listwise ranking losses for the cross-encoder.

Both losses operate on per-source candidate lists. Shapes are batch-first:

    scores: (batch, k)   -- model output, one scalar per (source, candidate) pair
    labels: (batch, k)   -- graded relevance in {0, 1, 2, 3}

Listwise softmax (the recommended headline loss, per Ranker.md and the build
plan §6) optimizes NDCG directly: it converts the graded labels into a target
distribution `softmax(labels)` and minimizes cross-entropy against the score
distribution `softmax(scores)`. Pairwise RankNet is a secondary option for
ablations.

Torch is imported lazily so this module is safe to import on a machine that
doesn't have the `[ml]` extra installed -- everywhere else in the pipeline
only needs the function signatures.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import torch
    Tensor = torch.Tensor
else:  # pragma: no cover - import guard
    Tensor = object  # type: ignore[assignment,misc]


def _coerce_batched(scores: "Tensor", labels: "Tensor") -> tuple["Tensor", "Tensor"]:
    """Promote 1D `(k,)` inputs to 2D `(1, k)` so the math always sees a batch."""
    import torch

    if scores.shape != labels.shape:
        raise ValueError(
            f"scores and labels must have the same shape, got {tuple(scores.shape)} "
            f"and {tuple(labels.shape)}"
        )
    if scores.ndim == 1:
        scores = scores.unsqueeze(0)
        labels = labels.unsqueeze(0)
    elif scores.ndim != 2:
        raise ValueError(f"expected 1D or 2D tensors, got {scores.ndim}D")
    return scores, labels.to(torch.float32)


def listwise_softmax_loss(scores: "Tensor", labels: "Tensor") -> "Tensor":
    """Listwise softmax cross-entropy (ListNet-style).

    Target distribution: `softmax(labels)`. Predicted: `softmax(scores)`. Loss
    is `-Σ target · log_softmax(scores)` averaged over the batch.

    This is the loss the build plan §6 recommends ("listwise tends to win on
    NDCG@5 and is the better interview answer than '4-class CE'"). It rewards
    score *ordering* that matches label ordering, not absolute score values.
    """
    import torch
    import torch.nn.functional as F  # noqa: N812

    scores2d, labels2d = _coerce_batched(scores, labels)
    target = F.softmax(labels2d, dim=-1)
    pred_log = F.log_softmax(scores2d, dim=-1)
    per_row = -(target * pred_log).sum(dim=-1)
    return per_row.mean()


def pairwise_ranknet_loss(scores: "Tensor", labels: "Tensor") -> "Tensor":
    """RankNet pairwise loss.

    For every pair `(i, j)` in the same source where `labels[i] > labels[j]`,
    encourage `sigmoid(scores[i] - scores[j])` to be 1. Mean over valid pairs.
    If a source has no valid pairs (all labels equal), it contributes zero.
    """
    import torch
    import torch.nn.functional as F  # noqa: N812

    scores2d, labels2d = _coerce_batched(scores, labels)
    # Pairwise score and label differences, shape (batch, k, k).
    score_diff = scores2d.unsqueeze(-1) - scores2d.unsqueeze(-2)
    label_diff = labels2d.unsqueeze(-1) - labels2d.unsqueeze(-2)
    valid = (label_diff > 0).to(scores2d.dtype)  # 1 where i is more relevant than j
    valid_count = valid.sum()
    if valid_count.item() == 0:
        # Return a zero tensor that participates in autograd.
        return (scores2d * 0.0).sum()
    targets = valid  # BCE target: 1.0 for "i should rank above j"
    loss_per_pair = F.binary_cross_entropy_with_logits(score_diff, targets, reduction="none")
    return (loss_per_pair * valid).sum() / valid_count
