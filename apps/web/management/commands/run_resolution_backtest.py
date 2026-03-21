from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.market_intelligence import ResolutionForecastService
from apps.web.services.snapshot_ingestion import SnapshotIngestionService


class Command(BaseCommand):
    help = "Run the YES/NO resolution forecasting rolling backtest and optionally persist it as an ExperimentRun."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Compute the report without creating an ExperimentRun row.",
        )
        parser.add_argument(
            "--min-train-size",
            type=int,
            default=8,
            help="Minimum number of earlier labeled snapshots required before evaluation begins.",
        )
        parser.add_argument(
            "--refresh-labels",
            action="store_true",
            help="Backfill resolution labels across saved snapshot histories before running the backtest.",
        )
        parser.add_argument(
            "--refresh-remote",
            action="store_true",
            help="When refreshing labels, revisit unresolved events through Polymarket before scoring.",
        )
        parser.add_argument(
            "--limit-events",
            type=int,
            default=0,
            help="When refreshing labels, only inspect the most recent N unresolved event families.",
        )

    def handle(self, *args, **options):
        label_backfill = None
        if options["refresh_labels"]:
            label_backfill = SnapshotIngestionService().backfill_resolution_labels(
                refresh_remote=options["refresh_remote"],
                limit_events=max(int(options["limit_events"]), 0),
                collector="run_resolution_backtest",
            )
        service = ResolutionForecastService(min_train_size=max(int(options["min_train_size"]), 2))
        report = service.run(persist=not options["no_persist"])
        if label_backfill is not None:
            report["label_backfill"] = label_backfill
        self.stdout.write(json.dumps(report, indent=2))
