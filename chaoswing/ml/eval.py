"""Ranking metrics on raw arrays.

Pure numpy. Inputs are per-query: `scores` are the reranker's outputs in the
order it produced, and `labels` are the matching graded relevances (0-3).

Conventions:
  * Higher score = more relevant.
  * Ties are broken in the order given (no random shuffles).
  * `k` is clipped to `len(scores)`; small lists do not throw.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np


def _sorted_labels_by_score(scores: Sequence[float], labels: Sequence[int]) -> np.ndarray:
    s = np.asarray(scores, dtype=np.float64)
    l = np.asarray(labels, dtype=np.float64)
    if s.shape != l.shape:
        raise ValueError("scores and labels must have the same shape")
    order = np.argsort(-s, kind="stable")
    return l[order]


def dcg_at_k(scores: Sequence[float], labels: Sequence[int], k: int) -> float:
    """`(2**rel - 1) / log2(rank + 1)` form."""
    sorted_labels = _sorted_labels_by_score(scores, labels)
    k = min(k, sorted_labels.size)
    if k == 0:
        return 0.0
    top = sorted_labels[:k]
    gains = np.power(2.0, top) - 1.0
    discounts = 1.0 / np.log2(np.arange(2, k + 2, dtype=np.float64))
    return float(np.sum(gains * discounts))


def ndcg_at_k(scores: Sequence[float], labels: Sequence[int], k: int) -> float:
    if not labels:
        return 0.0
    dcg = dcg_at_k(scores, labels, k)
    ideal_scores = np.asarray(labels, dtype=np.float64)
    ideal_dcg = dcg_at_k(ideal_scores, labels, k)
    if ideal_dcg == 0.0:
        return 0.0
    return dcg / ideal_dcg


def mrr(scores: Sequence[float], labels: Sequence[int], *, threshold: int = 1) -> float:
    sorted_labels = _sorted_labels_by_score(scores, labels)
    hits = np.where(sorted_labels >= threshold)[0]
    if hits.size == 0:
        return 0.0
    return 1.0 / float(hits[0] + 1)


def recall_at_k(
    scores: Sequence[float],
    labels: Sequence[int],
    k: int,
    *,
    threshold: int = 1,
) -> float:
    sorted_labels = _sorted_labels_by_score(scores, labels)
    total_positive = int(np.sum(np.asarray(labels) >= threshold))
    if total_positive == 0:
        return 0.0
    k = min(k, sorted_labels.size)
    if k == 0:
        return 0.0
    hit = int(np.sum(sorted_labels[:k] >= threshold))
    return hit / total_positive


def mean_over_queries(metric_fn, per_query_inputs: Sequence[tuple], **kwargs) -> float:
    """Convenience: average a metric across queries."""
    if not per_query_inputs:
        return 0.0
    total = 0.0
    for scores, labels in per_query_inputs:
        total += float(metric_fn(scores, labels, **kwargs))
    return total / len(per_query_inputs)
