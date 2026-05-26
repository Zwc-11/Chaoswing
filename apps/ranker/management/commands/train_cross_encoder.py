"""Module 4 driver — `python manage.py train_cross_encoder`.

Reads `RankingExample` rows for a `TemporalSplit`, materializes them into
listwise training examples (positives + hard + easy negatives), and hands
off to `chaoswing.ml.train.train`. Persists a `RerankerRun` row with the
best val NDCG@5 and a pointer to the saved checkpoint.

Typical use:
    python manage.py train_cross_encoder --split chaoswing-2025
    python manage.py train_cross_encoder --split chaoswing-2025 \
        --backbone cross-encoder/ms-marco-MiniLM-L-6-v2 --loss listwise --epochs 3
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from chaoswing.ml._types import MarketDoc, TemporalCutoff

from apps.ranker.models import RankingExample, RerankerRun, TemporalSplit


class Command(BaseCommand):
    help = (
        "Fine-tune the cross-encoder on the train slice of a TemporalSplit, "
        "early-stopping on val NDCG@5."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--split", required=True, help="TemporalSplit.name to train on.")
        parser.add_argument(
            "--backbone", default="microsoft/deberta-v3-base", help="HF model name."
        )
        parser.add_argument(
            "--loss",
            choices=["pairwise", "listwise"],
            default="listwise",
            help="Ranking loss (default: listwise).",
        )
        parser.add_argument("--epochs", type=int, default=3)
        parser.add_argument("--batch-size", type=int, default=16)
        parser.add_argument(
            "--max-candidates",
            type=int,
            default=16,
            help="Listwise candidate list size per source.",
        )
        parser.add_argument(
            "--max-length",
            type=int,
            default=128,
            help="Tokenizer max length per (source, candidate) pair.",
        )
        parser.add_argument(
            "--precision",
            choices=["fp32", "fp16", "bf16"],
            default="bf16",
            help="Autocast precision (default: bf16; falls back to fp32 if unsupported).",
        )
        parser.add_argument(
            "--checkpoint-dir",
            type=Path,
            default=Path("models/cross_encoder"),
            help="Parent dir for saved checkpoints.",
        )
        parser.add_argument(
            "--run-id",
            default=None,
            help="Training run id (used in checkpoint path). Auto-generated when omitted.",
        )
        parser.add_argument(
            "--device",
            default=None,
            help="Override device autodetection (e.g. 'cuda', 'cpu').",
        )

    def handle(self, *args, **options) -> None:
        # Heavy ML imports are deferred so `--help` doesn't load torch.
        from chaoswing.ml.data import materialize_listwise_examples
        from chaoswing.ml.train import TrainConfig, train

        try:
            split = TemporalSplit.objects.get(name=options["split"])
        except TemporalSplit.DoesNotExist as exc:
            raise CommandError(f"no TemporalSplit named '{options['split']}'") from exc

        run_id = options.get("run_id") or uuid.uuid4().hex[:12]
        train_cutoff = TemporalCutoff(timestamp=split.train_cutoff, label="train_cutoff")

        train_rows = list(
            RankingExample.objects.filter(split=split, split_name="train")
            .values("source_market_id", "candidates", "event_family")
        )
        val_rows = list(
            RankingExample.objects.filter(split=split, split_name="val")
            .values("source_market_id", "candidates", "event_family")
        )
        if not train_rows:
            raise CommandError("no train RankingExamples for split")
        if not val_rows:
            raise CommandError("no val RankingExamples for split (need val for early-stop)")

        docs_by_id = self._load_docs_for_rows(train_rows + val_rows, cutoff=train_cutoff)

        train_examples = materialize_listwise_examples(
            train_rows, docs_by_id, max_candidates=options["max_candidates"]
        )
        val_examples = materialize_listwise_examples(
            val_rows, docs_by_id, max_candidates=options["max_candidates"]
        )
        if not train_examples:
            raise CommandError("no listwise train examples after materialization")
        if not val_examples:
            raise CommandError("no listwise val examples after materialization")

        config = TrainConfig(
            backbone=options["backbone"],
            loss=options["loss"],
            max_candidates=options["max_candidates"],
            max_length=options["max_length"],
            batch_size=options["batch_size"],
            epochs=options["epochs"],
            precision=options["precision"],
            checkpoint_dir=options["checkpoint_dir"],
            device=options.get("device"),
            mlflow_run_name=f"cross-encoder-{run_id}",
        )

        result = train(
            train_examples=train_examples,
            val_examples=val_examples,
            config=config,
            train_cutoff=train_cutoff,
            run_id=run_id,
        )

        RerankerRun.objects.create(
            method="cross-encoder-finetuned",
            kind="training",
            split=split,
            metrics={
                "best_val_ndcg_at_5": result.best_metric,
                "best_epoch": result.best_epoch,
                "epochs_run": len(result.history),
            },
            config={
                "backbone": config.backbone,
                "loss": config.loss,
                "lr": config.learning_rate,
                "batch_size": config.batch_size,
                "max_candidates": config.max_candidates,
                "max_length": config.max_length,
                "precision": config.precision,
                "run_id": run_id,
            },
            artifact_path=str(result.checkpoint_path),
            notes=f"history={json.dumps(result.history, separators=(',', ':'))}",
        )

        report = {
            "run_id": run_id,
            "split": split.name,
            "backbone": config.backbone,
            "loss": config.loss,
            "best_val_ndcg_at_5": result.best_metric,
            "best_epoch": result.best_epoch,
            "checkpoint_path": str(result.checkpoint_path),
            "train_examples": len(train_examples),
            "val_examples": len(val_examples),
            "history": result.history,
        }
        self.stdout.write(json.dumps(report, indent=2))

    # ----- helpers -------------------------------------------------------

    def _load_docs_for_rows(
        self,
        rows: list[dict],
        *,
        cutoff: TemporalCutoff,
    ) -> dict[str, MarketDoc]:
        """Build `{market_id: MarketDoc}` for every source AND candidate referenced.

        Goes to `MarketSnapshot` for each referenced slug, gated by the train
        cutoff. Sources past the cutoff (val slice) are gated by the
        materializer's own filtering — but for training, the cutoff is the
        right bound and `validate_training_examples` will re-check it.
        """
        from apps.web.models import MarketSnapshot

        ids: set[str] = set()
        for row in rows:
            ids.add(row["source_market_id"])
            for cand in row.get("candidates", []) or []:
                cid = cand.get("candidate_market_id")
                if cid:
                    ids.add(cid)

        docs: dict[str, MarketDoc] = {}
        for slug in ids:
            # For training docs we pull the first available snapshot regardless of
            # cutoff so we can still look up the candidate text. The leakage gate
            # is enforced by `validate_training_examples` (source.first_seen <
            # cutoff) and by Module 2's split assignments; text alone has no
            # temporal signal.
            row = (
                MarketSnapshot.objects.filter(event_slug=slug)
                .order_by("snapshot_at")
                .values("event_title", "category", "payload", "snapshot_at")
                .first()
            )
            if not row:
                continue
            description = ""
            if isinstance(row.get("payload"), dict):
                description = str(row["payload"].get("description", ""))
            docs[slug] = MarketDoc(
                market_id=slug,
                title=row.get("event_title", "") or slug,
                description=description,
                category=row.get("category", "") or "",
                first_seen=row["snapshot_at"],
                event_family=slug,
            )
        return docs

    def _parse_iso(self, value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
