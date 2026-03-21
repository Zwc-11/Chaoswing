from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.market_intelligence import RelatedMarketRankingBenchmarkService


class Command(BaseCommand):
    help = (
        "Run the silver-label related-market ranking benchmark and optionally persist it as an "
        "ExperimentRun."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Compute the report without creating an ExperimentRun row.",
        )
        parser.add_argument(
            "--hard-negative-pool-size",
            type=int,
            default=12,
            help="Number of high-overlap non-positive candidates to include per ranking example.",
        )

    def handle(self, *args, **options):
        service = RelatedMarketRankingBenchmarkService(
            hard_negative_pool_size=max(int(options["hard_negative_pool_size"]), 2)
        )
        report = service.run(persist=not options["no_persist"])
        self.stdout.write(json.dumps(report, indent=2))
