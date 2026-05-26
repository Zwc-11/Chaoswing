"""Module 2 driver — `python manage.py build_temporal_splits`.

Reads the mined-label JSONL, computes train/val/test assignments
deduplicated by `event_family`, and persists a `TemporalSplit` row plus
`RankingExample` rows per source market.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from chaoswing.ml._types import TemporalCutoff
from chaoswing.ml.splits import TemporalSplitConfig, compute_splits, group_records_by_split

from apps.ranker.models import RankingExample, TemporalSplit
from apps.ranker.services._schemas import iter_relevance_records


DEFAULT_LABELS_PATH = Path("ml_data/relevance_labels.jsonl")


class Command(BaseCommand):
    help = (
        "Compute temporal train/val/test splits from a mined-labels JSONL, "
        "deduplicated by event_family."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--name", required=True, help="Split name (unique).")
        parser.add_argument("--train-cutoff", required=True, help="ISO timestamp.")
        parser.add_argument("--val-cutoff", required=True, help="ISO timestamp.")
        parser.add_argument(
            "--labels",
            type=Path,
            default=DEFAULT_LABELS_PATH,
            help=f"Path to relevance_labels.jsonl (default: {DEFAULT_LABELS_PATH}).",
        )
        parser.add_argument(
            "--mining-run-id",
            default="",
            help="Optional mining_run_id to record alongside the split.",
        )
        parser.add_argument(
            "--no-examples",
            action="store_true",
            help="Skip materializing RankingExample rows.",
        )

    def handle(self, *args, **options) -> None:
        labels_path: Path = options["labels"]
        if not labels_path.exists():
            raise CommandError(f"labels file not found: {labels_path}")
        train_cutoff = TemporalCutoff(
            timestamp=self._parse_iso(options["train_cutoff"]),
            label="train_cutoff",
        )
        val_cutoff = TemporalCutoff(
            timestamp=self._parse_iso(options["val_cutoff"]),
            label="val_cutoff",
        )
        config = TemporalSplitConfig(train_cutoff=train_cutoff, val_cutoff=val_cutoff)

        with labels_path.open("r", encoding="utf-8") as handle:
            records = list(iter_relevance_records(handle))

        assignment = compute_splits(records, config)
        grouped = group_records_by_split(records, assignment)

        split = self._persist(
            name=options["name"],
            train_cutoff=train_cutoff,
            val_cutoff=val_cutoff,
            assignment=assignment,
            grouped=grouped,
            mining_run_id=options["mining_run_id"],
            persist_examples=not options["no_examples"],
        )

        report = {
            "name": split.name,
            "train_cutoff": train_cutoff.timestamp.isoformat(),
            "val_cutoff": val_cutoff.timestamp.isoformat(),
            "counts": assignment.counts,
            "families": len(assignment.family_to_split),
            "examples_persisted": (not options["no_examples"]),
        }
        self.stdout.write(json.dumps(report, indent=2))

    @transaction.atomic
    def _persist(
        self,
        *,
        name: str,
        train_cutoff: TemporalCutoff,
        val_cutoff: TemporalCutoff,
        assignment,
        grouped,
        mining_run_id: str,
        persist_examples: bool,
    ) -> TemporalSplit:
        TemporalSplit.objects.filter(name=name).delete()
        split = TemporalSplit.objects.create(
            name=name,
            train_cutoff=train_cutoff.timestamp,
            val_cutoff=val_cutoff.timestamp,
            mining_run_id=mining_run_id,
            family_assignments=assignment.family_to_split,
            counts=assignment.counts,
        )
        if not persist_examples:
            return split
        for split_name, records in grouped.items():
            by_source: dict[str, list[dict]] = defaultdict(list)
            family_by_source: dict[str, str] = {}
            for record in records:
                by_source[record.source_id].append(
                    {
                        "candidate_market_id": record.candidate_id,
                        "relevance": int(record.relevance),
                        "negative_kind": record.negative_kind or "",
                    }
                )
                family_by_source.setdefault(record.source_id, record.event_family)
            example_rows = [
                RankingExample(
                    split=split,
                    split_name=split_name,
                    source_market_id=source_id,
                    event_family=family_by_source.get(source_id, ""),
                    candidates=candidates,
                )
                for source_id, candidates in by_source.items()
            ]
            RankingExample.objects.bulk_create(example_rows, batch_size=500)
        return split

    def _parse_iso(self, value: str) -> datetime:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CommandError(f"not a valid ISO timestamp: {value}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
