from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.leadlag import LeadLagPairBuilderService


class Command(BaseCommand):
    help = "Build the cross-venue lead-lag pair registry from normalized market mappings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Score pairs without writing LeadLagPair rows.",
        )

    def handle(self, *args, **options):
        report = LeadLagPairBuilderService().build(persist=not options["no_persist"])
        self.stdout.write(json.dumps(report, indent=2))
