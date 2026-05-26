"""Module 7 driver — `python manage.py run_forecasting_probe`.

Stub. For each resolved source market, builds two forecasts (baseline,
challenger) using only data strictly before each forecast timestamp, and
reports Brier / log loss / calibration lift. Lands last.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Forecasting probe (Module 7 — not yet implemented)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--split", required=True)
        parser.add_argument("--top-k", type=int, default=10)

    def handle(self, *args, **options) -> None:
        raise CommandError(
            "run_forecasting_probe is not implemented yet. It lands with Module 7."
        )
