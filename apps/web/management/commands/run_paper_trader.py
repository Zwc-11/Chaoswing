from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.leadlag import LeadLagSignalService, PaperTradingService


class Command(BaseCommand):
    help = "Generate current lead-lag signals and update the paper-trade ledger."

    def add_arguments(self, parser):
        parser.add_argument(
            "--horizon-seconds",
            type=int,
            default=None,
            help="Optional override for the paper-trade exit horizon.",
        )
        parser.add_argument(
            "--pair-limit",
            type=int,
            default=None,
            help="Optional limit on the number of trade-eligible pairs to scan.",
        )
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Scan and simulate without writing LeadLagSignal or PaperTrade rows.",
        )

    def handle(self, *args, **options):
        persist = not options["no_persist"]
        signal_report = LeadLagSignalService().scan(
            persist=persist,
            pair_limit=options["pair_limit"],
        )
        trade_report = PaperTradingService().run(
            persist=persist,
            horizon_seconds=options["horizon_seconds"],
        )
        self.stdout.write(
            json.dumps(
                {
                    "signals": signal_report,
                    "paper_trades": trade_report,
                },
                indent=2,
            )
        )
