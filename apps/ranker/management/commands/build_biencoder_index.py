"""Module 3 driver — `python manage.py build_biencoder_index`.

Builds the stage-1 FAISS (or numpy fallback) index over markets whose
`first_seen < cutoff`, then optionally evaluates Recall@100 of label>=2
positives on a labelled split.

Typical flow:
    # Build the index used to mine hard negatives + serve stage-1:
    python manage.py build_biencoder_index --cutoff 2025-06-01T00:00:00Z

    # Tune on val:
    python manage.py build_biencoder_index --cutoff 2025-06-01T00:00:00Z \
        --eval-split chaoswing-2025 --eval-on val

    # Headline number on test:
    python manage.py build_biencoder_index --cutoff 2025-06-01T00:00:00Z \
        --eval-split chaoswing-2025 --eval-on test

Ranker.md §"Don't" forbids tuning on test, so the default eval split is `val`.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from chaoswing.ml._types import MarketDoc, TemporalCutoff
from chaoswing.ml.leakage import LeakageError

from apps.ranker.models import RankingExample, RerankerRun, TemporalSplit
from apps.ranker.services.biencoder import (
    BiEncoderIndexBuilder,
    BiEncoderIndexConfig,
    BiEncoderReranker,
    SentenceTransformerEncoder,
    evaluate_recall_at_k,
)
from apps.ranker.services._repository import DjangoSnapshotRepository


DEFAULT_INDEX_DIR = Path("models/biencoder")


class Command(BaseCommand):
    help = (
        "Build the bi-encoder FAISS index over pre-cutoff markets, and "
        "optionally evaluate Recall@100 on a labelled split."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--cutoff",
            required=True,
            help="ISO-8601 timestamp; index only markets with first_seen < cutoff.",
        )
        parser.add_argument(
            "--model",
            default="BAAI/bge-small-en-v1.5",
            help="SentenceTransformers model name.",
        )
        parser.add_argument(
            "--index-dir",
            type=Path,
            default=DEFAULT_INDEX_DIR,
            help=f"Directory for index artifacts (default: {DEFAULT_INDEX_DIR}).",
        )
        parser.add_argument(
            "--run-id",
            default=None,
            help="Index run id (used in the artifact filename). Auto-generated when omitted.",
        )
        parser.add_argument(
            "--no-faiss",
            action="store_true",
            help="Force the numpy backend even when faiss is available.",
        )
        parser.add_argument(
            "--eval-split",
            default=None,
            help="If set, evaluate Recall@100 against this TemporalSplit.name.",
        )
        parser.add_argument(
            "--eval-on",
            default="val",
            choices=["val", "test"],
            help="Which split slice to evaluate on. Defaults to val (per Ranker.md).",
        )
        parser.add_argument(
            "--top-k",
            type=int,
            default=100,
            help="Retrieval depth for the Recall@k metric (default: 100).",
        )

    def handle(self, *args, **options) -> None:
        cutoff = TemporalCutoff(
            timestamp=self._parse_iso(options["cutoff"]),
            label="index_cutoff",
        )
        run_id = options.get("run_id") or uuid.uuid4().hex[:12]
        config = BiEncoderIndexConfig(
            model_name=options["model"],
            prefer_faiss=not options["no_faiss"],
        )
        repository = DjangoSnapshotRepository()
        encoder = SentenceTransformerEncoder(config.model_name)
        builder = BiEncoderIndexBuilder(encoder=encoder, repository=repository, config=config)

        index_path = options["index_dir"] / f"biencoder_{run_id}"
        handle = builder.build_and_persist(cutoff, path=index_path)

        report: dict = {
            "run_id": run_id,
            "cutoff": cutoff.timestamp.isoformat(),
            "model": config.model_name,
            "backend": handle.backend_name,
            "size": handle.size,
            "index_path": str(handle.index_path),
            "meta_path": str(handle.meta_path),
        }

        eval_split_name = options.get("eval_split")
        if eval_split_name:
            recall_report = self._evaluate(
                handle=handle,
                encoder=encoder,
                repository=repository,
                split_name=eval_split_name,
                split_slice=options["eval_on"],
                top_k=options["top_k"],
                run_id=run_id,
            )
            report["eval"] = recall_report

        self.stdout.write(json.dumps(report, indent=2))

    # ----- evaluation -----------------------------------------------------

    def _evaluate(
        self,
        *,
        handle,
        encoder,
        repository: DjangoSnapshotRepository,
        split_name: str,
        split_slice: str,
        top_k: int,
        run_id: str,
    ) -> dict:
        try:
            split = TemporalSplit.objects.get(name=split_name)
        except TemporalSplit.DoesNotExist as exc:
            raise CommandError(f"no TemporalSplit named '{split_name}'") from exc

        # Query cutoff is the cutoff at which the *evaluated source* first appears.
        # For val we use train_cutoff (val sources first_seen >= train_cutoff);
        # for test we use val_cutoff (test sources first_seen >= val_cutoff).
        # The index's build cutoff must be <= this, which we enforce by routing
        # both through the leakage helper inside `BiEncoderReranker.retrieve`.
        eval_cutoff_ts = split.train_cutoff if split_slice == "val" else split.val_cutoff
        eval_cutoff = TemporalCutoff(
            timestamp=eval_cutoff_ts,
            label=f"{split_slice}_eval_cutoff",
        )
        if handle.cutoff.timestamp > eval_cutoff.timestamp:
            raise LeakageError(
                f"index built at {handle.cutoff.timestamp.isoformat()} is past "
                f"the {split_slice} evaluation cutoff {eval_cutoff.timestamp.isoformat()}"
            )

        examples = list(
            RankingExample.objects.filter(split=split, split_name=split_slice)
            .values("source_market_id", "candidates", "event_family")
        )
        docs_by_id = self._docs_for_examples(repository, examples, handle, eval_cutoff)

        reranker = BiEncoderReranker(encoder=encoder, index=handle, model_name=handle.model_name)
        report = evaluate_recall_at_k(
            reranker,
            examples=examples,
            docs_by_id=docs_by_id,
            cutoff=eval_cutoff,
            k=top_k,
        )

        out_dir = Path("ml_data")
        out_dir.mkdir(parents=True, exist_ok=True)
        per_source_path = out_dir / f"biencoder_recall_{run_id}_{split_slice}.jsonl"
        with per_source_path.open("w", encoding="utf-8") as fh:
            for row in report.per_source:
                fh.write(json.dumps(row, separators=(",", ":")))
                fh.write("\n")

        RerankerRun.objects.create(
            method="biencoder-cosine",
            kind="inference",
            split=split,
            metrics={
                "recall_at_k": report.recall_at_100,
                "p95_latency_ms": report.p95_latency_ms,
                "k": top_k,
                "n_sources": report.n_sources,
                "slice": split_slice,
            },
            config={
                "model": handle.model_name,
                "backend": handle.backend_name,
                "index_run_id": run_id,
            },
            artifact_path=str(handle.index_path),
        )

        return {
            "split": split_name,
            "slice": split_slice,
            "n_sources": report.n_sources,
            "recall_at_k": report.recall_at_100,
            "k": top_k,
            "p95_latency_ms": report.p95_latency_ms,
            "per_source_path": str(per_source_path),
        }

    def _docs_for_examples(
        self,
        repository: DjangoSnapshotRepository,
        examples: list[dict],
        handle,
        eval_cutoff: TemporalCutoff,
    ) -> dict[str, MarketDoc]:
        """Build a `{market_id: MarketDoc}` map covering every source in the
        evaluation set. Sources live past the index cutoff, so we go straight
        to `MarketSnapshot` for them, gated by the eval cutoff.
        """
        from apps.web.models import MarketSnapshot

        source_ids = {ex["source_market_id"] for ex in examples}
        docs: dict[str, MarketDoc] = {}
        for slug in source_ids:
            row = (
                MarketSnapshot.objects.filter(
                    event_slug=slug, snapshot_at__lt=eval_cutoff.timestamp
                )
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

    # ----- helpers --------------------------------------------------------

    def _parse_iso(self, value: str) -> datetime:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CommandError(f"not a valid ISO timestamp: {value}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
