from __future__ import annotations

import io
import json

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings

from apps.web.models import AgentTrace, ExperimentRun, GraphRun
from apps.web.services.agent_trace_backfill import AgentTraceBackfillService
from apps.web.services.market_intelligence import AgentEvaluationService, BenchmarkSummaryService


@override_settings(
    CHAOSWING_ENABLE_REMOTE_FETCH=False,
    CHAOSWING_ENABLE_LLM=False,
    CHAOSWING_RATE_LIMIT_ENABLED=False,
)
class AgentEvaluationTests(TestCase):
    def setUp(self):
        cache.clear()
        run_one = GraphRun.objects.create(
            source_url="https://polymarket.com/event/fed-decision-in-march",
            event_slug="fed-decision-in-march",
            event_title="Fed decision in March",
            mode="agent-enriched",
            payload={"graph": {"nodes": [], "edges": []}},
        )
        run_two = GraphRun.objects.create(
            source_url="https://polymarket.com/event/crude-oil-by-end-of-march",
            event_slug="crude-oil-by-end-of-march",
            event_title="Crude oil by end of March",
            mode="resolved-backend",
            payload={"graph": {"nodes": [], "edges": []}},
        )

        AgentTrace.objects.create(
            graph_run=run_one,
            stage="retriever",
            status="completed",
            detail="Retriever resolved adjacent markets and source URLs.",
            latency_ms=110,
            token_input=0,
            token_output=0,
            cost_usd=0.0,
            citations=["https://example.com/fed-market"],
            metadata={"execution_mode": "deterministic"},
        )
        AgentTrace.objects.create(
            graph_run=run_one,
            stage="graph_editor",
            status="completed",
            detail="Graph editor applied two node additions.",
            latency_ms=390,
            token_input=520,
            token_output=140,
            cost_usd=0.0103,
            citations=["https://example.com/fed-market", "https://example.com/rates"],
            metadata={"execution_mode": "anthropic", "provider": "anthropic", "model": "claude-test"},
        )
        AgentTrace.objects.create(
            graph_run=run_one,
            stage="planner",
            status="completed",
            detail="Planner found a viable expansion path.",
            latency_ms=420,
            token_input=0,
            token_output=0,
            cost_usd=0.0,
            citations=["https://example.com/fomc"],
            metadata={"execution_mode": "deterministic"},
        )
        AgentTrace.objects.create(
            graph_run=run_one,
            stage="verifier",
            status="fallback",
            detail="Verifier downgraded unsupported spillover path.",
            latency_ms=0,
            token_input=0,
            token_output=0,
            cost_usd=0.0,
            citations=[],
            metadata={"execution_mode": "deterministic"},
        )
        AgentTrace.objects.create(
            graph_run=run_one,
            stage="critic",
            status="completed",
            detail="Critic accepted the final graph with one follow-up.",
            latency_ms=180,
            token_input=240,
            token_output=80,
            cost_usd=0.0049,
            citations=["https://example.com/fomc"],
            metadata={"execution_mode": "anthropic", "provider": "anthropic", "model": "claude-test"},
        )
        AgentTrace.objects.create(
            graph_run=run_two,
            stage="manual_review",
            status="failed",
            detail="Manual review required due to missing evidence.",
            latency_ms=210,
            token_input=220,
            token_output=90,
            cost_usd=0.0061,
            citations=["https://example.com/oil", "https://example.com/cpi"],
            metadata={"execution_mode": "anthropic", "provider": "anthropic", "model": "claude-test"},
        )

    def test_agent_eval_runs_and_upgrades_summary_row(self):
        report = AgentEvaluationService().run(persist=True)

        self.assertEqual(report["task_type"], "agent_eval")
        self.assertEqual(report["title"], "Agent instrumentation coverage report")
        self.assertEqual(report["metrics"]["trace_count"], 6)
        self.assertAlmostEqual(report["metrics"]["run_coverage_rate"], 1.0)
        self.assertAlmostEqual(report["metrics"]["required_stage_coverage_rate"], 0.5, places=4)
        self.assertAlmostEqual(report["metrics"]["citation_coverage_rate"], 5 / 6, places=4)
        self.assertAlmostEqual(report["metrics"]["latency_coverage_rate"], 5 / 6, places=4)
        self.assertAlmostEqual(report["metrics"]["token_coverage_rate"], 1.0, places=4)
        self.assertAlmostEqual(report["metrics"]["cost_coverage_rate"], 1.0, places=4)
        self.assertEqual(report["metrics"]["llm_trace_count"], 3)
        self.assertTrue(ExperimentRun.objects.filter(task_type="agent_eval").exists())

        summary = BenchmarkSummaryService().build_cached(force_refresh=True)
        self.assertTrue(
            any(
                benchmark["name"] == "Agent instrumentation coverage"
                for benchmark in summary["live_benchmarks"]
            )
        )

    def test_agent_eval_command_returns_instrumentation_metrics(self):
        stdout = io.StringIO()

        call_command("run_agent_eval", "--no-persist", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        self.assertEqual(payload["task_type"], "agent_eval")
        self.assertIn("status_breakdown", payload)
        self.assertEqual(payload["metrics"]["trace_count"], 6)
        self.assertIn("required_stage_coverage_rate", payload["metrics"])
        self.assertEqual(payload["metrics"]["llm_trace_count"], 3)
        self.assertGreater(payload["metrics"]["total_tokens"], 0)

    def test_backfill_reconstructs_required_pipeline_stages_from_saved_run(self):
        GraphRun.objects.all().delete()
        run = GraphRun.objects.create(
            source_url="https://polymarket.com/event/fed-decision-in-march",
            event_slug="fed-decision-in-march",
            event_title="Fed decision in March",
            mode="agent-enriched",
            model_name="claude-sonnet-4-6",
            source_snapshot={
                "source_url": "https://polymarket.com/event/fed-decision-in-march",
                "canonical_url": "https://polymarket.com/event/fed-decision-in-march",
                "slug": "fed-decision-in-march",
                "title": "Fed decision in March",
                "description": "Target fed funds range decision market.",
                "status": "open",
                "category": "Macro",
                "tags": ["fed", "rates"],
                "outcomes": ["Yes", "No"],
                "source_kind": "gamma-api",
            },
            payload={
                "event": {
                    "title": "Fed decision in March",
                    "source_url": "https://polymarket.com/event/fed-decision-in-march",
                    "description": "Target fed funds range decision market.",
                    "status": "open",
                    "tags": ["fed", "rates"],
                },
                "graph": {
                    "nodes": [
                        {
                            "id": "event",
                            "label": "Fed decision in March",
                            "type": "Event",
                            "confidence": 0.95,
                            "summary": "The source market.",
                            "source_url": "https://polymarket.com/event/fed-decision-in-march",
                            "source_description": "Target fed funds range decision market.",
                            "icon_key": "event_primary",
                        },
                        {
                            "id": "related-1",
                            "label": "Fed rates in April",
                            "type": "RelatedMarket",
                            "confidence": 0.83,
                            "summary": "Adjacent rates repricing market.",
                            "source_url": "https://polymarket.com/event/fed-rates-in-april",
                            "source_description": "Rates follow-on market.",
                            "icon_key": "source_related_1",
                        },
                        {
                            "id": "evidence-1",
                            "label": "CPI surprise risk",
                            "type": "Evidence",
                            "confidence": 0.71,
                            "summary": "Inflation surprise can move the path.",
                            "source_url": "",
                            "source_description": "Inflation surprise can move the path.",
                            "evidence_snippets": ["CPI release resets cut odds."],
                            "icon_key": "source_related_1",
                        },
                    ],
                    "edges": [
                        {
                            "id": "edge-1",
                            "source": "event",
                            "target": "related-1",
                            "type": "affects_indirectly",
                            "confidence": 0.81,
                            "explanation": "March guidance affects April odds.",
                        }
                    ],
                },
                "run": {
                    "mode": "agent-enriched",
                    "review": {
                        "approved": True,
                        "issues": [],
                        "follow_up_actions": ["Monitor CPI."],
                        "quality_score": 0.91,
                    },
                    "workflow": [
                        {
                            "step": "event_resolution",
                            "status": "completed",
                            "detail": "Resolved source event.",
                        },
                        {
                            "step": "related_market_discovery",
                            "status": "completed",
                            "detail": "Discovered 1 related market.",
                        },
                        {
                            "step": "graph_construction",
                            "status": "completed",
                            "detail": "Constructed graph.",
                        },
                        {
                            "step": "llm_expansion",
                            "status": "completed",
                            "detail": "Expanded graph with one related market.",
                        },
                        {
                            "step": "llm_review",
                            "status": "completed",
                            "detail": "Reviewed graph output.",
                        },
                        {
                            "step": "payload_validation",
                            "status": "completed",
                            "detail": "Validated payload.",
                        },
                    ],
                },
                "context": {
                    "source_snapshot": {
                        "source_url": "https://polymarket.com/event/fed-decision-in-march",
                        "canonical_url": "https://polymarket.com/event/fed-decision-in-march",
                        "slug": "fed-decision-in-march",
                        "title": "Fed decision in March",
                        "description": "Target fed funds range decision market.",
                        "status": "open",
                        "category": "Macro",
                        "tags": ["fed", "rates"],
                        "outcomes": ["Yes", "No"],
                        "source_kind": "gamma-api",
                    },
                    "related_candidates": [
                        {
                            "snapshot": {
                                "source_url": "https://polymarket.com/event/fed-rates-in-april",
                                "canonical_url": "https://polymarket.com/event/fed-rates-in-april",
                                "slug": "fed-rates-in-april",
                                "title": "Fed rates in April",
                                "description": "Follow-on rates market.",
                                "status": "open",
                                "category": "Macro",
                                "tags": ["fed", "rates"],
                                "outcomes": ["Yes", "No"],
                                "source_kind": "gamma-api",
                            },
                            "confidence": 0.83,
                            "rationale": "Shared macro catalyst.",
                            "shared_tags": ["fed"],
                            "shared_terms": ["rates"],
                        }
                    ],
                },
            },
            workflow_log=[
                {"step": "llm_expansion", "status": "completed", "detail": "Expanded graph."},
                {"step": "llm_review", "status": "completed", "detail": "Reviewed graph."},
            ],
        )
        AgentTrace.objects.create(
            graph_run=run,
            stage="llm_expansion",
            status="completed",
            detail="Expanded graph.",
            latency_ms=420,
            token_input=640,
            token_output=180,
            cost_usd=0.0124,
            citations=["https://polymarket.com/event/fed-rates-in-april"],
            metadata={"provider": "anthropic", "model": "claude-sonnet-4-6"},
        )
        AgentTrace.objects.create(
            graph_run=run,
            stage="llm_review",
            status="completed",
            detail="Reviewed graph.",
            latency_ms=160,
            token_input=180,
            token_output=60,
            cost_usd=0.0041,
            citations=[],
            metadata={"provider": "anthropic", "model": "claude-sonnet-4-6"},
        )

        report = AgentTraceBackfillService().run()

        self.assertEqual(report["runs_with_new_stage_traces"], 1)
        self.assertEqual(report["stage_traces_created"], 5)
        self.assertEqual(report["stages_created"]["planner"], 1)

        run.refresh_from_db()
        self.assertEqual(
            [item["stage"] for item in run.payload["run"]["agent_pipeline"]],
            ["planner", "retriever", "graph_editor", "critic", "verifier"],
        )
        stage_traces = {
            trace.stage: trace for trace in AgentTrace.objects.filter(graph_run=run)
        }
        self.assertIn("planner", stage_traces)
        self.assertIn("retriever", stage_traces)
        self.assertIn("graph_editor", stage_traces)
        self.assertIn("critic", stage_traces)
        self.assertIn("verifier", stage_traces)
        self.assertEqual(stage_traces["graph_editor"].token_input, 640)
        self.assertEqual(stage_traces["critic"].token_output, 60)
        self.assertTrue(stage_traces["retriever"].citations)

        eval_report = AgentEvaluationService().run(persist=False)
        self.assertAlmostEqual(eval_report["metrics"]["required_stage_coverage_rate"], 1.0)

    def test_agent_eval_command_can_backfill_before_scoring(self):
        GraphRun.objects.all().delete()
        GraphRun.objects.create(
            source_url="https://polymarket.com/event/simple-market",
            event_slug="simple-market",
            event_title="Simple market",
            mode="deterministic-fallback",
            payload={
                "event": {
                    "title": "Simple market",
                    "source_url": "https://polymarket.com/event/simple-market",
                    "description": "Simple test market.",
                    "status": "open",
                },
                "graph": {
                    "nodes": [
                        {
                            "id": "event",
                            "label": "Simple market",
                            "type": "Event",
                            "confidence": 0.8,
                            "summary": "Simple test market.",
                            "source_url": "https://polymarket.com/event/simple-market",
                            "source_description": "Simple test market.",
                            "icon_key": "event_primary",
                        }
                    ],
                    "edges": [],
                },
                "run": {
                    "review": {
                        "approved": False,
                        "issues": ["No related markets."],
                        "follow_up_actions": ["Collect more context."],
                        "quality_score": 0.62,
                    }
                },
            },
        )

        stdout = io.StringIO()
        call_command("run_agent_eval", "--no-persist", "--backfill-missing", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        self.assertIn("backfill", payload)
        self.assertEqual(payload["backfill"]["runs_with_new_stage_traces"], 1)
        self.assertAlmostEqual(payload["metrics"]["required_stage_coverage_rate"], 1.0)

    def test_backfill_is_idempotent_for_required_stage_traces(self):
        GraphRun.objects.all().delete()
        run = GraphRun.objects.create(
            source_url="https://polymarket.com/event/idempotent-stage-market",
            event_slug="idempotent-stage-market",
            event_title="Idempotent stage market",
            mode="deterministic-fallback",
            payload={
                "event": {
                    "title": "Idempotent stage market",
                    "source_url": "https://polymarket.com/event/idempotent-stage-market",
                    "description": "Test event.",
                    "status": "open",
                },
                "graph": {
                    "nodes": [
                        {
                            "id": "event",
                            "label": "Idempotent stage market",
                            "type": "Event",
                            "confidence": 0.8,
                            "summary": "Test event.",
                            "source_url": "https://polymarket.com/event/idempotent-stage-market",
                            "source_description": "Test event.",
                            "icon_key": "event_primary",
                        }
                    ],
                    "edges": [],
                },
                "run": {
                    "review": {
                        "approved": False,
                        "issues": ["No related markets."],
                        "follow_up_actions": ["Collect more context."],
                        "quality_score": 0.62,
                    }
                },
            },
        )

        service = AgentTraceBackfillService()
        first = service.run()
        second = service.run()

        self.assertEqual(first["runs_with_new_stage_traces"], 1)
        self.assertEqual(first["stage_traces_created"], 5)
        self.assertEqual(second["runs_with_new_stage_traces"], 0)
        self.assertEqual(
            AgentTrace.objects.filter(graph_run=run, stage__in=["planner", "retriever", "graph_editor", "critic", "verifier"]).count(),
            5,
        )

    def test_backfill_repairs_missing_llm_cost_metadata(self):
        GraphRun.objects.all().delete()
        run = GraphRun.objects.create(
            source_url="https://polymarket.com/event/cost-repair-market",
            event_slug="cost-repair-market",
            event_title="Cost repair market",
            mode="agent-enriched",
            model_name="claude-sonnet-4-6",
            payload={"graph": {"nodes": [], "edges": []}},
        )
        trace = AgentTrace.objects.create(
            graph_run=run,
            stage="graph_editor",
            status="completed",
            detail="Legacy editor trace without cost estimate.",
            latency_ms=220,
            token_input=1000,
            token_output=250,
            cost_usd=0.0,
            citations=["https://polymarket.com/event/cost-repair-market"],
            metadata={},
        )

        report = AgentTraceBackfillService().run()

        trace.refresh_from_db()
        self.assertEqual(report["trace_repairs"], 1)
        self.assertEqual(report["runs_with_trace_repairs"], 1)
        self.assertEqual(trace.metadata["execution_mode"], "anthropic")
        self.assertEqual(trace.metadata["model"], "claude-sonnet-4-6")
        self.assertEqual(trace.metadata["provider"], "anthropic")
        self.assertGreater(trace.cost_usd, 0.0)
