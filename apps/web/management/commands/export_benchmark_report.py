from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.market_intelligence import BenchmarkSummaryService


class Command(BaseCommand):
    help = "Export the current ChaosWing benchmark snapshot as JSON."

    def add_arguments(self, parser):
        parser.add_argument(
            "--pretty",
            action="store_true",
            help="Pretty-print the benchmark JSON output.",
        )

    def handle(self, *args, **options):
        summary = BenchmarkSummaryService().build()
        indent = 2 if options["pretty"] else None
        self.stdout.write(json.dumps(summary, indent=indent))
