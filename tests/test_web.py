import json

from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from apps.web.models import GraphRun
from apps.web.services.contracts import PolymarketEventSnapshot, RelatedEventCandidate
from apps.web.services.graph_builder import GraphConstructionService
from apps.web.services.graph_workflow import GraphWorkflowService


@override_settings(CHAOSWING_ENABLE_REMOTE_FETCH=False, CHAOSWING_ENABLE_LLM=False)
class WebRoutesTests(TestCase):
    def test_root_renders_landing_page(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "See the")
        self.assertContains(response, "Launch App")

    def test_dashboard_renders(self):
        response = self.client.get(reverse("web:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Butterfly Effect Engine for Prediction Markets")
        self.assertContains(response, "Load Butterfly Graph")
        self.assertContains(response, "chaoswing-initial-state")

    def test_graph_api_returns_persisted_payload(self):
        response = self.client.post(
            reverse("web:graph_from_url"),
            data=json.dumps(
                {
                    "url": "https://polymarket.com/event/will-brent-crude-trade-above-95-before-july-2026"
                }
            ),
            content_type="application/json",
        )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["event"]["status"], "open")
        self.assertIsNotNone(payload["run"]["id"])
        self.assertEqual(payload["run"]["mode"], "deterministic-fallback")
        self.assertGreaterEqual(len(payload["graph"]["nodes"]), 10)
        self.assertGreaterEqual(len(payload["graph"]["edges"]), 10)
        self.assertIn("event_primary", payload["assets"])
        self.assertIn("review", payload["run"])
        self.assertTrue(payload["graph"]["nodes"][0]["source_url"])
        self.assertTrue(payload["graph"]["nodes"][0]["icon_key"])
        related_market = next(
            node for node in payload["graph"]["nodes"] if node["type"] == "RelatedMarket"
        )
        self.assertNotEqual(related_market["source_url"], payload["event"]["source_url"])
        self.assertTrue(related_market["source_description"])
        run = GraphRun.objects.get()
        self.assertEqual(run.mode, "deterministic-fallback")
        self.assertTrue(run.source_snapshot)
        self.assertTrue(run.graph_stats)

    def test_graph_run_detail_endpoint_returns_saved_run(self):
        create_response = self.client.post(
            reverse("web:graph_from_url"),
            data=json.dumps({"url": "https://polymarket.com/event/will-opec-plus-extend-output-cuts-through-q3-2026"}),
            content_type="application/json",
        )
        run_id = create_response.json()["run"]["id"]

        detail_response = self.client.get(reverse("web:graph_run_detail", args=[run_id]))
        detail_payload = detail_response.json()

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_payload["id"], run_id)
        self.assertIn("payload", detail_payload)
        self.assertIn("workflow_log", detail_payload)
        self.assertIn("graph_stats", detail_payload)

    def test_review_run_endpoint_returns_review(self):
        create_response = self.client.post(
            reverse("web:graph_from_url"),
            data=json.dumps({"url": "https://polymarket.com/event/will-opec-plus-extend-output-cuts-through-q3-2026"}),
            content_type="application/json",
        )
        run_id = create_response.json()["run"]["id"]

        review_response = self.client.post(reverse("web:review_graph_run", args=[run_id]))
        review_payload = review_response.json()

        self.assertEqual(review_response.status_code, 200)
        self.assertEqual(review_payload["id"], run_id)
        self.assertIn("review", review_payload)
        self.assertIn("quality_score", review_payload["review"])

    def test_graph_api_rejects_non_polymarket_urls(self):
        response = self.client.post(
            reverse("web:graph_from_url"),
            data=json.dumps({"url": "https://example.com/not-polymarket"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Use a full Polymarket event URL.")

    def test_inspector_empty_partial_renders(self):
        response = self.client.get(reverse("web:inspector_empty_partial"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a graph element")

    def test_inspector_node_partial_renders(self):
        response = self.client.post(
            reverse("web:inspector_node_partial"),
            data=json.dumps(
                {
                    "label": "OPEC+ supply policy",
                    "type": "Entity",
                    "confidence": 0.89,
                    "summary": "Direct market driver.",
                    "source_url": "https://polymarket.com/event/example",
                    "source_title": "Will OPEC+ tighten supply this summer?",
                    "source_description": "This market tracks whether OPEC+ extends tighter production discipline.",
                    "icon_url": "data:image/svg+xml;base64,abc123",
                    "metadata": [{"label": "Signal", "value": "Quota guidance"}],
                    "evidence_snippets": ["Ministers hinted at tighter discipline."],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OPEC+ supply policy")
        self.assertContains(response, "Quota guidance")
        self.assertContains(response, "Will OPEC+ tighten supply this summer?")
        self.assertContains(
            response, "This market tracks whether OPEC+ extends tighter production discipline."
        )
        self.assertContains(response, "Open market")

    def test_inspector_edge_partial_renders(self):
        response = self.client.post(
            reverse("web:inspector_edge_partial"),
            data=json.dumps(
                {
                    "source_label": "Event",
                    "target_label": "Rule",
                    "type": "governed_by_rule",
                    "confidence": 0.97,
                    "explanation": "Exchange settlement determines resolution.",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Event")
        self.assertContains(response, "Exchange settlement determines resolution.")


@override_settings(CHAOSWING_ENABLE_REMOTE_FETCH=False, CHAOSWING_ENABLE_LLM=False)
class GraphWorkflowServiceTests(TestCase):
    def test_graph_workflow_resolved_backend_with_dynamic_inputs(self):
        snapshot = PolymarketEventSnapshot(
            source_url="https://polymarket.com/event/will-openai-release-gpt-5",
            canonical_url="https://polymarket.com/event/will-openai-release-gpt-5",
            event_id="evt_live_1",
            slug="will-openai-release-gpt-5",
            title="Will OpenAI release GPT-5?",
            description="OpenAI release timing could ripple into adjacent AI and semiconductor contracts.",
            resolution_source="https://openai.com",
            image_url="",
            icon_url="",
            status="open",
            category="Technology",
            tags=["AI", "OpenAI", "Compute"],
            tag_ids=["1", "2"],
            outcomes=["Yes", "No"],
            updated_at="2026-03-10T18:00:00Z",
            volume=120000,
            liquidity=54000,
            open_interest=22000,
            markets=[],
            source_kind="gamma-api",
            subtitle="Technology | 1 market | $120,000 volume",
        )
        related_snapshot = PolymarketEventSnapshot(
            source_url="https://polymarket.com/event/will-nvidia-stock-hit-250",
            canonical_url="https://polymarket.com/event/will-nvidia-stock-hit-250",
            event_id="evt_live_2",
            slug="will-nvidia-stock-hit-250",
            title="Will Nvidia stock hit $250?",
            description="AI compute demand can spill into semiconductor equities.",
            resolution_source="https://example.com",
            image_url="",
            icon_url="",
            status="open",
            category="Technology",
            tags=["AI", "Compute", "Semiconductors"],
            tag_ids=["2", "3"],
            outcomes=["Yes", "No"],
            updated_at="2026-03-10T18:00:00Z",
            volume=98000,
            liquidity=45000,
            open_interest=15000,
            markets=[],
            source_kind="gamma-api",
            subtitle="Technology",
        )

        class StubMetadataService:
            def hydrate(self, source_url):
                if "nvidia" in source_url:
                    return related_snapshot
                return snapshot

        class StubDiscoveryService:
            def discover(self, snapshot, limit=4):
                return [
                    RelatedEventCandidate(
                        snapshot=related_snapshot,
                        confidence=0.84,
                        rationale="shared tags: ai, compute; title overlap: ai, release",
                        shared_tags=["ai", "compute"],
                        shared_terms=["ai", "release"],
                    )
                ]

        class DisabledAgent:
            available = False
            model = ""

            def expand_graph(self, snapshot, seed_payload):
                return None

            def review_graph(self, snapshot, payload):
                return None

        workflow = GraphWorkflowService(
            metadata_service=StubMetadataService(),
            discovery_service=StubDiscoveryService(),
            graph_builder=GraphConstructionService(),
            agent=DisabledAgent(),
        )

        payload = workflow.run(snapshot.source_url)

        self.assertEqual(payload["run"]["mode"], "resolved-backend")
        self.assertEqual(payload["event"]["title"], snapshot.title)
        self.assertTrue(payload["context"]["source_snapshot"])
        self.assertEqual(payload["run"]["graph_stats"]["related_markets"], 1)
        related_node = next(
            node for node in payload["graph"]["nodes"] if node["type"] == "RelatedMarket"
        )
        self.assertEqual(related_node["source_url"], related_snapshot.canonical_url)
        self.assertTrue(related_node["icon_key"])
