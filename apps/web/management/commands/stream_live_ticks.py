from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.leadlag_streaming import LeadLagStreamingCollectionService


class Command(BaseCommand):
    help = "Stream low-latency lead-lag ticks with websocket-first transport where supported."

    def add_arguments(self, parser):
        parser.add_argument(
            "--venues",
            nargs="*",
            default=["polymarket", "kalshi"],
            help="Venue list to collect from.",
        )
        parser.add_argument(
            "--market-limit",
            type=int,
            default=10,
            help="Maximum tracked markets per venue.",
        )
        parser.add_argument(
            "--duration-seconds",
            type=int,
            default=30,
            help="How long each stream session should stay open.",
        )
        parser.add_argument(
            "--iterations",
            type=int,
            default=1,
            help="How many stream sessions to run. Use 0 to keep reconnecting until stopped.",
        )
        parser.add_argument(
            "--reconnect-seconds",
            type=int,
            default=5,
            help="Pause between stream sessions or after a failure.",
        )
        parser.add_argument(
            "--transport",
            choices=["hybrid", "websocket", "poll"],
            default="hybrid",
            help="Use websocket-first hybrid collection, websocket-only, or polling only.",
        )
        parser.add_argument(
            "--poll-seconds",
            type=int,
            default=None,
            help="Pause between poll cycles when polling is active.",
        )
        parser.add_argument(
            "--active-pairs-only",
            action="store_true",
            help="Track only markets already referenced by the active lead-lag pair registry.",
        )
        parser.add_argument(
            "--rebuild-pairs-every",
            type=int,
            default=0,
            help="Rebuild the lead-lag pair registry every N completed stream sessions.",
        )
        parser.add_argument(
            "--scan-signals-every",
            type=int,
            default=0,
            help="Run signal evaluation every N completed stream sessions.",
        )
        parser.add_argument(
            "--run-paper-trader",
            action="store_true",
            help="Run the paper-trade ledger whenever signal scans are triggered.",
        )
        parser.add_argument(
            "--pair-limit",
            type=int,
            default=None,
            help="Optional limit passed to signal scanning when --scan-signals-every is active.",
        )
        parser.add_argument(
            "--horizon-seconds",
            type=int,
            default=None,
            help="Optional paper-trade exit horizon when --run-paper-trader is active.",
        )
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Collect ticks without writing MarketEventTick rows.",
        )

    def handle(self, *args, **options):
        service = LeadLagStreamingCollectionService()
        for report in service.iter_supervised_stream(
            venues=list(options["venues"] or []),
            market_limit=max(int(options["market_limit"]), 1),
            duration_seconds=max(int(options["duration_seconds"]), 1),
            active_pairs_only=options["active_pairs_only"],
            transport=str(options["transport"] or "hybrid"),
            poll_seconds=options["poll_seconds"],
            persist=not options["no_persist"],
            iterations=int(options["iterations"]),
            reconnect_seconds=max(int(options["reconnect_seconds"]), 0),
            rebuild_pairs_every=max(int(options["rebuild_pairs_every"]), 0),
            scan_signals_every=max(int(options["scan_signals_every"]), 0),
            run_paper_trader=options["run_paper_trader"],
            pair_limit=options["pair_limit"],
            horizon_seconds=options["horizon_seconds"],
        ):
            self.stdout.write(json.dumps(report, indent=2))
