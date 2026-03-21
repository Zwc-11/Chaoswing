from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.web.services.mlops import GoldenDatasetEvaluationService


class Command(BaseCommand):
    help = (
        "Evaluate the human-labeled related-market golden dataset, optionally persist the result "
        "as an ExperimentRun, and optionally log the run to MLflow."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-persist",
            action="store_true",
            help="Compute the report without creating a golden_dataset_eval ExperimentRun row.",
        )
        parser.add_argument(
            "--strategy",
            choices=["baseline", "model", "compare"],
            default="baseline",
            help=(
                "Which scoring path to treat as the headline evaluation. "
                "Use baseline for the non-ML lexical test, model for the context-aware reranker, "
                "or compare to log both plus lift."
            ),
        )
        parser.add_argument(
            "--min-judged-candidates",
            type=int,
            default=3,
            help="Minimum number of judged candidates required per run before a case enters the golden dataset.",
        )
        parser.add_argument(
            "--min-reviewers-per-candidate",
            type=int,
            default=1,
            help="Minimum number of reviewer labels required before a candidate enters the golden dataset.",
        )
        parser.add_argument(
            "--output-path",
            default="",
            help="Optional JSONL path for the exported golden dataset. Defaults to ml_data/golden_related_market_usefulness.jsonl.",
        )
        parser.add_argument(
            "--log-mlflow",
            action="store_true",
            help="Log the run to MLflow using local defaults or environment overrides.",
        )
        parser.add_argument(
            "--mlflow-experiment",
            default="",
            help="Override the MLflow experiment name for this run only.",
        )
        parser.add_argument(
            "--mlflow-tracking-uri",
            default="",
            help="Optional MLflow tracking URI override, for example sqlite:///mlflow.db.",
        )
        parser.add_argument(
            "--mlflow-run-name",
            default="",
            help="Optional MLflow run name override.",
        )

    def handle(self, *args, **options):
        output_path = str(options["output_path"] or "").strip()
        service = GoldenDatasetEvaluationService(
            min_judged_candidates=max(int(options["min_judged_candidates"]), 2),
            min_reviewers_per_candidate=max(int(options["min_reviewers_per_candidate"]), 1),
        )
        try:
            report = service.run(
                strategy=options["strategy"],
                persist=not options["no_persist"],
                log_mlflow=bool(options["log_mlflow"]),
                mlflow_tracking_uri=str(options["mlflow_tracking_uri"] or ""),
                mlflow_experiment_name=str(options["mlflow_experiment"] or ""),
                mlflow_run_name=str(options["mlflow_run_name"] or ""),
                output_path=Path(output_path) if output_path else None,
            )
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(json.dumps(report, indent=2))
