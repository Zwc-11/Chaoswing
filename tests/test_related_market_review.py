from __future__ import annotations

import io
import json

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from apps.web.models import ExperimentRun, GraphRun, RelatedMarketJudgment
from apps.web.services.market_intelligence import (
    BenchmarkSummaryService,
    DatasetBuilderService,
    RelatedMarketJudgmentService,
    RelatedMarketUsefulnessBenchmarkService,
)


def _review_payload() -> dict[str, object]:
    return {
        "event": {
            "title": "Fed decision in March",
            "description": "Rate cuts spill into duration, growth leadership, and macro proxies.",
            "tags": ["macro", "rates"],
            "source_url": "https://polymarket.com/event/fed-decision-in-march",
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
                    "id": "evt_001",
                    "type": "Event",
                    "label": "Fed decision in March",
                    "summary": "Rate decision anchor.",
                    "confidence": 1.0,
                    "metadata": [],
                },
                {
                    "id": "ent_rates",
                    "type": "Entity",
                    "label": "Rate cuts",
                    "summary": "Rate-cut expectations affect duration and growth leadership.",
                    "confidence": 0.82,
                    "metadata": [{"label": "Theme", "value": "rates"}],
                },
                {
                    "id": "ev_growth",
                    "type": "Evidence",
                    "label": "Growth leadership",
                    "summary": "Large-cap leadership can move when rates reprice.",
                    "confidence": 0.76,
                    "metadata": [{"label": "Catalyst", "value": "FOMC"}],
                },
                {
                    "id": "rel_cuts",
                    "type": "RelatedMarket",
                    "label": "How many Fed rate cuts in 2026?",
                    "summary": "Longer-duration expression of the same rate-cut narrative.",
                    "confidence": 0.91,
                    "source_url": "https://polymarket.com/event/how-many-fed-rate-cuts-in-2026",
                    "metadata": [{"label": "Category", "value": "Macro"}],
                },
                {
                    "id": "rel_company",
                    "type": "RelatedMarket",
                    "label": "Largest company end of March?",
                    "summary": "Growth leadership proxy when rates move.",
                    "confidence": 0.78,
                    "source_url": "https://polymarket.com/event/largest-company-end-of-march",
                    "metadata": [{"label": "Category", "value": "Macro"}],
                },
                {
                    "id": "rel_cpi",
                    "type": "RelatedMarket",
                    "label": "Will CPI come in above expectations next release?",
                    "summary": "Inflation-linked follow-through from rate repricing.",
                    "confidence": 0.69,
                    "source_url": "https://polymarket.com/event/will-cpi-come-in-above-expectations-next-release",
                    "metadata": [{"label": "Category", "value": "Macro"}],
                },
                {
                    "id": "rel_debate",
                    "type": "RelatedMarket",
                    "label": "Will there be a presidential debate?",
                    "summary": "Political catalyst market with weak macro relevance.",
                    "confidence": 0.54,
                    "source_url": "https://polymarket.com/event/will-there-be-a-presidential-debate",
                    "metadata": [{"label": "Category", "value": "Politics"}],
                },
            ],
            "edges": [],
        },
        "context": {
            "source_snapshot": {
                "category": "Macro",
                "tags": ["macro", "rates"],
            }
        },
    }


@override_settings(
    CHAOSWING_ENABLE_REMOTE_FETCH=False,
    CHAOSWING_ENABLE_LLM=False,
    CHAOSWING_RATE_LIMIT_ENABLED=False,
)
class RelatedMarketReviewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.run = GraphRun.objects.create(
            source_url="https://polymarket.com/event/fed-decision-in-march",
            event_slug="fed-decision-in-march",
            event_title="Fed decision in March",
            mode="resolved-backend",
            source_snapshot={"category": "Macro", "tags": ["macro", "rates"]},
            graph_stats={"related_markets": 4, "evidence_nodes": 1},
            payload=_review_payload(),
            workflow_log=[],
        )
        self.service = RelatedMarketJudgmentService()

    def _candidates(self) -> list[dict[str, object]]:
        return self.service.review_queue()["cases"][0]["candidates"]

    def _upsert(self, candidate: dict[str, object], label: str, *, reviewer: str, notes: str = ""):
        self.service.upsert_judgment(
            self.run,
            candidate_key=str(candidate["candidate_key"]),
            candidate_title=str(candidate["title"]),
            candidate_summary=str(candidate["summary"]),
            candidate_source_url=str(candidate["source_url"]),
            candidate_rank=int(candidate["rank"]),
            candidate_confidence=float(candidate["confidence"]),
            usefulness_label=label,
            notes=notes,
            reviewer=reviewer,
        )

    def test_review_queue_page_and_api_render(self):
        page = self.client.get(reverse("web:related_market_review"))
        api = self.client.get(reverse("web:related_market_review_queue"))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Related-Market Review Queue")
        self.assertContains(page, "Save label")
        self.assertEqual(api.status_code, 200)
        payload = api.json()
        self.assertIn("summary", payload)
        self.assertEqual(payload["summary"]["reviewer_count"], 0)
        self.assertEqual(len(payload["cases"]), 1)
        self.assertEqual(payload["cases"][0]["event_title"], "Fed decision in March")
        self.assertEqual(payload["cases"][0]["candidates"][0]["review_state"], "pending")

    def test_review_queue_surfaces_second_review_and_contested_states(self):
        candidates = self._candidates()
        contested = candidates[0]
        agreed = candidates[1]

        self._upsert(contested, "core", reviewer="alice", notes="Primary hedge")
        self._upsert(contested, "reject", reviewer="bob", notes="Too thematic")
        self._upsert(agreed, "watch", reviewer="alice", notes="Secondary")
        self._upsert(agreed, "watch", reviewer="bob", notes="Same view")

        queue = self.service.review_queue()
        case = queue["cases"][0]
        by_key = {candidate["candidate_key"]: candidate for candidate in case["candidates"]}

        self.assertEqual(by_key[str(contested["candidate_key"])]["review_state"], "contested")
        self.assertEqual(by_key[str(agreed["candidate_key"])]["review_state"], "agreed")
        self.assertEqual(queue["summary"]["contested_candidates"], 1)
        self.assertEqual(queue["summary"]["needs_second_review_candidates"], 0)
        self.assertEqual(queue["summary"]["reviewer_count"], 2)
        self.assertGreater(queue["summary"]["avg_agreement_rate"], 0.0)

    def test_submit_judgment_api_and_run_human_benchmark(self):
        candidates = self._candidates()
        submissions = [
            (candidates[0], "core", "alice"),
            (candidates[0], "core", "bob"),
            (candidates[1], "watch", "alice"),
            (candidates[2], "watch", "alice"),
            (candidates[3], "reject", "alice"),
        ]
        for candidate, label, reviewer in submissions:
            response = self.client.post(
                reverse("web:submit_related_market_judgment"),
                data=json.dumps(
                    {
                        "run_id": str(self.run.id),
                        "candidate_key": candidate["candidate_key"],
                        "candidate_title": candidate["title"],
                        "candidate_summary": candidate["summary"],
                        "candidate_source_url": candidate["source_url"],
                        "candidate_rank": candidate["rank"],
                        "candidate_confidence": candidate["confidence"],
                        "usefulness_label": label,
                        "notes": "Unit-test judgment",
                        "reviewer": reviewer,
                    }
                ),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)

        self.assertEqual(RelatedMarketJudgment.objects.count(), 5)

        report = RelatedMarketUsefulnessBenchmarkService(min_judged_candidates=3).run(persist=True)

        self.assertEqual(report["task_type"], "related_market_usefulness")
        self.assertEqual(report["example_count"], 1)
        self.assertIn("model_ndcg_at_5", report["metrics"])
        self.assertIn("avg_agreement_rate", report["metrics"])
        self.assertIn("multi_reviewer_candidate_rate", report["metrics"])
        self.assertTrue(ExperimentRun.objects.filter(task_type="related_market_usefulness").exists())

        summary = BenchmarkSummaryService().build_cached(force_refresh=True)
        live_benchmarks = {
            benchmark["name"]: benchmark
            for benchmark in summary["live_benchmarks"]
        }
        self.assertIn("Human-labeled related-market usefulness", live_benchmarks)
        self.assertIn("agreement", live_benchmarks["Human-labeled related-market usefulness"]["secondary_metric"])
        self.assertFalse(
            any(
                benchmark["name"] == "Human-labeled related-market usefulness"
                for benchmark in summary["next_benchmarks"]
            )
        )

    def test_same_reviewer_updates_existing_judgment_and_dataset_export_consensus(self):
        candidates = self._candidates()
        self._upsert(candidates[0], "core", reviewer="Alice Example", notes="First pass")
        self._upsert(candidates[0], "watch", reviewer="Alice Example", notes="Revised")
        self._upsert(candidates[1], "core", reviewer="Bob", notes="Useful")
        self._upsert(candidates[2], "reject", reviewer="Carol", notes="Noise")

        self.assertEqual(RelatedMarketJudgment.objects.count(), 3)
        judgment = RelatedMarketJudgment.objects.get(
            graph_run=self.run,
            candidate_key=str(candidates[0]["candidate_key"]),
        )
        self.assertEqual(judgment.usefulness_label, "watch")
        self.assertEqual(judgment.reviewer_key, "alice-example")

        stdout = io.StringIO()
        call_command(
            "run_related_market_usefulness_benchmark",
            "--no-persist",
            "--min-judged-candidates",
            "3",
            stdout=stdout,
        )
        payload = json.loads(stdout.getvalue())

        self.assertEqual(payload["task_type"], "related_market_usefulness")
        self.assertEqual(payload["example_count"], 1)

        records = DatasetBuilderService().build_records()
        self.assertIn("related_market_judgments", records)
        self.assertIn("related_market_review_consensus", records)
        self.assertIn("related_market_usefulness_examples", records)
        self.assertEqual(len(records["related_market_judgments"]), 3)
        consensus_map = {
            row["candidate_key"]: row
            for row in records["related_market_review_consensus"]
        }
        self.assertEqual(
            consensus_map[str(candidates[0]["candidate_key"])]["consensus_label"],
            "watch",
        )
        self.assertEqual(
            consensus_map[str(candidates[0]["candidate_key"])]["review_state"],
            "needs_second_review",
        )
