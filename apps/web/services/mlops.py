from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from django.conf import settings

from apps.web.models import ExperimentRun, GraphRun, RelatedMarketJudgment

from .market_intelligence import RelatedMarketUsefulnessBenchmarkService


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _numeric_items(payload: dict[str, Any]) -> dict[str, float]:
    numeric: dict[str, float] = {}
    for key, value in payload.items():
        if isinstance(value, bool):
            numeric[key] = float(value)
        elif isinstance(value, (int, float)):
            numeric[key] = float(value)
    return numeric


def _stringified_items(payload: dict[str, Any]) -> dict[str, str]:
    params: dict[str, str] = {}
    for key, value in payload.items():
        if value in (None, ""):
            continue
        if isinstance(value, (str, int, float, bool)):
            params[key] = str(value)
        else:
            params[key] = json.dumps(value, sort_keys=True)
    return params


@dataclass(slots=True)
class MlflowRunRecord:
    tracking_uri: str
    experiment_name: str
    run_id: str
    artifact_uri: str
    run_name: str

    def as_dict(self) -> dict[str, str]:
        return {
            "tracking_uri": self.tracking_uri,
            "experiment_name": self.experiment_name,
            "run_id": self.run_id,
            "artifact_uri": self.artifact_uri,
            "run_name": self.run_name,
        }


class MlflowTrackingService:
    def __init__(
        self,
        *,
        tracking_uri: str | None = None,
        experiment_name: str | None = None,
    ):
        self.tracking_uri = str(
            tracking_uri or getattr(settings, "CHAOSWING_MLFLOW_TRACKING_URI", "mlruns")
        ).strip()
        self.experiment_name = str(
            experiment_name or getattr(settings, "CHAOSWING_MLFLOW_EXPERIMENT", "ChaosWing")
        ).strip() or "ChaosWing"

    def log_report(
        self,
        *,
        report: dict[str, Any],
        run_name: str,
        params: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        tags: dict[str, Any] | None = None,
        artifact_paths: list[Path] | None = None,
    ) -> MlflowRunRecord:
        mlflow = self._import_mlflow()
        normalized_tracking_uri = self._normalized_tracking_uri(self.tracking_uri)
        mlflow.set_tracking_uri(normalized_tracking_uri)
        mlflow.set_experiment(self.experiment_name)

        with mlflow.start_run(run_name=run_name) as active_run:
            clean_tags = _stringified_items(tags or {})
            if clean_tags:
                mlflow.set_tags(clean_tags)

            clean_params = _stringified_items(params or {})
            if clean_params:
                mlflow.log_params(clean_params)

            clean_metrics = _numeric_items(metrics or {})
            if clean_metrics:
                mlflow.log_metrics(clean_metrics)

            self._log_dict(mlflow, report, artifact_file="reports/golden_dataset_eval.json")
            for artifact_path in artifact_paths or []:
                path = Path(artifact_path)
                if path.exists():
                    mlflow.log_artifact(str(path), artifact_path="artifacts")

            run_info = active_run.info
            return MlflowRunRecord(
                tracking_uri=normalized_tracking_uri,
                experiment_name=self.experiment_name,
                run_id=str(getattr(run_info, "run_id", "")),
                artifact_uri=str(getattr(run_info, "artifact_uri", "")),
                run_name=run_name,
            )

    def _import_mlflow(self):
        try:
            return importlib.import_module("mlflow")
        except ImportError as exc:
            raise RuntimeError(
                "MLflow is not installed. Install it with `python -m pip install -e \".[mlops]\"` "
                "or `python -m pip install mlflow`."
            ) from exc

    def _log_dict(self, mlflow: Any, payload: dict[str, Any], *, artifact_file: str) -> None:
        if hasattr(mlflow, "log_dict"):
            mlflow.log_dict(payload, artifact_file)
            return

        fallback_path = Path("ml_data") / artifact_file
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        mlflow.log_artifact(str(fallback_path), artifact_path="reports")

    def _normalized_tracking_uri(self, value: str) -> str:
        raw_value = str(value or "").strip()
        if not raw_value:
            return Path("mlruns").resolve().as_uri()
        if raw_value.lower().startswith("file:"):
            return raw_value
        parsed = urlparse(raw_value)
        if parsed.scheme and not (len(parsed.scheme) == 1 and raw_value[1:3] in {":\\", ":/"}):
            return raw_value
        return Path(raw_value).expanduser().resolve().as_uri()


class GoldenDatasetEvaluationService:
    DATASET_NAME = "related_market_usefulness"
    TITLE = "Golden dataset related-market evaluation"
    STRATEGY_LABELS = {
        "baseline": "lexical_baseline",
        "model": "context_reranker",
        "compare": "baseline_vs_context_reranker",
    }

    def __init__(
        self,
        *,
        output_dir: Path | None = None,
        min_judged_candidates: int = 3,
        min_reviewers_per_candidate: int = 1,
        mlflow_service: MlflowTrackingService | None = None,
    ):
        self.output_dir = output_dir or Path("ml_data")
        self.benchmark_service = RelatedMarketUsefulnessBenchmarkService(
            min_judged_candidates=max(int(min_judged_candidates), 2),
            min_reviewers_per_candidate=max(int(min_reviewers_per_candidate), 1),
        )
        self.mlflow_service = mlflow_service or MlflowTrackingService()

    def run(
        self,
        *,
        strategy: str = "baseline",
        persist: bool = True,
        log_mlflow: bool = False,
        mlflow_tracking_uri: str = "",
        mlflow_experiment_name: str = "",
        mlflow_run_name: str = "",
        output_path: Path | None = None,
    ) -> dict[str, Any]:
        normalized_strategy = str(strategy or "baseline").strip().lower()
        if normalized_strategy not in self.STRATEGY_LABELS:
            raise ValueError("Strategy must be baseline, model, or compare.")

        examples = self.benchmark_service.export_examples()
        dataset_path = self._write_dataset(
            examples,
            output_path=output_path,
        )
        benchmark_report = self.benchmark_service.run(persist=False)
        selected_metrics = self._selected_metrics(
            benchmark_report.get("metrics") or {},
            strategy=normalized_strategy,
        )
        dataset_version = (
            f"judgments:{RelatedMarketJudgment.objects.count()}"
            f"|runs:{GraphRun.objects.count()}"
            f"|examples:{len(examples)}"
        )
        report = {
            "task_type": "golden_dataset_eval",
            "title": self.TITLE,
            "dataset_name": self.DATASET_NAME,
            "dataset_version": dataset_version,
            "dataset_path": str(dataset_path),
            "example_count": len(examples),
            "judgment_count": RelatedMarketJudgment.objects.count(),
            "evaluation_strategy": normalized_strategy,
            "strategy_label": self.STRATEGY_LABELS[normalized_strategy],
            "selected_metrics": selected_metrics,
            "benchmark_metrics": benchmark_report.get("metrics") or {},
            "source_benchmark_task": benchmark_report.get("task_type") or "",
            "mlflow": None,
        }

        if persist:
            ExperimentRun.objects.create(
                task_type="golden_dataset_eval",
                title=self.TITLE,
                dataset_version=dataset_version,
                metrics=selected_metrics,
                artifacts={
                    "dataset_name": self.DATASET_NAME,
                    "dataset_path": str(dataset_path),
                    "evaluation_strategy": normalized_strategy,
                    "benchmark_metrics": benchmark_report.get("metrics") or {},
                    "example_preview": examples[:10],
                },
                notes=(
                    "Golden dataset evaluation over the human-labeled related-market usefulness set. "
                    "The lexical baseline can be tracked without a trained ML model, while the same "
                    "dataset can later compare against the context-aware reranker."
                ),
            )

        if log_mlflow:
            run_name = mlflow_run_name.strip() or (
                f"golden-{self.STRATEGY_LABELS[normalized_strategy]}-{len(examples)}examples"
            )
            tracking_service = MlflowTrackingService(
                tracking_uri=mlflow_tracking_uri.strip() or self.mlflow_service.tracking_uri,
                experiment_name=mlflow_experiment_name.strip() or self.mlflow_service.experiment_name,
            )
            mlflow_record = tracking_service.log_report(
                report=report,
                run_name=run_name,
                params={
                    "dataset_name": self.DATASET_NAME,
                    "dataset_version": dataset_version,
                    "evaluation_strategy": normalized_strategy,
                    "min_judged_candidates": self.benchmark_service.min_judged_candidates,
                    "min_reviewers_per_candidate": self.benchmark_service.min_reviewers_per_candidate,
                },
                metrics=selected_metrics,
                tags={
                    "chaoswing.task_type": "golden_dataset_eval",
                    "chaoswing.dataset_name": self.DATASET_NAME,
                    "chaoswing.strategy": self.STRATEGY_LABELS[normalized_strategy],
                },
                artifact_paths=[dataset_path],
            )
            report["mlflow"] = mlflow_record.as_dict()

        return report

    def _write_dataset(self, examples: list[dict[str, Any]], *, output_path: Path | None = None) -> Path:
        path = output_path or (self.output_dir / "golden_related_market_usefulness.jsonl")
        _write_jsonl(path, examples)
        return path

    def _selected_metrics(self, metrics: dict[str, Any], *, strategy: str) -> dict[str, float]:
        if strategy == "baseline":
            return {
                "recall_at_3": float(metrics.get("baseline_recall_at_3") or 0.0),
                "ndcg_at_5": float(metrics.get("baseline_ndcg_at_5") or 0.0),
                "mrr": float(metrics.get("baseline_mrr") or 0.0),
                "example_count": float(metrics.get("example_count") or 0.0),
                "avg_candidate_count": float(metrics.get("avg_candidate_count") or 0.0),
            }
        if strategy == "model":
            return {
                "recall_at_3": float(metrics.get("model_recall_at_3") or 0.0),
                "ndcg_at_5": float(metrics.get("model_ndcg_at_5") or 0.0),
                "mrr": float(metrics.get("model_mrr") or 0.0),
                "example_count": float(metrics.get("example_count") or 0.0),
                "avg_candidate_count": float(metrics.get("avg_candidate_count") or 0.0),
            }
        return {
            "baseline_recall_at_3": float(metrics.get("baseline_recall_at_3") or 0.0),
            "model_recall_at_3": float(metrics.get("model_recall_at_3") or 0.0),
            "recall_at_3_lift": float(metrics.get("recall_at_3_lift") or 0.0),
            "baseline_ndcg_at_5": float(metrics.get("baseline_ndcg_at_5") or 0.0),
            "model_ndcg_at_5": float(metrics.get("model_ndcg_at_5") or 0.0),
            "ndcg_at_5_lift": float(metrics.get("ndcg_at_5_lift") or 0.0),
            "baseline_mrr": float(metrics.get("baseline_mrr") or 0.0),
            "model_mrr": float(metrics.get("model_mrr") or 0.0),
            "mrr_lift": float(metrics.get("mrr_lift") or 0.0),
            "example_count": float(metrics.get("example_count") or 0.0),
        }
