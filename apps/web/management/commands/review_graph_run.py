from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.web.models import GraphRun
from apps.web.services import GraphWorkflowService


class Command(BaseCommand):
    help = "Review a saved ChaosWing graph run with the configured agent or deterministic fallback."

    def add_arguments(self, parser):
        parser.add_argument("run_id", help="Saved GraphRun UUID to review.")

    def handle(self, *args, **options):
        run_id = str(options["run_id"] or "").strip()
        if not run_id:
            raise CommandError("A GraphRun UUID is required.")

        try:
            run = GraphRun.objects.get(pk=run_id)
        except GraphRun.DoesNotExist as exc:
            raise CommandError(f"Run {run_id} does not exist.") from exc

        review = GraphWorkflowService().review_saved_run(run)
        summary = {
            "run_id": str(run.id),
            "mode": run.mode,
            "model_name": run.model_name,
            "review": review,
        }
        self.stdout.write(json.dumps(summary, indent=2))
