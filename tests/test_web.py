import json

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.web.models import AgentTrace, GraphRun, MarketSnapshot
from apps.web.services.anthropic_agent import AnthropicGraphAgent
from apps.web.services.contracts import PolymarketEventSnapshot, RelatedEventCandidate
from apps.web.services.graph_builder import GraphConstructionService
from apps.web.services.graph_workflow import GraphWorkflowService


@override_settings(
    CHAOSWING_ENABLE_REMOTE_FETCH=False,
    CHAOSWING_ENABLE_LLM=False,
    CHAOSWING_RATE_LIMIT_ENABLED=False,
)
class WebRoutesTests(TestCase):
    def test_root_renders_landing_page(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "From one market to a")
        self.assertContains(response, "Launch App")
        self.assertContains(response, "View Benchmarks")
        self.assertContains(response, 'property="og:image"')
        self.assertContains(response, 'name="twitter:image"')
        self.assertContains(response, "http://testserver/static/web/img/chaoswing-social-card.jpg")
        self.assertNotContains(response, "http://testserver/static/web/img/chaoswing-logo.png")

    def test_landing_page_uses_cached_summary_on_repeat_requests(self):
        cache.clear()
        self.client.post(
            reverse("web:graph_from_url"),
            data=json.dumps({"url": "https://polymarket.com/event/fed-decision-in-march-885"}),
            content_type="application/json",
        )

        with CaptureQueriesContext(connection) as first_ctx:
            first_response = self.client.get("/")
        with CaptureQueriesContext(connection) as second_ctx:
            second_response = self.client.get("/")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertGreater(len(first_ctx.captured_queries), 0)
        self.assertLess(len(second_ctx.captured_queries), len(first_ctx.captured_queries))

    def test_dashboard_renders(self):
        response = self.client.get(reverse("web:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ChaosWing App - Market Intelligence Workspace")
        self.assertContains(response, "Load Market Brief")
        self.assertContains(response, "Watchlists")
        self.assertContains(response, "Benchmarks")
        self.assertContains(response, "Keep coming back")
        self.assertContains(response, "chaoswing-initial-state")
        self.assertContains(response, 'property="og:image"')
        self.assertContains(response, 'name="twitter:image"')
        self.assertContains(response, "http://testserver/static/web/img/chaoswing-social-card.jpg")
        self.assertNotContains(response, "http://testserver/static/web/img/chaoswing-logo.png")

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
        self.assertIn("brief_url", payload["run"])
        related_market = next(
            node for node in payload["graph"]["nodes"] if node["type"] == "RelatedMarket"
        )
        self.assertNotEqual(related_market["source_url"], payload["event"]["source_url"])
        self.assertTrue(related_market["source_description"])
        run = GraphRun.objects.get()
        self.assertEqual(run.mode, "deterministic-fallback")
        self.assertTrue(run.source_snapshot)
        self.assertTrue(run.graph_stats)
        self.assertEqual(MarketSnapshot.objects.count(), 1)
        self.assertGreaterEqual(AgentTrace.objects.count(), 1)

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
        self.assertIn("brief_url", detail_payload)

    def test_graph_run_brief_page_and_api_render(self):
        create_response = self.client.post(
            reverse("web:graph_from_url"),
            data=json.dumps({"url": "https://polymarket.com/event/fed-decision-in-march-885"}),
            content_type="application/json",
        )
        run_id = create_response.json()["run"]["id"]

        brief_page = self.client.get(reverse("web:market_brief", args=[run_id]))
        brief_api = self.client.get(reverse("web:graph_run_brief", args=[run_id]))

        self.assertEqual(brief_page.status_code, 200)
        self.assertContains(brief_page, "Shareable market brief")
        self.assertContains(brief_page, "Workflow trace")
        self.assertContains(brief_page, "Top related markets")
        self.assertEqual(brief_api.status_code, 200)
        self.assertIn("brief", brief_api.json())
        self.assertIn("top_related_markets", brief_api.json()["brief"])
        self.assertIn("change_summary", brief_api.json()["brief"])
        self.assertIn("catalyst_timeline", brief_api.json()["brief"])
        self.assertIn("trace_summary", brief_api.json()["brief"]["trust"])
        self.assertIn("stages", brief_api.json()["brief"]["trust"]["trace_summary"])

    def test_related_markets_and_changes_apis_render(self):
        create_response = self.client.post(
            reverse("web:graph_from_url"),
            data=json.dumps({"url": "https://polymarket.com/event/fed-decision-in-march-885"}),
            content_type="application/json",
        )
        run_id = create_response.json()["run"]["id"]

        ranking_response = self.client.get(reverse("web:graph_run_related_markets", args=[run_id]))
        changes_response = self.client.get(reverse("web:graph_run_changes", args=[run_id]))

        self.assertEqual(ranking_response.status_code, 200)
        self.assertIn("ranking", ranking_response.json())
        self.assertEqual(changes_response.status_code, 200)
        self.assertIn("changes", changes_response.json())

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

    def test_inspector_node_partial_shows_conceptual_message_when_no_source_url(self):
        response = self.client.post(
            reverse("web:inspector_node_partial"),
            data=json.dumps(
                {
                    "label": "Iran risk premium",
                    "type": "Entity",
                    "confidence": 0.78,
                    "summary": "Conceptual topic extracted from the event narrative.",
                    "source_url": "",
                    "source_title": "",
                    "source_description": "",
                    "icon_url": "data:image/svg+xml;base64,abc123",
                    "metadata": [{"label": "Role", "value": "Topic or actor"}],
                    "evidence_snippets": [],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Iran risk premium")
        self.assertContains(response, "Conceptual node — no direct market link")
        self.assertNotContains(response, "Open market")

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

    def test_benchmark_pages_and_api_render(self):
        self.client.post(
            reverse("web:graph_from_url"),
            data=json.dumps({"url": "https://polymarket.com/event/fed-decision-in-march-885"}),
            content_type="application/json",
        )

        page_response = self.client.get(reverse("web:benchmarks"))
        api_response = self.client.get(reverse("web:benchmark_summary"))

        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, "Benchmark Dashboard")
        self.assertContains(page_response, "Live benchmark tracks")
        self.assertContains(page_response, "Persisted benchmark runs")
        self.assertContains(page_response, "Where current benchmark coverage comes from")
        self.assertContains(page_response, "Review labels")
        self.assertEqual(api_response.status_code, 200)
        self.assertIn("summary_cards", api_response.json())
        self.assertIn("live_benchmarks", api_response.json())
        self.assertIn("experiment_runs", api_response.json())
        self.assertIn("mode_breakdown", api_response.json())
        self.assertIn("human_label_review", api_response.json())

    def test_benchmark_page_uses_cached_summary_on_repeat_requests(self):
        cache.clear()
        self.client.post(
            reverse("web:graph_from_url"),
            data=json.dumps({"url": "https://polymarket.com/event/fed-decision-in-march-885"}),
            content_type="application/json",
        )

        with CaptureQueriesContext(connection) as first_ctx:
            first_response = self.client.get(reverse("web:benchmarks"))
        with CaptureQueriesContext(connection) as second_ctx:
            second_response = self.client.get(reverse("web:benchmarks"))

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertGreater(len(first_ctx.captured_queries), 0)
        self.assertLess(len(second_ctx.captured_queries), len(first_ctx.captured_queries))

    def test_watchlists_page_and_api_render(self):
        page_response = self.client.get(reverse("web:watchlists"))
        api_response = self.client.get(reverse("web:watchlists_api"))

        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, "Trader Watchlists")
        self.assertContains(page_response, "Reusable starting points")
        self.assertContains(page_response, "Open first market")
        self.assertEqual(api_response.status_code, 200)
        self.assertGreaterEqual(api_response.json()["count"], 1)


@override_settings(
    CHAOSWING_ENABLE_REMOTE_FETCH=False,
    CHAOSWING_ENABLE_LLM=False,
    CHAOSWING_RATE_LIMIT_ENABLED=False,
)
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
        self.assertEqual(MarketSnapshot.objects.count(), 1)
        self.assertGreaterEqual(AgentTrace.objects.count(), 1)
        planner_trace = AgentTrace.objects.get(stage="planner")
        retriever_trace = AgentTrace.objects.get(stage="retriever")
        verifier_trace = AgentTrace.objects.get(stage="verifier")
        critic_trace = AgentTrace.objects.get(stage="critic")
        event_resolution_trace = AgentTrace.objects.get(stage="event_resolution")
        self.assertGreater(planner_trace.latency_ms, 0)
        self.assertGreater(retriever_trace.latency_ms, 0)
        self.assertGreater(verifier_trace.latency_ms, 0)
        self.assertGreater(critic_trace.latency_ms, 0)
        self.assertGreater(event_resolution_trace.latency_ms, 0)
        related_node = next(
            node for node in payload["graph"]["nodes"] if node["type"] == "RelatedMarket"
        )
        self.assertEqual(related_node["source_url"], related_snapshot.canonical_url)
        self.assertTrue(related_node["icon_key"])

    def test_graph_workflow_persists_llm_trace_metadata(self):
        snapshot = PolymarketEventSnapshot(
            source_url="https://polymarket.com/event/fed-decision-in-march",
            canonical_url="https://polymarket.com/event/fed-decision-in-march",
            event_id="evt_macro_1",
            slug="fed-decision-in-march",
            title="Fed decision in March",
            description="A macro event with clear spillover into rate cuts and equities.",
            resolution_source="https://federalreserve.gov",
            image_url="",
            icon_url="",
            status="open",
            category="Macro",
            tags=["Fed", "Rates"],
            tag_ids=["1", "2"],
            outcomes=["Yes", "No"],
            updated_at="2026-03-10T18:00:00Z",
            volume=120000,
            liquidity=54000,
            open_interest=22000,
            markets=[],
            source_kind="gamma-api",
            subtitle="Macro",
        )

        class StubMetadataService:
            def hydrate(self, source_url):
                return snapshot

        class StubDiscoveryService:
            def discover(self, snapshot, limit=4):
                return []

        class InstrumentedAgent:
            available = True
            model = "claude-test"

            def expand_graph(self, snapshot, seed_payload):
                return {
                    "_llm_trace": {
                        "provider": "anthropic",
                        "model": "claude-test",
                        "latency_ms": 512,
                        "token_input": 930,
                        "token_output": 210,
                        "cost_usd": 0.0182,
                        "response_id": "msg_expansion_1",
                    },
                    "node_additions": [
                        {
                            "id": "ent_rates_signal",
                            "label": "Rate cuts repricing",
                            "type": "Entity",
                            "confidence": 0.81,
                            "summary": "Growth assets react when the market reprices rate cuts.",
                            "source_url": "https://polymarket.com/event/how-many-fed-rate-cuts-in-2026",
                            "metadata": [{"label": "Theme", "value": "rates"}],
                            "evidence_snippets": ["Treasury yields and rate-cut markets reprice together."],
                        }
                    ],
                    "edge_additions": [
                        {
                            "id": "edge_evt_rates_signal",
                            "source": "evt_001",
                            "target": "ent_rates_signal",
                            "type": "affects_directly",
                            "confidence": 0.78,
                            "explanation": "The Fed decision directly reprices rate-cut expectations.",
                        }
                    ],
                    "node_updates": [],
                    "edge_updates": [],
                    "workflow_notes": ["Added a rates spillover node."],
                }

            def review_graph(self, snapshot, payload):
                return {
                    "_llm_trace": {
                        "provider": "anthropic",
                        "model": "claude-test",
                        "latency_ms": 231,
                        "token_input": 420,
                        "token_output": 96,
                        "cost_usd": 0.0064,
                        "response_id": "msg_review_1",
                    },
                    "approved": True,
                    "issues": [],
                    "follow_up_actions": ["Monitor rate-cut market drift."],
                    "quality_score": 0.73,
                }

        workflow = GraphWorkflowService(
            metadata_service=StubMetadataService(),
            discovery_service=StubDiscoveryService(),
            graph_builder=GraphConstructionService(),
            agent=InstrumentedAgent(),
        )

        payload = workflow.run(snapshot.source_url)

        self.assertEqual(payload["run"]["mode"], "agent-enriched")
        self.assertEqual(
            [stage["stage"] for stage in payload["run"]["agent_pipeline"]],
            ["planner", "retriever", "graph_editor", "critic", "verifier"],
        )
        expansion_trace = AgentTrace.objects.get(stage="llm_expansion")
        review_trace = AgentTrace.objects.get(stage="llm_review")
        planner_trace = AgentTrace.objects.get(stage="planner")
        retriever_trace = AgentTrace.objects.get(stage="retriever")
        editor_trace = AgentTrace.objects.get(stage="graph_editor")
        verifier_trace = AgentTrace.objects.get(stage="verifier")
        critic_trace = AgentTrace.objects.get(stage="critic")

        self.assertEqual(expansion_trace.latency_ms, 512)
        self.assertEqual(expansion_trace.token_input, 930)
        self.assertEqual(expansion_trace.token_output, 210)
        self.assertAlmostEqual(expansion_trace.cost_usd, 0.0182)
        self.assertIn("https://polymarket.com/event/how-many-fed-rate-cuts-in-2026", expansion_trace.citations)
        self.assertEqual(expansion_trace.metadata["provider"], "anthropic")
        self.assertEqual(review_trace.latency_ms, 231)
        self.assertEqual(review_trace.token_input, 420)
        self.assertEqual(review_trace.token_output, 96)
        self.assertAlmostEqual(review_trace.cost_usd, 0.0064)
        self.assertEqual(review_trace.metadata["response_id"], "msg_review_1")
        self.assertEqual(planner_trace.status, "completed")
        self.assertIn(snapshot.canonical_url, planner_trace.citations)
        self.assertEqual(retriever_trace.status, "fallback")
        self.assertEqual(editor_trace.token_input, 930)
        self.assertIn("https://polymarket.com/event/how-many-fed-rate-cuts-in-2026", editor_trace.citations)
        self.assertEqual(verifier_trace.status, "completed")
        self.assertEqual(critic_trace.token_output, 96)
        self.assertGreater(planner_trace.latency_ms, 0)
        self.assertGreater(retriever_trace.latency_ms, 0)
        self.assertGreater(verifier_trace.latency_ms, 0)
        self.assertGreater(critic_trace.latency_ms, 0)

    def test_anthropic_agent_estimates_cost_from_model_family_defaults(self):
        agent = AnthropicGraphAgent(api_key="test-key", enabled=True, model="claude-sonnet-4-6")

        self.assertAlmostEqual(
            agent._estimate_cost_usd(input_tokens=1_000_000, output_tokens=1_000_000),
            18.0,
        )
