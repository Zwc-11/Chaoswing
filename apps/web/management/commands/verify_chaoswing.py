from __future__ import annotations

import json

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.test.utils import override_settings

from apps.web.mock_graph import SAMPLE_POLYMARKET_LINKS
from apps.web.services import GraphWorkflowService


class Command(BaseCommand):
    help = "Run Django checks, tests, and a backend workflow smoke run."

    def handle(self, *args, **options):
        self.stdout.write("Running Django checks...")
        call_command("check")

        self.stdout.write("Running Django tests...")
        call_command("test", "tests")

        self.stdout.write("Running graph workflow smoke test...")
        with override_settings(CHAOSWING_ENABLE_REMOTE_FETCH=False, CHAOSWING_ENABLE_LLM=False):
            payload = GraphWorkflowService().run(SAMPLE_POLYMARKET_LINKS[0]["url"])
        summary = {
            "run_id": payload["run"]["id"],
            "mode": payload["run"]["mode"],
            "graph_stats": payload["run"].get("graph_stats", {}),
            "quality_score": payload["run"].get("review", {}).get("quality_score"),
        }
        self.stdout.write(json.dumps(summary, indent=2))
