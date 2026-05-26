"""Tests for Module 7: the related-market forecasting probe."""
from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta

from django.core.management import call_command
from django.test import TestCase

from apps.ranker.models import RankingExample, RerankerRun, TemporalSplit
from apps.ranker.services.forecasting import ForecastingProbeService
from apps.web.models import MarketSnapshot, ResolutionLabel
from chaoswing.ml._types import ScoredCandidate

VAL_CUTOFF = datetime(2025, 6, 1, tzinfo=UTC)


def _snapshot(
    slug: str,
    *,
    title: str,
    at: datetime,
    probability: float,
    resolved_outcome: str | None = None,
) -> MarketSnapshot:
    snapshot = MarketSnapshot.objects.create(
        source_url=f"https://example.test/{slug}",
        event_slug=slug,
        event_title=title,
        outcomes=["Yes", "No"],
        implied_probability=probability,
        volume=1000.0,
        liquidity=500.0,
        open_interest=200.0,
        snapshot_at=at,
        payload={
            "markets": [
                {
                    "outcomes": ["Yes", "No"],
                    "outcome_prices": [probability, 1.0 - probability],
                }
            ]
        },
    )
    if resolved_outcome is not None:
        ResolutionLabel.objects.create(
            market_snapshot=snapshot,
            event_slug=slug,
            resolved_outcome=resolved_outcome,
            resolved_probability=0.995,
            source="test",
        )
    return snapshot


class _SignalFirstReranker:
    name = "signal-first"
    requires_training = False

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def rerank(self, source, candidates, *, cutoff):
        self.calls.append(
            {
                "source": source,
                "candidates": list(candidates),
                "cutoff": cutoff,
            }
        )
        ordered = sorted(candidates, key=lambda doc: (doc.market_id != "signal", doc.market_id))
        return [
            ScoredCandidate(market_id=doc.market_id, score=1.0 / rank, rank=rank)
            for rank, doc in enumerate(ordered, start=1)
        ]


class ForecastingProbeTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.split = TemporalSplit.objects.create(
            name="forecast-probe",
            train_cutoff=VAL_CUTOFF - timedelta(days=30),
            val_cutoff=VAL_CUTOFF,
            family_assignments={},
            counts={"test": 5},
        )
        first_target = VAL_CUTOFF + timedelta(days=10)
        _snapshot(
            "signal",
            title="Signal related market",
            at=first_target - timedelta(days=2),
            probability=0.40,
        )
        _snapshot(
            "signal",
            title="Signal related market",
            at=first_target - timedelta(days=1),
            probability=0.45,
        )
        # This value is at the first forecast cutoff and must not enter its features.
        _snapshot(
            "signal",
            title="Signal related market",
            at=first_target,
            probability=0.99,
        )
        _snapshot(
            "boundary-only",
            title="Not available at first forecast",
            at=first_target,
            probability=0.75,
        )

        for index in range(5):
            slug = f"source-{index}"
            target_at = first_target + timedelta(days=2 * index)
            earlier_snapshot = _snapshot(
                slug,
                title=f"Resolved source market {index}",
                at=target_at - timedelta(days=2),
                probability=0.20 + (0.10 * index),
            )
            if index == 0:
                ResolutionLabel.objects.create(
                    market_snapshot=earlier_snapshot,
                    event_slug=slug,
                    resolved_outcome="No",
                    resolved_probability=0.995,
                    source="propagated-test-label",
                )
            _snapshot(
                slug,
                title=f"Resolved source market {index}",
                at=target_at - timedelta(days=1),
                probability=0.25 + (0.10 * index),
            )
            _snapshot(
                slug,
                title=f"Resolved source market {index}",
                at=target_at,
                probability=0.30 + (0.10 * index),
                resolved_outcome="Yes" if index % 2 else "No",
            )
            RankingExample.objects.create(
                split=cls.split,
                split_name="test",
                source_market_id=slug,
                event_family=slug,
                candidates=[],
            )

    def test_features_and_candidates_are_strictly_pre_forecast(self) -> None:
        ranker = _SignalFirstReranker()
        service = ForecastingProbeService(
            split=self.split,
            reranker=ranker,
            method="signal-first",
            top_k=1,
            min_train_size=2,
        )

        examples = service.build_examples()

        self.assertEqual(len(examples), 5)
        first = examples[0]
        self.assertEqual(first["related_market_ids"], ["signal"])
        self.assertAlmostEqual(first["related_mean_probability"], 0.45)
        self.assertAlmostEqual(first["related_mean_momentum"], 0.05)
        self.assertNotIn(
            "boundary-only",
            {doc.market_id for doc in ranker.calls[0]["candidates"]},
        )
        for example in examples:
            forecast_at = datetime.fromisoformat(example["forecast_at"])
            source_at = datetime.fromisoformat(example["source_observation_at"])
            self.assertLess(source_at, forecast_at)
            for related_at in example["related_observation_times"]:
                self.assertLess(datetime.fromisoformat(related_at), forecast_at)
        for call in ranker.calls:
            for candidate in call["candidates"]:
                self.assertLess(candidate.first_seen, call["cutoff"].timestamp)

    def test_run_persists_probe_metrics(self) -> None:
        service = ForecastingProbeService(
            split=self.split,
            reranker=_SignalFirstReranker(),
            method="signal-first",
            top_k=1,
            min_train_size=2,
        )

        report = service.run(persist=True)

        self.assertEqual(report["task_type"], "forecasting_probe")
        self.assertEqual(report["example_count"], 5)
        self.assertEqual(report["evaluated_examples"], 3)
        self.assertIn("challenger_brier", report["metrics"])
        self.assertIn("calibration_lift", report["metrics"])
        run = RerankerRun.objects.get(kind="probe", method="signal-first")
        self.assertEqual(run.split, self.split)
        self.assertEqual(run.metrics["example_count"], 3)
        self.assertEqual(run.config["top_k"], 1)

    def test_command_runs_with_stateless_baseline_without_persisting(self) -> None:
        stdout = io.StringIO()

        call_command(
            "run_forecasting_probe",
            "--split",
            self.split.name,
            "--method",
            "lexical-overlap",
            "--top-k",
            "1",
            "--min-train-size",
            "2",
            "--no-persist",
            stdout=stdout,
        )

        report = json.loads(stdout.getvalue())
        self.assertEqual(report["task_type"], "forecasting_probe")
        self.assertEqual(report["method"], "lexical-overlap")
        self.assertEqual(report["evaluated_examples"], 3)
        self.assertFalse(RerankerRun.objects.filter(kind="probe").exists())
