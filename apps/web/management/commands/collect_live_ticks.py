from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.web.services.leadlag import LeadLagTickCollectionService


class Command(BaseCommand):
    help = "Collect live cross-venue research ticks for Polymarket and Kalshi."

    def add_arguments(self, parser):
        parser.add_argument(
            "--venues",
            nargs="*",
            default=["polymarket", "kalshi"],
            help="Venue list to collect from.",
        )
        parser.add_argument(
            "--market-limit",
            type=int,
            default=10,
            help="Maximum active markets per venue to poll.",
        )
        parser.add_argument(
            "--iterations",
            type=int,
            default=1,
            help="How many polling loops to run.",
        )
        parser.add_argument(
            "--poll-seconds",
            type=int,
            default=None,
            help="Pause between polling loops. Defaults to CHAOSWING_LEADLAG_POLL_SECONDS.",
        )
        parser.add_argument(
            "--active-pairs-only",
            action="store_true",
            help="Poll only markets already referenced by the active lead-lag pair registry.",
        )
        parser.add_argument(
            "--fixture-path",
            help="Optional JSONL fixture file of normalized ticks for offline ingestion/tests.",
        )
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Collect ticks without writing MarketEventTick rows.",
        )

    def handle(self, *args, **options):
        fixture_path = options["fixture_path"]
        report = LeadLagTickCollectionService().collect(
            venues=list(options["venues"] or []),
            market_limit=max(int(options["market_limit"]), 1),
            iterations=max(int(options["iterations"]), 1),
            poll_seconds=options["poll_seconds"],
            active_pairs_only=options["active_pairs_only"],
            fixture_path=Path(fixture_path) if fixture_path else None,
            persist=not options["no_persist"],
        )
        self.stdout.write(json.dumps(report, indent=2))
