from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.web.services import GraphWorkflowService


class Command(BaseCommand):
    help = "Run the ChaosWing backend workflow for a single Polymarket event URL."

    def add_arguments(self, parser):
        parser.add_argument("url", help="Polymarket event URL to analyze.")

    def handle(self, *args, **options):
        source_url = options["url"].strip()
        if not source_url:
            raise CommandError("A Polymarket event URL is required.")

        payload = GraphWorkflowService().run(source_url)
        summary = {
            "run_id": payload["run"]["id"],
            "mode": payload["run"]["mode"],
            "model_name": payload["run"].get("model_name", ""),
            "graph_stats": payload["run"].get("graph_stats", {}),
            "review": payload["run"].get("review", {}),
            "source_url": payload["event"]["source_url"],
        }
        self.stdout.write(json.dumps(summary, indent=2))
