from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.leadlag import CrossVenueMarketMapService


class Command(BaseCommand):
    help = "Sync the normalized Polymarket and Kalshi market catalog used by lead-lag research."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit-per-venue",
            type=int,
            default=40,
            help="Maximum number of active markets to pull from each venue.",
        )
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Fetch and score the catalog without writing CrossVenueMarketMap rows.",
        )

    def handle(self, *args, **options):
        report = CrossVenueMarketMapService().sync(
            limit_per_venue=max(int(options["limit_per_venue"]), 1),
            persist=not options["no_persist"],
        )
        self.stdout.write(json.dumps(report, indent=2))
