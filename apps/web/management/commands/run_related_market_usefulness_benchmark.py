from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.market_intelligence import RelatedMarketUsefulnessBenchmarkService


class Command(BaseCommand):
    help = (
        "Run the human-labeled related-market usefulness benchmark and optionally persist it "
        "as an ExperimentRun."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Compute the report without creating an ExperimentRun row.",
        )
        parser.add_argument(
            "--min-judged-candidates",
            type=int,
            default=3,
            help="Minimum number of judged candidates required per run before evaluation.",
        )
        parser.add_argument(
            "--min-reviewers-per-candidate",
            type=int,
            default=1,
            help="Minimum number of reviewer labels required before a candidate enters the benchmark.",
        )

    def handle(self, *args, **options):
        service = RelatedMarketUsefulnessBenchmarkService(
            min_judged_candidates=max(int(options["min_judged_candidates"]), 2),
            min_reviewers_per_candidate=max(int(options["min_reviewers_per_candidate"]), 1),
        )
        report = service.run(persist=not options["no_persist"])
        self.stdout.write(json.dumps(report, indent=2))
