from __future__ import annotations

import io
import json

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings

from apps.web.models import ExperimentRun, GraphRun
from apps.web.services.market_intelligence import (
    BenchmarkSummaryService,
    DatasetBuilderService,
    RelatedMarketRankingBenchmarkService,
)


def _graph_payload(
    *,
    title: str,
    description: str,
    category: str,
    context_nodes: list[dict[str, object]],
    related_markets: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "event": {
            "title": title,
            "description": description,
            "tags": [category.lower(), "unit-test"],
        },
        "run": {
            "review": {
                "approved": True,
                "quality_score": 0.84,
            }
        },
        "graph": {
            "nodes": [
                {
                    "id": "event",
                    "type": "Event",
                    "label": title,
                    "summary": description,
                    "confidence": 0.95,
                    "metadata": [],
                },
                *context_nodes,
                *related_markets,
            ],
            "edges": [],
        },
        "context": {
            "source_snapshot": {
                "category": category,
                "tags": [category.lower(), "unit-test"],
            }
        },
    }


@override_settings(
    CHAOSWING_ENABLE_REMOTE_FETCH=False,
    CHAOSWING_ENABLE_LLM=False,
    CHAOSWING_RATE_LIMIT_ENABLED=False,
)
class RelatedMarketRankingTests(TestCase):
    def setUp(self):
        cache.clear()
        self._create_run(
            slug="fed-decision-in-march",
            title="Fed decision in March",
            description="Track how a March Fed decision cascades into rates and large-cap leadership.",
            category="Macro",
            context_nodes=[
                {
                    "id": "entity-fed",
                    "type": "Entity",
                    "label": "Treasury yields",
                    "summary": "Rates and Treasury yields move when the market reprices rate cuts.",
                    "confidence": 0.82,
                    "metadata": [{"label": "Theme", "value": "rate cuts"}],
                },
                {
                    "id": "evidence-fed",
                    "type": "Evidence",
                    "label": "FOMC path repricing",
                    "summary": "The strongest spillover is to rate cuts, duration, and growth leadership.",
                    "confidence": 0.78,
                    "metadata": [{"label": "Catalyst", "value": "March FOMC"}],
                },
            ],
            related_markets=[
                {
                    "id": "related-fed-cuts",
                    "type": "RelatedMarket",
                    "label": "How many Fed rate cuts in 2026?",
                    "summary": "Longer-duration expression of the same rate-cut narrative.",
                    "confidence": 0.9,
                    "metadata": [{"label": "Category", "value": "Macro"}],
                },
                {
                    "id": "related-fed-company",
                    "type": "RelatedMarket",
                    "label": "Largest company end of March?",
                    "summary": "Growth leadership market that moves when rates change.",
                    "confidence": 0.74,
                    "metadata": [{"label": "Category", "value": "Macro"}],
                },
            ],
        )
        self._create_run(
            slug="crude-oil-end-of-march",
            title="Crude oil by end of March",
            description="Track how an oil shock spills into inflation and commodity-linked contracts.",
            category="Commodities",
            context_nodes=[
                {
                    "id": "entity-oil",
                    "type": "Entity",
                    "label": "Energy inflation",
                    "summary": "Energy inflation spillover matters more than simple price overlap.",
                    "confidence": 0.81,
                    "metadata": [{"label": "Theme", "value": "inflation"}],
                },
                {
                    "id": "evidence-oil",
                    "type": "Evidence",
                    "label": "Commodity repricing",
                    "summary": "Downstream inflation markets react when crude rallies on supply shocks.",
                    "confidence": 0.76,
                    "metadata": [{"label": "Catalyst", "value": "oil supply"}],
                },
            ],
            related_markets=[
                {
                    "id": "related-oil-cpi",
                    "type": "RelatedMarket",
                    "label": "Will CPI come in above expectations next release?",
                    "summary": "Inflation-linked contract downstream of an energy shock.",
                    "confidence": 0.88,
                    "metadata": [{"label": "Category", "value": "Commodities"}],
                },
                {
                    "id": "related-oil-cl",
                    "type": "RelatedMarket",
                    "label": "Will Crude Oil (CL) hit week of March 9?",
                    "summary": "Alternative crude contract sensitive to the same supply narrative.",
                    "confidence": 0.72,
                    "metadata": [{"label": "Category", "value": "Commodities"}],
                },
            ],
        )
        self._create_run(
            slug="democratic-nominee-2028",
            title="Democratic nominee 2028",
            description="Track coalition shifts, turnout, and debate catalysts in the nomination race.",
            category="Politics",
            context_nodes=[
                {
                    "id": "entity-politics",
                    "type": "Entity",
                    "label": "Campaign momentum",
                    "summary": "Coalition, turnout, and debate moments drive candidate momentum.",
                    "confidence": 0.83,
                    "metadata": [{"label": "Theme", "value": "turnout"}],
                },
                {
                    "id": "evidence-politics",
                    "type": "Evidence",
                    "label": "Debate catalyst",
                    "summary": "Debates and media moments re-rank candidates and turnout expectations.",
                    "confidence": 0.77,
                    "metadata": [{"label": "Catalyst", "value": "debate"}],
                },
            ],
            related_markets=[
                {
                    "id": "related-politics-vote",
                    "type": "RelatedMarket",
                    "label": "Will Democrats win popular vote 2028?",
                    "summary": "Turnout and coalition proxy for the same race narrative.",
                    "confidence": 0.86,
                    "metadata": [{"label": "Category", "value": "Politics"}],
                },
                {
                    "id": "related-politics-debate",
                    "type": "RelatedMarket",
                    "label": "Will there be a presidential debate?",
                    "summary": "Catalyst market for campaign momentum and media narratives.",
                    "confidence": 0.71,
                    "metadata": [{"label": "Category", "value": "Politics"}],
                },
            ],
        )

    def _create_run(
        self,
        *,
        slug: str,
        title: str,
        description: str,
        category: str,
        context_nodes: list[dict[str, object]],
        related_markets: list[dict[str, object]],
    ) -> None:
        GraphRun.objects.create(
            source_url=f"https://polymarket.com/event/{slug}",
            event_slug=slug,
            event_title=title,
            mode="deterministic-fallback",
            source_snapshot={"category": category, "tags": [category.lower()]},
            graph_stats={
                "node_count": len(context_nodes) + len(related_markets) + 1,
                "related_markets": len(related_markets),
                "evidence_nodes": sum(1 for node in context_nodes if node["type"] == "Evidence"),
            },
            payload=_graph_payload(
                title=title,
                description=description,
                category=category,
                context_nodes=context_nodes,
                related_markets=related_markets,
            ),
            workflow_log=[],
        )

    def test_related_market_ranking_runs_and_updates_benchmark_summary(self):
        summary_before = BenchmarkSummaryService().build_cached(force_refresh=True)
        self.assertFalse(
            any(
                benchmark["name"] == "Related-market ranking silver benchmark"
                for benchmark in summary_before["live_benchmarks"]
            )
        )

        report = RelatedMarketRankingBenchmarkService(hard_negative_pool_size=4).run(persist=True)

        self.assertEqual(report["task_type"], "related_market_ranking")
        self.assertEqual(report["example_count"], 3)
        self.assertIn("model_ndcg_at_5", report["metrics"])
        self.assertIn("baseline_ndcg_at_5", report["metrics"])
        self.assertTrue(ExperimentRun.objects.filter(task_type="related_market_ranking").exists())

        summary_after = BenchmarkSummaryService().build_cached()
        self.assertTrue(
            any(
                benchmark["name"] == "Related-market ranking silver benchmark"
                for benchmark in summary_after["live_benchmarks"]
            )
        )
        self.assertFalse(
            any(
                benchmark["name"] == "Related-market ranking"
                for benchmark in summary_after["next_benchmarks"]
            )
        )
        self.assertTrue(
            any(
                benchmark["name"] == "Human-labeled related-market usefulness"
                for benchmark in summary_after["next_benchmarks"]
            )
        )

    def test_related_market_ranking_command_and_dataset_export(self):
        stdout = io.StringIO()

        call_command(
            "run_related_market_ranking_benchmark",
            "--no-persist",
            "--hard-negative-pool-size",
            "4",
            stdout=stdout,
        )
        payload = json.loads(stdout.getvalue())

        self.assertEqual(payload["task_type"], "related_market_ranking")
        self.assertEqual(payload["example_count"], 3)

        records = DatasetBuilderService().build_records()
        self.assertIn("related_market_ranking_examples", records)
        self.assertEqual(len(records["related_market_ranking_examples"]), 3)
