from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.web.services.market_intelligence import DatasetBuilderService


class Command(BaseCommand):
    help = (
        "Export ChaosWing runs, snapshots, labels, traces, forecast and ranking examples, "
        "human judgments, and experiments to JSONL and DuckDB when available."
    )

    def handle(self, *args, **options):
        written = DatasetBuilderService().write_jsonl()
        self.stdout.write(json.dumps(written, indent=2))
