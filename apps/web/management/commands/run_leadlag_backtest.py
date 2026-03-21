from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.leadlag import LeadLagBacktestService


class Command(BaseCommand):
    help = "Run the cross-venue lead-lag paper-trading backtest."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Compute the report without creating an ExperimentRun row.",
        )

    def handle(self, *args, **options):
        report = LeadLagBacktestService().run(persist=not options["no_persist"])
        self.stdout.write(json.dumps(report, indent=2))
