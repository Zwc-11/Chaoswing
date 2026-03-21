from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.market_intelligence import AgentTrustBenchmarkService


class Command(BaseCommand):
    help = (
        "Run the deterministic agent trust benchmark over saved graphs and staged traces, "
        "and optionally persist it as an ExperimentRun."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Compute the report without creating an ExperimentRun row.",
        )

    def handle(self, *args, **options):
        report = AgentTrustBenchmarkService().run(persist=not options["no_persist"])
        self.stdout.write(json.dumps(report, indent=2))
