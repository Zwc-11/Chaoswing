from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.snapshot_ingestion import SnapshotIngestionService


class Command(BaseCommand):
    help = "Continuously collect live Polymarket snapshots from watchlists, recent runs, trending markets, or explicit URLs."

    def add_arguments(self, parser):
        parser.add_argument(
            "urls",
            nargs="*",
            help="Optional Polymarket event URLs to include in the live collection pool.",
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
        parser.add_argument(
            "--poll-seconds",
            type=int,
            default=300,
            help="Pause between collection cycles in seconds.",
        )
        parser.add_argument(
            "--iterations",
            type=int,
            default=0,
            help="Number of cycles to run. Use 0 to run until interrupted.",
        )

    def handle(self, *args, **options):
        explicit_urls = list(options["urls"] or [])
        include_watchlists = bool(options["include_watchlists"]) or not explicit_urls
        service = SnapshotIngestionService()

        for iteration_index, result in enumerate(
            service.iter_live_loop(
                explicit_urls=explicit_urls,
                include_watchlists=include_watchlists,
                recent_run_limit=max(int(options["recent_run_limit"]), 0),
                trending_limit=max(int(options["trending_limit"]), 0),
                poll_seconds=max(int(options["poll_seconds"]), 0),
                iterations=int(options["iterations"]),
                collector="run_live_snapshot_collector",
            ),
            start=1,
        ):
            payload = result.as_dict()
            payload["iteration"] = iteration_index
            payload["source_mode"] = {
                "explicit_urls": bool(explicit_urls),
                "include_watchlists": include_watchlists,
                "recent_run_limit": max(int(options["recent_run_limit"]), 0),
                "trending_limit": max(int(options["trending_limit"]), 0),
                "poll_seconds": max(int(options["poll_seconds"]), 0),
                "iterations": int(options["iterations"]),
            }
            self.stdout.write(json.dumps(payload))
