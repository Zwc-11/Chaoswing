from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.agent_trace_backfill import AgentTraceBackfillService


class Command(BaseCommand):
    help = (
        "Reconstruct planner/retriever/graph-editor/critic/verifier stage traces for older "
        "GraphRun records, repair missing telemetry metadata on persisted traces, and "
        "optionally refresh payload agent_pipeline metadata."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Only backfill the most recent N runs.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Replace existing required stage traces instead of only filling missing stages.",
        )
        parser.add_argument(
            "--skip-payload-update",
            action="store_true",
            help="Do not write reconstructed agent_pipeline metadata back into GraphRun.payload.",
        )

    def handle(self, *args, **options):
        report = AgentTraceBackfillService().run(
            limit=options["limit"] or None,
            overwrite=options["overwrite"],
            update_payload=not options["skip_payload_update"],
        )
        self.stdout.write(json.dumps(report, indent=2))
