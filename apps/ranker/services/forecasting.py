"""Module 7: leakage-safe forecasting probe for reranked related markets.

The ranker test split identifies held-out source markets. It does not supply
candidate membership here: using labelled candidate lists as forecast inputs
would leak relevance information into the probe. At each forecast timestamp,
the service rebuilds the candidate corpus from snapshots strictly before that
timestamp, applies a reranker, and appends aggregate probability level and
momentum from its top-k results to a source-only forecast feature vector.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.ranker.models import RankingExample, RerankerRun, TemporalSplit
from apps.ranker.services._registry import Reranker
from apps.ranker.services._repository import DjangoSnapshotRepository
from apps.web.models import MarketSnapshot
from apps.web.services.market_intelligence import (
    ResolutionForecastService,
    _snapshot_resolution_target,
    _snapshot_yes_probability,
)
from apps.web.services.ml_hooks import BinaryLogisticRegression
from chaoswing.ml._types import ScoredCandidate, TemporalCutoff
from chaoswing.ml.leakage import assert_before_cutoff


@dataclass(frozen=True, slots=True)
class _ProbabilityState:
    snapshot: MarketSnapshot
    probability: float
    momentum: float


@dataclass(frozen=True, slots=True)
class _RelatedSignal:
    market_ids: list[str]
    observation_times: list[str]
    mean_probability: float
    mean_momentum: float
    coverage: float

    def as_vector(self) -> list[float]:
        return [self.mean_probability, self.mean_momentum, self.coverage]


class ForecastingProbeService:
    """Compare a source-only forecast against one augmented by ranked markets."""

    def __init__(
        self,
        *,
        split: TemporalSplit,
        reranker: Reranker,
        method: str,
        top_k: int = 10,
        min_train_size: int = 8,
    ):
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if min_train_size < 2:
            raise ValueError("min_train_size must be at least 2")
        self.split = split
        self.reranker = reranker
        self.method = method
        self.top_k = top_k
        self.min_train_size = min_train_size
        self.repository = DjangoSnapshotRepository(min_observations=1)

    def build_examples(self) -> list[dict[str, Any]]:
        """Build chronologically ordered, strictly pre-forecast feature rows."""
        source_ids = set(
            RankingExample.objects.filter(split=self.split, split_name="test")
            .order_by()
            .values_list("source_market_id", flat=True)
            .distinct()
        )
        if not source_ids:
            return []

        labelled_snapshots = (
            MarketSnapshot.objects.select_related("resolution_label")
            .filter(
                event_slug__in=source_ids,
                resolution_label__isnull=False,
                snapshot_at__gt=self.split.val_cutoff,
            )
            .order_by("event_slug", "-snapshot_at", "-created_at")
        )
        latest_by_source: dict[str, MarketSnapshot] = {}
        for snapshot in labelled_snapshots.iterator():
            latest_by_source.setdefault(snapshot.event_slug, snapshot)
        target_snapshots = sorted(
            latest_by_source.values(),
            key=lambda snapshot: (snapshot.snapshot_at, snapshot.created_at),
        )
        examples: list[dict[str, Any]] = []
        for target_snapshot in target_snapshots:
            example = self._build_example(target_snapshot)
            if example is not None:
                examples.append(example)
        return examples

    def run(self, *, persist: bool = True) -> dict[str, Any]:
        examples = self.build_examples()
        evaluation_rows: list[dict[str, Any]] = []
        latest_coefficients: dict[str, list[float]] = {}
        if len(examples) > self.min_train_size:
            for index in range(self.min_train_size, len(examples)):
                training = examples[:index]
                target = examples[index]
                targets = [int(row["target"]) for row in training]

                baseline_model = BinaryLogisticRegression()
                baseline_model.fit(
                    [row["baseline_vector"] for row in training],
                    targets,
                )
                challenger_model = BinaryLogisticRegression()
                challenger_model.fit(
                    [row["challenger_vector"] for row in training],
                    targets,
                )
                evaluation_rows.append(
                    {
                        "event_slug": target["event_slug"],
                        "forecast_at": target["forecast_at"],
                        "target": int(target["target"]),
                        "baseline_probability": baseline_model.predict_proba(
                            target["baseline_vector"]
                        ),
                        "challenger_probability": challenger_model.predict_proba(
                            target["challenger_vector"]
                        ),
                        "related_market_ids": target["related_market_ids"],
                    }
                )
                latest_coefficients = {
                    "baseline": baseline_model.coefficients(),
                    "challenger": challenger_model.coefficients(),
                }

        metrics = self._forecast_metrics(evaluation_rows)
        report = {
            "task_type": "forecasting_probe",
            "title": "Related-market forecasting probe",
            "method": self.method,
            "split": self.split.name,
            "dataset_version": f"{self.split.name}:forecast_examples:{len(examples)}",
            "metrics": metrics,
            "example_count": len(examples),
            "evaluated_examples": len(evaluation_rows),
            "minimum_train_size": self.min_train_size,
            "top_k": self.top_k,
        }
        if persist and evaluation_rows:
            RerankerRun.objects.create(
                method=self.method,
                kind="probe",
                split=self.split,
                metrics=metrics,
                config={
                    "probe": "related-market-signals",
                    "top_k": self.top_k,
                    "minimum_train_size": self.min_train_size,
                    "feature_contract": {
                        "baseline": "source_probability + source_momentum",
                        "challenger": (
                            "baseline + related_mean_probability + "
                            "related_mean_momentum + related_coverage"
                        ),
                        "timestamp_rule": "all feature observations strictly before forecast_at",
                    },
                    "latest_coefficients": latest_coefficients,
                },
                notes=(
                    "Expanding-window source-only forecast versus a challenger "
                    "augmented with reranked related-market signals."
                ),
            )
        return report

    def _build_example(self, target_snapshot: MarketSnapshot) -> dict[str, Any] | None:
        target = _snapshot_resolution_target(target_snapshot)
        if target is None:
            return None
        cutoff = TemporalCutoff(
            timestamp=target_snapshot.snapshot_at,
            label=f"forecast_at:{target_snapshot.event_slug}",
        )
        source_state = self._latest_state_before(target_snapshot.event_slug, cutoff)
        if source_state is None:
            return None

        corpus_ids = set(
            MarketSnapshot.objects.filter(snapshot_at__lt=cutoff.timestamp)
            .order_by()
            .values_list("event_slug", flat=True)
            .distinct()
        )
        corpus_ids.discard(target_snapshot.event_slug)
        docs = self.repository.load_market_docs(
            corpus_ids | {target_snapshot.event_slug},
            cutoff=cutoff,
        )
        source_doc = docs.get(target_snapshot.event_slug)
        if source_doc is None:
            return None
        candidates = [docs[market_id] for market_id in sorted(corpus_ids) if market_id in docs]
        ranked = self.reranker.rerank(source_doc, candidates, cutoff=cutoff)
        related_signal = self._aggregate_related_signal(ranked, cutoff)

        baseline_vector = [source_state.probability, source_state.momentum]
        return {
            "event_slug": target_snapshot.event_slug,
            "forecast_at": cutoff.timestamp.isoformat(),
            "target": int(target),
            "baseline_vector": baseline_vector,
            "challenger_vector": baseline_vector + related_signal.as_vector(),
            "source_observation_at": source_state.snapshot.snapshot_at.isoformat(),
            "related_market_ids": related_signal.market_ids,
            "related_observation_times": related_signal.observation_times,
            "related_mean_probability": related_signal.mean_probability,
            "related_mean_momentum": related_signal.mean_momentum,
        }

    def _latest_state_before(
        self,
        market_id: str,
        cutoff: TemporalCutoff,
    ) -> _ProbabilityState | None:
        valid: list[tuple[MarketSnapshot, float]] = []
        snapshots = MarketSnapshot.objects.filter(
            event_slug=market_id,
            snapshot_at__lt=cutoff.timestamp,
        ).order_by("-snapshot_at", "-created_at")
        for snapshot in snapshots.iterator():
            assert_before_cutoff(
                snapshot.snapshot_at,
                cutoff,
                what=f"feature snapshot {market_id}",
            )
            probability = _snapshot_yes_probability(snapshot)
            if probability is None:
                continue
            valid.append((snapshot, probability))
            if len(valid) == 2:
                break
        if not valid:
            return None
        latest_snapshot, latest_probability = valid[0]
        momentum = latest_probability - valid[1][1] if len(valid) > 1 else 0.0
        return _ProbabilityState(
            snapshot=latest_snapshot,
            probability=latest_probability,
            momentum=momentum,
        )

    def _aggregate_related_signal(
        self,
        ranked: list[ScoredCandidate],
        cutoff: TemporalCutoff,
    ) -> _RelatedSignal:
        states: list[tuple[str, _ProbabilityState]] = []
        for result in ranked[: self.top_k]:
            state = self._latest_state_before(result.market_id, cutoff)
            if state is not None:
                states.append((result.market_id, state))
        if not states:
            return _RelatedSignal(
                market_ids=[],
                observation_times=[],
                mean_probability=0.0,
                mean_momentum=0.0,
                coverage=0.0,
            )
        return _RelatedSignal(
            market_ids=[market_id for market_id, _state in states],
            observation_times=[
                state.snapshot.snapshot_at.isoformat() for _market_id, state in states
            ],
            mean_probability=sum(state.probability for _market_id, state in states) / len(states),
            mean_momentum=sum(state.momentum for _market_id, state in states) / len(states),
            coverage=len(states) / self.top_k,
        )

    def _forecast_metrics(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        reusable_rows = [
            {
                "target": row["target"],
                "baseline_probability": row["baseline_probability"],
                "model_probability": row["challenger_probability"],
            }
            for row in rows
        ]
        base = ResolutionForecastService(min_train_size=self.min_train_size)._forecast_metrics(
            reusable_rows
        )
        return {
            "example_count": base["example_count"],
            "positive_rate": base["positive_rate"],
            "baseline_brier": base["baseline_brier"],
            "challenger_brier": base["model_brier"],
            "brier_lift": base["brier_lift"],
            "baseline_log_loss": base["baseline_log_loss"],
            "challenger_log_loss": base["model_log_loss"],
            "log_loss_lift": base["log_loss_lift"],
            "baseline_accuracy": base["baseline_accuracy"],
            "challenger_accuracy": base["model_accuracy"],
            "baseline_calibration_error": base["baseline_calibration_error"],
            "challenger_calibration_error": base["model_calibration_error"],
            "calibration_lift": (
                base["baseline_calibration_error"] - base["model_calibration_error"]
            ),
        }
