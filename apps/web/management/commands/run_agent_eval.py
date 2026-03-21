from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.agent_trace_backfill import AgentTraceBackfillService
from apps.web.services.market_intelligence import AgentEvaluationService


class Command(BaseCommand):
    help = (
        "Summarize persisted agent traces, including instrumentation coverage, and optionally "
        "persist the result as an ExperimentRun."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Compute the report without creating an ExperimentRun row.",
        )
        parser.add_argument(
            "--backfill-missing",
            action="store_true",
            help="Backfill missing planner/retriever/graph-editor/critic/verifier traces before evaluation.",
        )
        parser.add_argument(
            "--overwrite-stage-traces",
            action="store_true",
            help="When backfilling, replace existing required stage traces instead of only filling gaps.",
        )

    def handle(self, *args, **options):
        backfill_report = None
        if options["backfill_missing"]:
            backfill_report = AgentTraceBackfillService().run(
                overwrite=options["overwrite_stage_traces"],
            )
        report = AgentEvaluationService().run(persist=not options["no_persist"])
        if backfill_report:
            report["backfill"] = backfill_report
        self.stdout.write(json.dumps(report, indent=2))
