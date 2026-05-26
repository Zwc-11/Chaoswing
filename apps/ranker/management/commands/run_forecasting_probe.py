"""Module 7 driver - `python manage.py run_forecasting_probe`."""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.ranker.models import TemporalSplit
from apps.ranker.services import baselines, biencoder, cross_encoder, listwise_llm
from apps.ranker.services.forecasting import ForecastingProbeService


class Command(BaseCommand):
    help = (
        "Compare source-only forecasts with forecasts augmented by a "
        "reranker's top-k related-market signals."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--split", required=True, help="TemporalSplit.name to evaluate.")
        parser.add_argument(
            "--method",
            default="cross-encoder-finetuned",
            choices=[
                "bm25",
                "lexical-overlap",
                "biencoder-cosine",
                "cross-encoder-finetuned",
                "rankgpt-listwise",
                "cohere-rerank",
            ],
            help="Reranker whose surfaced markets provide challenger signals.",
        )
        parser.add_argument("--top-k", type=int, default=10)
        parser.add_argument(
            "--min-train-size",
            type=int,
            default=8,
            help="Minimum earlier forecast examples before rolling evaluation.",
        )
        parser.add_argument(
            "--cross-encoder",
            type=Path,
            default=None,
            help="Checkpoint directory required for method=cross-encoder-finetuned.",
        )
        parser.add_argument(
            "--biencoder-index",
            type=Path,
            default=None,
            help="Index metadata path required for method=biencoder-cosine.",
        )
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Compute the probe without creating a RerankerRun row.",
        )

    def handle(self, *args, **options) -> None:
        try:
            split = TemporalSplit.objects.get(name=options["split"])
        except TemporalSplit.DoesNotExist as exc:
            raise CommandError(f"no TemporalSplit named '{options['split']}'") from exc
        method = options["method"]
        reranker = self._build_reranker(
            method=method,
            cross_encoder_dir=options["cross_encoder"],
            biencoder_index=options["biencoder_index"],
        )
        try:
            service = ForecastingProbeService(
                split=split,
                reranker=reranker,
                method=method,
                top_k=int(options["top_k"]),
                min_train_size=int(options["min_train_size"]),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        report = service.run(persist=not options["no_persist"])
        self.stdout.write(json.dumps(report, indent=2))

    def _build_reranker(
        self,
        *,
        method: str,
        cross_encoder_dir: Path | None,
        biencoder_index: Path | None,
    ):
        if method == "bm25":
            return baselines.BM25Reranker()
        if method == "lexical-overlap":
            return baselines.LexicalOverlapReranker()
        if method == "biencoder-cosine":
            if biencoder_index is None:
                raise CommandError("--biencoder-index is required for method=biencoder-cosine")
            return biencoder.BiEncoderReranker.from_index_path(biencoder_index)
        if method == "cross-encoder-finetuned":
            if cross_encoder_dir is None:
                raise CommandError("--cross-encoder is required for method=cross-encoder-finetuned")
            return cross_encoder.FineTunedCrossEncoder(checkpoint_dir=cross_encoder_dir)
        if method == "rankgpt-listwise":
            if not listwise_llm.AnthropicLLMClient()._resolve_api_key():
                raise CommandError(
                    "Anthropic API key not configured for method=rankgpt-listwise"
                )
            return listwise_llm.RankGPTReranker()
        if method == "cohere-rerank":
            reranker = baselines.CohereRerankerBaseline()
            if not reranker._resolve_api_key():
                raise CommandError("Cohere API key not configured for method=cohere-rerank")
            return reranker
        raise CommandError(f"unsupported forecasting probe method: {method}")
