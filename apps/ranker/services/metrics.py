"""Django-aware metric wrappers.

The math lives in `chaoswing.ml.eval`. This module adds the bits that need
Django settings or MLflow logging.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Sequence

from chaoswing.ml.eval import mean_over_queries, mrr, ndcg_at_k, recall_at_k


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RankingMetrics:
    ndcg_at_5: float
    ndcg_at_10: float
    mrr: float
    recall_at_100: float
    p95_latency_ms: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "ndcg_at_5": self.ndcg_at_5,
            "ndcg_at_10": self.ndcg_at_10,
            "mrr": self.mrr,
            "recall_at_100": self.recall_at_100,
            "p95_latency_ms": self.p95_latency_ms,
        }


def compute_ranking_metrics(
    per_query: Sequence[tuple[list[float], list[int]]],
    *,
    p95_latency_ms: float = 0.0,
) -> RankingMetrics:
    """Average NDCG@5, NDCG@10, MRR, Recall@100 across queries."""
    return RankingMetrics(
        ndcg_at_5=mean_over_queries(ndcg_at_k, per_query, k=5),
        ndcg_at_10=mean_over_queries(ndcg_at_k, per_query, k=10),
        mrr=mean_over_queries(mrr, per_query),
        recall_at_100=mean_over_queries(recall_at_k, per_query, k=100),
        p95_latency_ms=p95_latency_ms,
    )


@contextmanager
def mlflow_run(run_name: str | None) -> Iterator[object | None]:
    """Best-effort MLflow run context. If mlflow isn't installed, yields None.

    The benchmark and trainer commands wrap their work in this; metric logging
    is then guarded by `if run is not None`. Keeping the dependency optional
    means the test suite doesn't need to install mlflow.
    """
    try:
        import mlflow
    except ImportError:
        yield None
        return
    from django.conf import settings

    tracking_uri = getattr(settings, "CHAOSWING_MLFLOW_TRACKING_URI", "")
    experiment = getattr(settings, "CHAOSWING_MLFLOW_EXPERIMENT", "chaoswing")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_name) as run:
        yield run


def log_metrics(run, metrics: RankingMetrics) -> None:
    if run is None:
        return
    try:
        import mlflow
    except ImportError:
        return
    for key, value in metrics.as_dict().items():
        mlflow.log_metric(key, value)
