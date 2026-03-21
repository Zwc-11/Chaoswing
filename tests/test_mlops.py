from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings

from apps.web.models import ExperimentRun, GraphRun, RelatedMarketJudgment
from apps.web.services.mlops import GoldenDatasetEvaluationService


def _golden_payload() -> dict[str, object]:
    return {
        "event": {
            "title": "Fed decision in March",
            "description": "Rates narrative that spills into macro proxies.",
            "tags": ["macro", "rates"],
            "source_url": "https://polymarket.com/event/fed-decision-in-march",
        },
        "graph": {
            "nodes": [
                {
                    "id": "entity_rates",
                    "type": "Entity",
                    "label": "Rate cuts",
                    "summary": "Cuts affect duration and growth leadership.",
                    "metadata": [{"label": "Theme", "value": "rates"}],
                },
                {
                    "id": "rm_1",
                    "type": "RelatedMarket",
                    "label": "How many Fed rate cuts in 2026?",
                    "summary": "Longer-duration rate-cut narrative proxy.",
                    "confidence": 0.92,
                    "source_url": "https://polymarket.com/event/how-many-fed-rate-cuts-in-2026",
                    "metadata": [{"label": "Category", "value": "Macro"}],
                },
                {
                    "id": "rm_2",
                    "type": "RelatedMarket",
                    "label": "Will CPI come in above expectations next release?",
                    "summary": "Inflation-linked follow-through market.",
                    "confidence": 0.71,
                    "source_url": "https://polymarket.com/event/will-cpi-come-in-above-expectations-next-release",
                    "metadata": [{"label": "Category", "value": "Macro"}],
                },
                {
                    "id": "rm_3",
                    "type": "RelatedMarket",
                    "label": "Will there be a presidential debate?",
                    "summary": "Political market with weak macro relevance.",
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


class _FakeActiveRun:
    def __init__(self):
        self.info = SimpleNamespace(
            run_id="fake-run-id",
            artifact_uri="file:///tmp/fake-artifacts",
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeMlflow:
    def __init__(self):
        self.tracking_uri = ""
        self.experiment_name = ""
        self.tags = {}
        self.params = {}
        self.metrics = {}
        self.logged_dicts: list[tuple[str, dict[str, object]]] = []
        self.logged_artifacts: list[tuple[str, str | None]] = []
        self.run_name = ""

    def set_tracking_uri(self, uri: str) -> None:
        self.tracking_uri = uri

    def set_experiment(self, name: str) -> None:
        self.experiment_name = name

    def start_run(self, *, run_name: str):
        self.run_name = run_name
        return _FakeActiveRun()

    def set_tags(self, tags: dict[str, str]) -> None:
        self.tags.update(tags)

    def log_params(self, params: dict[str, str]) -> None:
        self.params.update(params)

    def log_metrics(self, metrics: dict[str, float]) -> None:
        self.metrics.update(metrics)

    def log_dict(self, payload: dict[str, object], artifact_file: str) -> None:
        self.logged_dicts.append((artifact_file, payload))

    def log_artifact(self, local_path: str, artifact_path: str | None = None) -> None:
        self.logged_artifacts.append((local_path, artifact_path))


@override_settings(
    CHAOSWING_ENABLE_REMOTE_FETCH=False,
    CHAOSWING_ENABLE_LLM=False,
    CHAOSWING_RATE_LIMIT_ENABLED=False,
    CHAOSWING_MLFLOW_TRACKING_URI="mlruns-test",
    CHAOSWING_MLFLOW_EXPERIMENT="ChaosWing Test",
)
class GoldenDatasetEvaluationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.run = GraphRun.objects.create(
            source_url="https://polymarket.com/event/fed-decision-in-march",
            event_slug="fed-decision-in-march",
            event_title="Fed decision in March",
            mode="resolved-backend",
            source_snapshot={"category": "Macro", "tags": ["macro", "rates"]},
            graph_stats={"related_markets": 3},
            payload=_golden_payload(),
            workflow_log=[],
        )
        for candidate_key, title, label, reviewer in [
            ("how-many-fed-rate-cuts-in-2026", "How many Fed rate cuts in 2026?", "core", "alice"),
            ("how-many-fed-rate-cuts-in-2026", "How many Fed rate cuts in 2026?", "core", "bob"),
            ("will-cpi-come-in-above-expectations-next-release", "Will CPI come in above expectations next release?", "watch", "alice"),
            ("will-there-be-a-presidential-debate", "Will there be a presidential debate?", "reject", "alice"),
        ]:
            RelatedMarketJudgment.objects.create(
                graph_run=self.run,
                candidate_key=candidate_key,
                candidate_title=title,
                candidate_summary="Unit test judgment",
                candidate_source_url=f"https://example.com/{candidate_key}",
                candidate_rank=1,
                candidate_confidence=0.8,
                usefulness_label=label,
                reviewer=reviewer,
                reviewer_key=reviewer,
                source="unit-test",
            )

    def test_golden_dataset_eval_command_exports_dataset_and_persists_experiment(self):
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "golden.jsonl"
            call_command(
                "run_golden_dataset_eval",
                "--strategy",
                "baseline",
                "--output-path",
                str(output_path),
                stdout=stdout,
            )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["task_type"], "golden_dataset_eval")
            self.assertEqual(payload["evaluation_strategy"], "baseline")
            self.assertTrue(output_path.exists())
            self.assertEqual(payload["dataset_path"], str(output_path))
            self.assertEqual(payload["example_count"], 1)
            self.assertIn("ndcg_at_5", payload["selected_metrics"])
            self.assertTrue(ExperimentRun.objects.filter(task_type="golden_dataset_eval").exists())

    def test_golden_dataset_eval_can_log_to_mlflow(self):
        fake_mlflow = _FakeMlflow()
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = None
            with patch.dict(sys.modules, {"mlflow": fake_mlflow}):
                report = GoldenDatasetEvaluationService(output_dir=Path(tmp_dir)).run(
                    strategy="baseline",
                    persist=False,
                    log_mlflow=True,
                    mlflow_experiment_name="ChaosWing Golden Tests",
                    mlflow_run_name="unit-golden-baseline",
                )

            self.assertIsNotNone(report)
            self.assertEqual(report["mlflow"]["run_id"], "fake-run-id")
            self.assertEqual(fake_mlflow.experiment_name, "ChaosWing Golden Tests")
            self.assertEqual(fake_mlflow.run_name, "unit-golden-baseline")
            self.assertIn("ndcg_at_5", fake_mlflow.metrics)
            self.assertTrue(any(item[0] == "reports/golden_dataset_eval.json" for item in fake_mlflow.logged_dicts))
            self.assertTrue(any(item[1] == "artifacts" for item in fake_mlflow.logged_artifacts))
