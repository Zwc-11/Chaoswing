from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.snapshot_ingestion import SnapshotIngestionService


class Command(BaseCommand):
    help = (
        "Backfill ResolutionLabel rows across each event's snapshot history, optionally refreshing "
        "current market state from Polymarket for unresolved events."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--refresh-remote",
            action="store_true",
            help="Revisit unresolved events through the Polymarket metadata service before giving up.",
        )
        parser.add_argument(
            "--limit-events",
            type=int,
            default=0,
            help="Only inspect the most recent N unresolved event families.",
        )

    def handle(self, *args, **options):
        report = SnapshotIngestionService().backfill_resolution_labels(
            refresh_remote=options["refresh_remote"],
            limit_events=max(int(options["limit_events"]), 0),
            collector="label_resolved_markets",
        )
        self.stdout.write(json.dumps(report, indent=2))
