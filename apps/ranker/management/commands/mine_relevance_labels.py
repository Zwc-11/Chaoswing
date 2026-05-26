"""Module 1 driver — `python manage.py mine_relevance_labels --cutoff <iso>`.

Mines 0-3 graded relevance labels from lead-lag co-movement strictly before
`--cutoff`, writes `ml_data/relevance_labels.jsonl`, and (unless
`--no-persist`) stores them as `RelevanceLabel` rows.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from chaoswing.ml._types import TemporalCutoff
from chaoswing.ml.leakage import audit_records

from apps.ranker.services.label_mining import LabelMiner, LabelMinerConfig


DEFAULT_OUTPUT = Path("ml_data/relevance_labels.jsonl")


class Command(BaseCommand):
    help = (
        "Mine 0-3 graded relevance labels from historical implied-probability "
        "co-movement strictly before --cutoff."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--cutoff",
            required=True,
            help="ISO-8601 timestamp; only data strictly before this is used.",
        )
        parser.add_argument(
            "--output",
            type=Path,
            default=DEFAULT_OUTPUT,
            help=f"JSONL output path (default: {DEFAULT_OUTPUT}).",
        )
        parser.add_argument(
            "--run-id",
            default=None,
            help="Mining run id used as the persistence key. Auto-generated when omitted.",
        )
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Skip writing RelevanceLabel rows; only emit the JSONL.",
        )
        parser.add_argument(
            "--grid-minutes",
            type=int,
            default=5,
            help="Resample grid step in minutes (default: 5).",
        )
        parser.add_argument(
            "--max-lag-steps",
            type=int,
            default=12,
            help="Lag window explored on each side, in grid steps (default: 12).",
        )

    def handle(self, *args, **options) -> None:
        cutoff_dt = self._parse_cutoff(options["cutoff"])
        cutoff = TemporalCutoff(timestamp=cutoff_dt, label="mining_cutoff")
        config = LabelMinerConfig(
            grid_freq=timedelta(minutes=options["grid_minutes"]),
            max_lag_steps=options["max_lag_steps"],
        )
        miner = LabelMiner(cutoff=cutoff, config=config, run_id=options.get("run_id"))
        records = miner.mine()
        audit_records(records, cutoff)  # belt-and-suspenders: enforce the rule once more
        written = miner.write_jsonl(records, options["output"])
        persisted = 0
        if not options["no_persist"]:
            persisted = miner.persist(records)
        report = {
            "cutoff": cutoff.timestamp.isoformat(),
            "run_id": miner.run_id,
            "records": len(records),
            "jsonl_path": str(options["output"]),
            "jsonl_lines": written,
            "persisted_rows": persisted,
        }
        self.stdout.write(json.dumps(report, indent=2))

    def _parse_cutoff(self, value: str) -> datetime:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CommandError(f"--cutoff is not a valid ISO timestamp: {value}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
