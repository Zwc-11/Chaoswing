"""Module 6 driver — `python manage.py run_rerank_benchmark`.

Scores every available reranker on the same temporal test set and writes
the comparison table the README and resume bullets quote.

Per source in the test slice of `--split`:
  1. Collect the labeled candidate list (`RankingExample.candidates`).
  2. For each registered reranker, call `rerank(source, candidates, cutoff=test_cutoff)`.
  3. Compute NDCG@5, NDCG@10, MRR, Recall@100, Recall@5; time the call.
  4. Aggregate per-method means; persist a `RerankerRun` row.

A line of stdout per method, sorted by NDCG@5 desc — same shape as the
resume bullet:

    cross-encoder-finetuned   ndcg@5=0.612  mrr=0.701  recall@5=0.812  p95=23ms
    biencoder-cosine          ndcg@5=0.485  mrr=0.560  recall@5=0.712  p95= 8ms
    bm25                      ndcg@5=0.341  mrr=0.418  recall@5=0.581  p95= 1ms
    lexical-overlap           ndcg@5=0.298  mrr=0.371  recall@5=0.520  p95= 1ms

Cohere is reported but **never the headline** (Ranker.md §"Don't" — "Cohere
is eval-only and never headlined").
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from chaoswing.ml._types import MarketDoc, TemporalCutoff
from chaoswing.ml.eval import ndcg_at_k, mrr as mrr_fn, recall_at_k

from apps.ranker.models import RankingExample, RerankerRun, TemporalSplit
from apps.ranker.services._registry import Reranker, registry
from apps.ranker.services._repository import DjangoSnapshotRepository

# Import service modules so their @register decorators run before we look up names.
from apps.ranker.services import baselines, biencoder, cross_encoder, listwise_llm  # noqa: F401


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _MethodReport:
    method: str
    n_sources: int
    ndcg_at_5: float
    ndcg_at_10: float
    mrr: float
    recall_at_100: float
    recall_at_5: float
    p95_latency_ms: float
    per_source: list[dict] = field(default_factory=list)
    error: str | None = None

    def as_metrics_dict(self) -> dict:
        return {
            "ndcg_at_5": self.ndcg_at_5,
            "ndcg_at_10": self.ndcg_at_10,
            "mrr": self.mrr,
            "recall_at_100": self.recall_at_100,
            "recall_at_5": self.recall_at_5,
            "p95_latency_ms": self.p95_latency_ms,
            "n_sources": self.n_sources,
        }


class Command(BaseCommand):
    help = "Benchmark every registered reranker on the test slice of a TemporalSplit."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--split", required=True, help="TemporalSplit.name to evaluate on.")
        parser.add_argument(
            "--only",
            nargs="*",
            default=None,
            help="Restrict to these method names (default: every registered method).",
        )
        parser.add_argument(
            "--biencoder-index",
            type=Path,
            default=None,
            help="Path to a biencoder .meta.json. Required to include biencoder-cosine.",
        )
        parser.add_argument(
            "--cross-encoder",
            type=Path,
            default=None,
            help="Cross-encoder checkpoint dir. Required to include cross-encoder-finetuned.",
        )
        parser.add_argument(
            "--max-sources",
            type=int,
            default=None,
            help="Cap the number of test sources scored (useful for smoke runs).",
        )
        parser.add_argument(
            "--out-dir",
            type=Path,
            default=Path("ml_data"),
            help="Output directory for the JSONL report.",
        )
        parser.add_argument(
            "--run-id",
            default=None,
            help="Benchmark run id (used in output filenames).",
        )

    def handle(self, *args, **options) -> None:
        try:
            split = TemporalSplit.objects.get(name=options["split"])
        except TemporalSplit.DoesNotExist as exc:
            raise CommandError(f"no TemporalSplit named '{options['split']}'") from exc

        run_id = options.get("run_id") or uuid.uuid4().hex[:12]
        test_cutoff = TemporalCutoff(timestamp=split.val_cutoff, label="test_cutoff")

        rows = list(
            RankingExample.objects.filter(split=split, split_name="test")
            .values("source_market_id", "candidates", "event_family")
        )
        if not rows:
            raise CommandError("no test RankingExamples for split")
        if options["max_sources"]:
            rows = rows[: options["max_sources"]]

        docs_by_id = self._load_docs(rows)

        # Build per-source `(source, candidates, label_map)` triples once; every
        # reranker sees the same input. Sources missing from the doc map are skipped.
        eval_rows = self._build_eval_rows(rows, docs_by_id)
        if not eval_rows:
            raise CommandError("no eval rows after joining with MarketDoc lookups")

        rerankers = self._build_rerankers(
            only=set(options["only"]) if options["only"] else None,
            biencoder_index=options["biencoder_index"],
            cross_encoder_dir=options["cross_encoder"],
        )
        if not rerankers:
            raise CommandError("no rerankers available to benchmark")

        reports: list[_MethodReport] = []
        for name, instance in rerankers.items():
            report = self._score_method(name, instance, eval_rows, test_cutoff)
            reports.append(report)
            self._persist_run(report, split=split, run_id=run_id)

        out_dir: Path = options["out_dir"]
        out_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = out_dir / f"benchmark_{run_id}.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as fh:
            for report in reports:
                fh.write(json.dumps({
                    "method": report.method,
                    "metrics": report.as_metrics_dict(),
                    "error": report.error,
                }, separators=(",", ":")))
                fh.write("\n")

        self.stdout.write(self._format_table(reports))
        self.stdout.write(json.dumps({
            "run_id": run_id,
            "split": split.name,
            "n_sources": len(eval_rows),
            "report_path": str(jsonl_path),
        }, indent=2))

    # ----- per-method scoring --------------------------------------------

    def _score_method(
        self,
        name: str,
        reranker: Reranker,
        eval_rows: Sequence[dict],
        cutoff: TemporalCutoff,
    ) -> _MethodReport:
        latencies_ms: list[float] = []
        per_source_ndcg5: list[float] = []
        per_source_ndcg10: list[float] = []
        per_source_mrr: list[float] = []
        per_source_r100: list[float] = []
        per_source_r5: list[float] = []
        per_source_log: list[dict] = []
        try:
            for row in eval_rows:
                source: MarketDoc = row["source"]
                candidates: list[MarketDoc] = row["candidates"]
                label_map: dict[str, int] = row["label_map"]
                t0 = time.perf_counter()
                ranked = reranker.rerank(source, candidates, cutoff=cutoff)
                latencies_ms.append((time.perf_counter() - t0) * 1000.0)
                scores = [r.score for r in ranked]
                labels = [label_map.get(r.market_id, 0) for r in ranked]
                per_source_ndcg5.append(ndcg_at_k(scores, labels, 5))
                per_source_ndcg10.append(ndcg_at_k(scores, labels, 10))
                per_source_mrr.append(mrr_fn(scores, labels))
                per_source_r100.append(recall_at_k(scores, labels, 100))
                per_source_r5.append(recall_at_k(scores, labels, 5))
                per_source_log.append({
                    "source_market_id": source.market_id,
                    "ndcg_at_5": per_source_ndcg5[-1],
                    "mrr": per_source_mrr[-1],
                })
        except Exception as exc:
            logger.warning("method=%s failed: %s", name, exc)
            return _MethodReport(
                method=name, n_sources=0,
                ndcg_at_5=0.0, ndcg_at_10=0.0, mrr=0.0,
                recall_at_100=0.0, recall_at_5=0.0, p95_latency_ms=0.0,
                error=str(exc),
            )
        if not per_source_ndcg5:
            return _MethodReport(
                method=name, n_sources=0,
                ndcg_at_5=0.0, ndcg_at_10=0.0, mrr=0.0,
                recall_at_100=0.0, recall_at_5=0.0, p95_latency_ms=0.0,
                error="no scored sources",
            )
        return _MethodReport(
            method=name,
            n_sources=len(per_source_ndcg5),
            ndcg_at_5=_mean(per_source_ndcg5),
            ndcg_at_10=_mean(per_source_ndcg10),
            mrr=_mean(per_source_mrr),
            recall_at_100=_mean(per_source_r100),
            recall_at_5=_mean(per_source_r5),
            p95_latency_ms=_percentile(latencies_ms, 95),
            per_source=per_source_log,
        )

    # ----- setup helpers -------------------------------------------------

    def _build_eval_rows(
        self,
        rows: Sequence[dict],
        docs_by_id: dict[str, MarketDoc],
    ) -> list[dict]:
        out: list[dict] = []
        for row in rows:
            source = docs_by_id.get(row["source_market_id"])
            if source is None:
                continue
            cand_docs: list[MarketDoc] = []
            label_map: dict[str, int] = {}
            for c in row.get("candidates", []) or []:
                cid = c.get("candidate_market_id")
                doc = docs_by_id.get(cid) if cid else None
                if doc is None:
                    continue
                cand_docs.append(doc)
                label_map[cid] = int(c.get("relevance", 0))
            if len(cand_docs) < 2:
                continue
            # The reranker eval is only meaningful when there's at least one positive.
            if max(label_map.values(), default=0) < 2:
                continue
            out.append({"source": source, "candidates": cand_docs, "label_map": label_map})
        return out

    def _load_docs(self, rows: Sequence[dict]) -> dict[str, MarketDoc]:
        ids: set[str] = set()
        for row in rows:
            ids.add(row["source_market_id"])
            for c in row.get("candidates", []) or []:
                cid = c.get("candidate_market_id")
                if cid:
                    ids.add(cid)
        return DjangoSnapshotRepository().load_market_docs(ids)

    def _build_rerankers(
        self,
        *,
        only: set[str] | None,
        biencoder_index: Path | None,
        cross_encoder_dir: Path | None,
    ) -> dict[str, Reranker]:
        """Construct one instance per registered method.

        Stateless methods (BM25, lexical, Cohere) use no-arg constructors.
        Bi-encoder and cross-encoder need explicit artifact paths.
        """
        wanted = only if only else set(registry.names())
        instances: dict[str, Reranker] = {}

        if "bm25" in wanted:
            instances["bm25"] = baselines.BM25Reranker()
        if "lexical-overlap" in wanted:
            instances["lexical-overlap"] = baselines.LexicalOverlapReranker()
        if "cohere-rerank" in wanted:
            # Probe API key now so we can skip cleanly rather than blow up mid-loop.
            probe = baselines.CohereRerankerBaseline()
            if probe._resolve_api_key():
                instances["cohere-rerank"] = probe
            else:
                logger.info("skipping cohere-rerank: API key not configured")
        if "biencoder-cosine" in wanted:
            if biencoder_index is None:
                logger.info("skipping biencoder-cosine: --biencoder-index not provided")
            else:
                instances["biencoder-cosine"] = biencoder.BiEncoderReranker.from_index_path(
                    biencoder_index
                )
        if "cross-encoder-finetuned" in wanted:
            if cross_encoder_dir is None:
                logger.info(
                    "skipping cross-encoder-finetuned: --cross-encoder not provided"
                )
            else:
                instances["cross-encoder-finetuned"] = cross_encoder.FineTunedCrossEncoder(
                    checkpoint_dir=cross_encoder_dir
                )
        if "rankgpt-listwise" in wanted:
            # Same opt-in stance as Cohere: paid API, don't burn tokens unless
            # the user has wired it up. We probe the API key once, up front,
            # so the loop doesn't crash mid-way through.
            probe = listwise_llm.RankGPTReranker()
            api_client = listwise_llm.AnthropicLLMClient()
            if api_client._resolve_api_key():
                instances["rankgpt-listwise"] = probe
            else:
                logger.info(
                    "skipping rankgpt-listwise: ANTHROPIC API key not configured"
                )

        return instances

    def _persist_run(
        self,
        report: _MethodReport,
        *,
        split: TemporalSplit,
        run_id: str,
    ) -> None:
        RerankerRun.objects.create(
            method=report.method,
            kind="inference",
            split=split,
            metrics=report.as_metrics_dict(),
            config={"benchmark_run_id": run_id, "error": report.error or ""},
        )

    # ----- output formatting --------------------------------------------

    def _format_table(self, reports: Sequence[_MethodReport]) -> str:
        # Sort by NDCG@5 desc, failures last.
        ordered = sorted(
            reports,
            key=lambda r: (r.error is not None, -r.ndcg_at_5),
        )
        lines = [
            "method                          ndcg@5    ndcg@10   mrr       "
            "recall@5  recall@100  p95(ms)   n",
        ]
        for r in ordered:
            if r.error:
                lines.append(f"{r.method:<31s} unavailable: {r.error}")
                continue
            lines.append(
                f"{r.method:<31s} {r.ndcg_at_5:<8.4f} {r.ndcg_at_10:<8.4f} "
                f"{r.mrr:<8.4f} {r.recall_at_5:<8.4f} {r.recall_at_100:<10.4f} "
                f"{r.p95_latency_ms:<8.1f} {r.n_sources:d}"
            )
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Small numeric helpers (kept local to the command so the service layer
# doesn't accumulate one-line utilities).
# ---------------------------------------------------------------------------


def _mean(xs: Sequence[float]) -> float:
    return float(sum(xs) / len(xs)) if xs else 0.0


def _percentile(xs: Sequence[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * p / 100.0
    lower = int(k)
    upper = min(lower + 1, len(s) - 1)
    weight = k - lower
    return float(s[lower] * (1 - weight) + s[upper] * weight)
