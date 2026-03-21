from __future__ import annotations

import io
import json

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings

from apps.web.models import AgentTrace, ExperimentRun, GraphRun
from apps.web.services.market_intelligence import (
    AgentTrustBenchmarkService,
    BenchmarkSummaryService,
    DatasetBuilderService,
)


@override_settings(
    CHAOSWING_ENABLE_REMOTE_FETCH=False,
    CHAOSWING_ENABLE_LLM=False,
    CHAOSWING_RATE_LIMIT_ENABLED=False,
)
class AgentTrustBenchmarkTests(TestCase):
    def setUp(self):
        cache.clear()
        trusted_run = GraphRun.objects.create(
            source_url="https://polymarket.com/event/trusted-market",
            event_slug="trusted-market",
            event_title="Trusted market",
            mode="resolved-backend",
            payload={
                "event": {
                    "title": "Trusted market",
                    "source_url": "https://polymarket.com/event/trusted-market",
                    "description": "Trusted market description",
                },
                "run": {
                    "review": {
                        "approved": True,
                        "issues": [],
                        "follow_up_actions": ["Monitor catalyst"],
                        "quality_score": 0.91,
                    }
                },
                "graph": {
                    "nodes": [
                        {
                            "id": "event",
                            "type": "Event",
                            "label": "Trusted market",
                            "summary": "Event node",
                            "source_url": "https://polymarket.com/event/trusted-market",
                            "metadata": [],
                            "evidence_snippets": [],
                        },
                        {
                            "id": "entity",
                            "type": "Entity",
                            "label": "Fed path",
                            "summary": "Supported entity",
                            "source_url": "",
                            "metadata": [{"label": "Theme", "value": "Rates"}],
                            "evidence_snippets": ["Fed path reprices duration"],
                        },
                    ],
                    "edges": [
                        {
                            "id": "edge-1",
                            "source": "event",
                            "target": "entity",
                            "type": "affects_indirectly",
                            "explanation": "Rate path reprices adjacent assets.",
                        }
                    ],
                },
            },
        )
        AgentTrace.objects.create(
            graph_run=trusted_run,
            stage="retriever",
            status="completed",
            detail="Retrieved related markets.",
            citations=["https://polymarket.com/event/trusted-market"],
        )
        AgentTrace.objects.create(
            graph_run=trusted_run,
            stage="graph_editor",
            status="completed",
            detail="Applied graph edits.",
            citations=["https://polymarket.com/event/trusted-market"],
            latency_ms=220,
            token_input=400,
            token_output=120,
            cost_usd=0.0062,
        )

        weak_run = GraphRun.objects.create(
            source_url="https://polymarket.com/event/weak-market",
            event_slug="weak-market",
            event_title="Weak market",
            mode="deterministic-fallback",
            payload={
                "event": {
                    "title": "Weak market",
                    "source_url": "https://polymarket.com/event/weak-market",
                    "description": "Weak market description",
                },
                "run": {
                    "review": {
                        "approved": False,
                        "issues": ["Unsupported conceptual node"],
                        "follow_up_actions": ["Add supporting evidence"],
                        "quality_score": 0.51,
                    }
                },
                "graph": {
                    "nodes": [
                        {
                            "id": "event",
                            "type": "Event",
                            "label": "Weak market",
                            "summary": "Event node",
                            "source_url": "https://polymarket.com/event/weak-market",
                            "metadata": [],
                            "evidence_snippets": [],
                        },
                        {
                            "id": "hypothesis",
                            "type": "Hypothesis",
                            "label": "Loose narrative",
                            "summary": "Unsupported claim",
                            "source_url": "",
                            "metadata": [],
                            "evidence_snippets": [],
                        },
                    ],
                    "edges": [
                        {
                            "id": "edge-2",
                            "source": "event",
                            "target": "hypothesis",
                            "type": "related_to",
                            "explanation": "",
                        }
                    ],
                },
            },
        )
        AgentTrace.objects.create(
            graph_run=weak_run,
            stage="retriever",
            status="completed",
            detail="Retrieved only the source market.",
            citations=[],
        )
        AgentTrace.objects.create(
            graph_run=weak_run,
            stage="graph_editor",
            status="skipped",
            detail="No graph edits applied.",
            citations=[],
        )

    def test_agent_trust_benchmark_runs_and_upgrades_summary(self):
        report = AgentTrustBenchmarkService().run(persist=True)

        self.assertEqual(report["task_type"], "agent_trust")
        self.assertEqual(report["example_count"], 2)
        self.assertIn("avg_trust_score", report["metrics"])
        self.assertIn("avg_unsupported_claim_rate", report["metrics"])
        self.assertTrue(ExperimentRun.objects.filter(task_type="agent_trust").exists())

        summary = BenchmarkSummaryService().build_cached(force_refresh=True)
        self.assertTrue(
            any(
                benchmark["name"] == "Agent trust benchmark"
                for benchmark in summary["live_benchmarks"]
            )
        )
        self.assertFalse(
            any(
                benchmark["name"] == "Agent trust benchmark"
                for benchmark in summary["next_benchmarks"]
            )
        )

    def test_agent_trust_command_and_dataset_export(self):
        stdout = io.StringIO()

        call_command("run_agent_trust_benchmark", "--no-persist", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        self.assertEqual(payload["task_type"], "agent_trust")
        self.assertEqual(payload["example_count"], 2)

        records = DatasetBuilderService().build_records()
        self.assertIn("agent_trust_examples", records)
        self.assertEqual(len(records["agent_trust_examples"]), 2)
