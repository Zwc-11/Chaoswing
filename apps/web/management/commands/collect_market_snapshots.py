from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.snapshot_ingestion import SnapshotIngestionService


class Command(BaseCommand):
    help = "Collect one batch of live Polymarket market snapshots into persisted MarketSnapshot rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "urls",
            nargs="*",
            help="One or more Polymarket event URLs. If omitted, featured watchlists are used.",
        )
        parser.add_argument(
            "--include-watchlists",
            action="store_true",
            help="Include featured watchlist URLs even when explicit URLs are supplied.",
        )
        parser.add_argument(
            "--recent-run-limit",
            type=int,
            default=0,
            help="Also collect from the most recent saved graph-run source URLs.",
        )
        parser.add_argument(
            "--trending-limit",
            type=int,
            default=0,
            help="Also collect from the highest-volume active Polymarket events.",
        )

    def handle(self, *args, **options):
        explicit_urls = list(options["urls"] or [])
        include_watchlists = bool(options["include_watchlists"]) or not explicit_urls
        service = SnapshotIngestionService()
        urls = service.build_source_urls(
            explicit_urls=explicit_urls,
            include_watchlists=include_watchlists,
            recent_run_limit=max(int(options["recent_run_limit"]), 0),
            trending_limit=max(int(options["trending_limit"]), 0),
        )
        result = service.collect_urls(urls, collector="collect_market_snapshots")
        payload = result.as_dict()
        payload["source_mode"] = {
            "explicit_urls": bool(explicit_urls),
            "include_watchlists": include_watchlists,
            "recent_run_limit": max(int(options["recent_run_limit"]), 0),
            "trending_limit": max(int(options["trending_limit"]), 0),
        }
        self.stdout.write(json.dumps(payload, indent=2))
