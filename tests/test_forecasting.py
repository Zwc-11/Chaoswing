from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings

from apps.web.models import ExperimentRun, MarketSnapshot, ResolutionLabel
from apps.web.services.market_intelligence import BenchmarkSummaryService, DatasetBuilderService, ResolutionForecastService


@override_settings(
    CHAOSWING_ENABLE_REMOTE_FETCH=False,
    CHAOSWING_ENABLE_LLM=False,
    CHAOSWING_RATE_LIMIT_ENABLED=False,
)
class ResolutionForecastTests(TestCase):
    def setUp(self):
        cache.clear()
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        for index in range(12):
            yes_probability = round(0.18 + (index * 0.055), 3)
            related_market_count = index % 4
            evidence_count = (index + 1) % 3
            feature_signal = yes_probability + (0.05 * related_market_count) + (0.04 * evidence_count)
            target = 1 if feature_signal >= 0.56 else 0

            snapshot = MarketSnapshot.objects.create(
                source_url=f"https://polymarket.com/event/unit-test-{index}",
                event_slug=f"unit-test-{index}",
                event_title=f"Unit test market {index}",
                status="closed",
                category="Test",
                source_kind="unit-test",
                tags=["test"],
                outcomes=["Yes", "No"],
                implied_probability=yes_probability,
                volume=1_000 + (index * 250),
                liquidity=700 + (index * 150),
                open_interest=400 + (index * 90),
                related_market_count=related_market_count,
                evidence_count=evidence_count,
                snapshot_at=base_time + timedelta(days=index),
                payload={
                    "markets": [
                        {
                            "outcomes": ["Yes", "No"],
                            "outcome_prices": [yes_probability, round(1.0 - yes_probability, 3)],
                        }
                    ]
                },
            )
            ResolutionLabel.objects.create(
                market_snapshot=snapshot,
                event_slug=snapshot.event_slug,
                resolved_outcome="Yes" if target else "No",
                resolved_probability=0.995,
                source="unit-test",
            )

    def test_resolution_backtest_runs_and_updates_benchmark_summary(self):
        summary_before = BenchmarkSummaryService().build_cached(force_refresh=True)
        self.assertFalse(
            any(
                benchmark["name"] == "Resolution forecasting rolling backtest"
                for benchmark in summary_before["live_benchmarks"]
            )
        )

        report = ResolutionForecastService(min_train_size=4).run(persist=True)

        self.assertEqual(report["task_type"], "resolution_backtest")
        self.assertEqual(report["evaluated_examples"], 8)
        self.assertGreater(report["metrics"]["example_count"], 0)
        self.assertIn("model_brier", report["metrics"])
        self.assertIn("baseline_brier", report["metrics"])
        self.assertTrue(ExperimentRun.objects.filter(task_type="resolution_backtest").exists())

        summary_after = BenchmarkSummaryService().build_cached()
        self.assertTrue(
            any(
                benchmark["name"] == "Resolution forecasting rolling backtest"
                for benchmark in summary_after["live_benchmarks"]
            )
        )
        self.assertFalse(
            any(
                benchmark["name"] == "Resolution forecasting"
                for benchmark in summary_after["next_benchmarks"]
            )
        )

    def test_resolution_backtest_command_and_dataset_export(self):
        stdout = io.StringIO()

        call_command(
            "run_resolution_backtest",
            "--no-persist",
            "--min-train-size",
            "4",
            stdout=stdout,
        )
        payload = json.loads(stdout.getvalue())

        self.assertEqual(payload["task_type"], "resolution_backtest")
        self.assertEqual(payload["evaluated_examples"], 8)

        records = DatasetBuilderService().build_records()
        self.assertIn("resolution_forecast_examples", records)
        self.assertEqual(len(records["resolution_forecast_examples"]), 12)

    def test_resolution_benchmark_stays_next_when_evaluated_sample_is_too_small(self):
        ExperimentRun.objects.create(
            task_type="resolution_backtest",
            title="Resolution forecasting rolling backtest",
            dataset_version="resolution_labels:9",
            metrics={
                "example_count": 1,
                "model_brier": 0.104,
                "brier_lift": 0.012,
            },
        )

        summary = BenchmarkSummaryService().build_cached(force_refresh=True)

        self.assertFalse(
            any(
                benchmark["name"] == "Resolution forecasting rolling backtest"
                for benchmark in summary["live_benchmarks"]
            )
        )
        resolution_next = next(
            benchmark
            for benchmark in summary["next_benchmarks"]
            if benchmark["name"] == "Resolution forecasting"
        )
        self.assertIn("Current readiness", resolution_next["description"])

    def test_resolution_backtest_command_can_refresh_labels_first(self):
        stdout = io.StringIO()
        fake_backfill = {
            "event_count": 3,
            "labels_created": 4,
            "remote_refreshed": 2,
            "unresolved": 1,
        }

        with patch(
            "apps.web.management.commands.run_resolution_backtest.SnapshotIngestionService.backfill_resolution_labels",
            return_value=fake_backfill,
        ) as mocked_backfill:
            call_command(
                "run_resolution_backtest",
                "--no-persist",
                "--min-train-size",
                "4",
                "--refresh-labels",
                "--refresh-remote",
                "--limit-events",
                "3",
                stdout=stdout,
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["label_backfill"]["labels_created"], 4)
        mocked_backfill.assert_called_once()
