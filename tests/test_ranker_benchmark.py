"""End-to-end test for the benchmark loop.

Builds a `TemporalSplit` + `RankingExample`s + `MarketSnapshot`s in the test
DB, runs `run_rerank_benchmark` with `--only` restricted to the two stateless
baselines (BM25 + lexical-overlap), and asserts the comparison table,
`RerankerRun` rows, and JSONL report all line up.

We do **not** exercise the bi-encoder or cross-encoder paths here — they
need artifact paths and heavy ML deps. Those are covered by their own test
modules. This test is about the orchestration: registry discovery → eval
row construction → per-method scoring → persistence.
"""
from __future__ import annotations

import io
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from apps.ranker.models import RankingExample, RerankerRun, TemporalSplit
from apps.web.models import MarketSnapshot


CUTOFF_TS = datetime(2025, 6, 1, tzinfo=timezone.utc)
PRE = CUTOFF_TS - timedelta(days=30)


def _seed_market(slug: str, *, title: str, snapshot_at: datetime = PRE) -> MarketSnapshot:
    return MarketSnapshot.objects.create(
        source_url=f"https://example.test/{slug}",
        event_slug=slug,
        event_title=title,
        snapshot_at=snapshot_at,
        implied_probability=0.5,
    )


class BenchmarkLoopTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        # Two clear clusters so BM25 + lexical have an obvious ranking signal.
        cls.markets = {
            "src_fed": _seed_market("src_fed", title="Will the Fed raise interest rates"),
            "pos_fed_1": _seed_market("pos_fed_1", title="Federal Reserve hikes interest rates"),
            "pos_fed_2": _seed_market("pos_fed_2", title="Fed raises interest rates by 25bp"),
            "neg_food_1": _seed_market("neg_food_1", title="Recipe for sourdough bread"),
            "neg_food_2": _seed_market("neg_food_2", title="Best chocolate chip cookies"),
            "src_elec": _seed_market("src_elec", title="Who wins the 2028 presidential election"),
            "pos_elec_1": _seed_market("pos_elec_1", title="Republican wins presidential race"),
            "pos_elec_2": _seed_market("pos_elec_2", title="GOP candidate wins the White House"),
            "neg_sports_1": _seed_market("neg_sports_1", title="Sports team wins championship game"),
            "neg_sports_2": _seed_market("neg_sports_2", title="Movie review summer blockbuster"),
        }
        cls.split = TemporalSplit.objects.create(
            name="bench-test",
            train_cutoff=CUTOFF_TS - timedelta(days=90),
            val_cutoff=CUTOFF_TS,
            family_assignments={},
            counts={"train": 0, "val": 0, "test": 2},
        )
        RankingExample.objects.create(
            split=cls.split,
            split_name="test",
            source_market_id="src_fed",
            event_family="src_fed",
            candidates=[
                {"candidate_market_id": "pos_fed_1", "relevance": 3, "negative_kind": "positive"},
                {"candidate_market_id": "pos_fed_2", "relevance": 2, "negative_kind": "positive"},
                {"candidate_market_id": "neg_food_1", "relevance": 0, "negative_kind": "easy"},
                {"candidate_market_id": "neg_food_2", "relevance": 0, "negative_kind": "easy"},
            ],
        )
        RankingExample.objects.create(
            split=cls.split,
            split_name="test",
            source_market_id="src_elec",
            event_family="src_elec",
            candidates=[
                {"candidate_market_id": "pos_elec_1", "relevance": 3, "negative_kind": "positive"},
                {"candidate_market_id": "pos_elec_2", "relevance": 2, "negative_kind": "positive"},
                {"candidate_market_id": "neg_sports_1", "relevance": 0, "negative_kind": "easy"},
                {"candidate_market_id": "neg_sports_2", "relevance": 0, "negative_kind": "easy"},
            ],
        )

    def test_benchmark_runs_bm25_and_lexical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            stdout = io.StringIO()
            call_command(
                "run_rerank_benchmark",
                "--split", "bench-test",
                "--only", "bm25", "lexical-overlap",
                "--out-dir", str(out_dir),
                "--run-id", "smoke",
                stdout=stdout,
            )
            text = stdout.getvalue()
            # Header + at least one method row per scored method.
            self.assertIn("method", text)
            self.assertIn("bm25", text)
            self.assertIn("lexical-overlap", text)

            # JSONL report on disk
            report_path = out_dir / "benchmark_smoke.jsonl"
            self.assertTrue(report_path.exists())
            with report_path.open("r", encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
            method_names = {row["method"] for row in rows}
            self.assertEqual(method_names, {"bm25", "lexical-overlap"})
            for row in rows:
                self.assertIsNone(row["error"])
                self.assertEqual(row["metrics"]["n_sources"], 2)
                # With obvious lexical signal, NDCG@5 must be > 0 — the positives
                # rank above the noise.
                self.assertGreater(row["metrics"]["ndcg_at_5"], 0.5)

            # Persistence: one RerankerRun per method
            runs = RerankerRun.objects.filter(
                split=self.split, method__in=["bm25", "lexical-overlap"]
            )
            self.assertEqual(runs.count(), 2)
            for run in runs:
                self.assertEqual(run.kind, "inference")
                self.assertGreater(run.metrics["ndcg_at_5"], 0.5)
                self.assertEqual(run.metrics["n_sources"], 2)
                self.assertEqual(run.config["benchmark_run_id"], "smoke")

    def test_only_argument_filters_methods(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            call_command(
                "run_rerank_benchmark",
                "--split", "bench-test",
                "--only", "bm25",
                "--out-dir", tmp,
                "--run-id", "single",
                stdout=stdout,
            )
            text = stdout.getvalue()
            self.assertIn("bm25", text)
            self.assertNotIn(" lexical-overlap ", text)

    def test_cohere_skipped_when_unavailable(self) -> None:
        """Without an API key, Cohere should silently drop out of the run."""
        import os

        prior = os.environ.pop("COHERE_API_KEY", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                stdout = io.StringIO()
                call_command(
                    "run_rerank_benchmark",
                    "--split", "bench-test",
                    "--only", "bm25", "cohere-rerank",
                    "--out-dir", tmp,
                    "--run-id", "no-cohere",
                    stdout=stdout,
                )
                text = stdout.getvalue()
                self.assertIn("bm25", text)
                # Cohere was requested but skipped, so it isn't in the table.
                self.assertNotIn(" cohere-rerank ", text)
        finally:
            if prior is not None:
                os.environ["COHERE_API_KEY"] = prior
