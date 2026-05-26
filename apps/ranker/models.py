"""Persistence layer for the reranker pipeline.

The build plan (§2) lists four models; this file is the entire schema. Keep
it small — these tables exist to make pipeline runs queryable and to back
benchmarks, not to mirror every JSONL field.

Heavy artifacts (checkpoints, embedding indexes) live on disk under
`models/` and `ml_data/`. The rows here just point at them.
"""
from __future__ import annotations

from django.db import models


class RelevanceLabel(models.Model):
    """One mined label: a (source, candidate) market pair with a 0-3 grade.

    Mirrors `ml_data/relevance_labels.jsonl`. We persist the same row to the
    DB so that splits, benchmarks, and the admin can join against it without
    re-parsing the JSONL.
    """

    RELEVANCE_CHOICES = [
        (0, "unrelated"),
        (1, "weak"),
        (2, "related"),
        (3, "strong"),
    ]
    NEGATIVE_KIND_CHOICES = [
        ("positive", "positive"),
        ("hard", "hard negative"),
        ("easy", "easy negative"),
        ("", "unspecified"),
    ]

    source_market_id = models.CharField(max_length=160, db_index=True)
    candidate_market_id = models.CharField(max_length=160, db_index=True)
    event_family = models.CharField(max_length=160, db_index=True, blank=True)

    relevance = models.PositiveSmallIntegerField(choices=RELEVANCE_CHOICES, db_index=True)
    negative_kind = models.CharField(
        max_length=16, choices=NEGATIVE_KIND_CHOICES, default="", blank=True, db_index=True
    )

    max_xcorr = models.FloatField(default=0.0)
    best_lag_seconds = models.IntegerField(default=0)
    granger_p = models.FloatField(null=True, blank=True)
    shock_co_fraction = models.FloatField(default=0.0)

    source_first_seen = models.DateTimeField(null=True, blank=True)
    candidate_first_seen = models.DateTimeField(null=True, blank=True)
    window_start = models.DateTimeField(null=True, blank=True)
    window_end = models.DateTimeField(null=True, blank=True, db_index=True)

    mining_cutoff = models.DateTimeField(db_index=True)
    mining_run_id = models.CharField(max_length=64, db_index=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_market_id", "candidate_market_id", "mining_run_id"],
                name="ranker_relevance_label_pair_run_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["event_family", "relevance"],
                name="ranker_label_fam_rel_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.source_market_id}->{self.candidate_market_id} [{self.relevance}]"


class TemporalSplit(models.Model):
    """A named train/val/test split, computed from a config and a label set.

    `family_assignments` is a dict `{event_family: split_name}`. Storing the
    full assignment makes a split reproducible and queryable without re-running
    the splitter.
    """

    name = models.CharField(max_length=80, unique=True)
    train_cutoff = models.DateTimeField()
    val_cutoff = models.DateTimeField()
    mining_run_id = models.CharField(max_length=64, db_index=True, blank=True)
    family_assignments = models.JSONField(default=dict, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"split:{self.name} train<{self.train_cutoff.isoformat()}"


class RankingExample(models.Model):
    """A materialized training row: a source plus its labelled candidate list.

    Listwise losses need the full per-source candidate list at training time;
    persisting it once avoids re-joining every epoch. `candidates` is a list
    of `{"candidate_market_id": str, "relevance": int, "negative_kind": str}`
    dicts ordered however the sampler chose.
    """

    SPLIT_CHOICES = [("train", "train"), ("val", "val"), ("test", "test")]

    split = models.ForeignKey(TemporalSplit, on_delete=models.CASCADE, related_name="examples")
    split_name = models.CharField(max_length=8, choices=SPLIT_CHOICES, db_index=True)
    source_market_id = models.CharField(max_length=160, db_index=True)
    event_family = models.CharField(max_length=160, db_index=True, blank=True)
    candidates = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["split_name", "source_market_id"]
        indexes = [
            models.Index(
                fields=["split", "split_name"],
                name="ranker_example_split_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.split_name}:{self.source_market_id} ({len(self.candidates)} cands)"


class RerankerRun(models.Model):
    """One end-to-end ranker evaluation: method, split, metrics, optional checkpoint.

    Both training runs and pure-inference runs (baselines, RankGPT) write rows
    here so the benchmark table is a single `SELECT ... ORDER BY ndcg_at_5 DESC`.
    """

    KIND_CHOICES = [
        ("training", "training run"),
        ("inference", "inference / baseline run"),
        ("probe", "forecasting probe"),
    ]

    method = models.CharField(max_length=80, db_index=True)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default="inference", db_index=True)
    split = models.ForeignKey(
        TemporalSplit,
        on_delete=models.SET_NULL,
        related_name="runs",
        null=True,
        blank=True,
    )
    metrics = models.JSONField(default=dict, blank=True)
    config = models.JSONField(default=dict, blank=True)
    artifact_path = models.CharField(max_length=300, blank=True)
    mlflow_run_id = models.CharField(max_length=64, blank=True, db_index=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["method", "kind"], name="ranker_run_method_kind_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.method} [{self.kind}] {self.created_at:%Y-%m-%d}"
