from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q


class GraphRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_url = models.URLField()
    event_slug = models.CharField(max_length=300, blank=True)
    event_title = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=32, default="completed")
    mode = models.CharField(max_length=64, default="deterministic-fallback")
    model_name = models.CharField(max_length=120, blank=True)
    source_snapshot = models.JSONField(default=dict, blank=True)
    graph_stats = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    workflow_log = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        title = self.event_title or self.source_url
        return f"{title} [{self.mode}]"


class Watchlist(models.Model):
    slug = models.SlugField(max_length=120, unique=True)
    title = models.CharField(max_length=140)
    thesis = models.CharField(max_length=240)
    summary = models.TextField(blank=True)
    cadence = models.CharField(max_length=80, blank=True)
    items = models.JSONField(default=list, blank=True)
    is_featured = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self) -> str:
        return self.title


class MarketSnapshot(models.Model):
    graph_run = models.ForeignKey(
        GraphRun,
        on_delete=models.CASCADE,
        related_name="snapshots",
        null=True,
        blank=True,
    )
    source_url = models.URLField()
    event_slug = models.CharField(max_length=300, db_index=True)
    event_title = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=32, default="open")
    category = models.CharField(max_length=120, blank=True)
    source_kind = models.CharField(max_length=64, blank=True)
    tags = models.JSONField(default=list, blank=True)
    outcomes = models.JSONField(default=list, blank=True)
    implied_probability = models.FloatField(default=0.0)
    volume = models.FloatField(default=0.0)
    liquidity = models.FloatField(default=0.0)
    open_interest = models.FloatField(default=0.0)
    related_market_count = models.PositiveIntegerField(default=0)
    evidence_count = models.PositiveIntegerField(default=0)
    snapshot_at = models.DateTimeField(db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-snapshot_at", "-created_at"]

    def __str__(self) -> str:
        return f"{self.event_title or self.event_slug} @ {self.snapshot_at.isoformat()}"


class ResolutionLabel(models.Model):
    market_snapshot = models.OneToOneField(
        MarketSnapshot,
        on_delete=models.CASCADE,
        related_name="resolution_label",
    )
    event_slug = models.CharField(max_length=300, db_index=True)
    resolved_outcome = models.CharField(max_length=120)
    resolved_probability = models.FloatField(default=0.0)
    source = models.CharField(max_length=64, default="outcome_prices")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.event_slug}: {self.resolved_outcome}"


class ExperimentRun(models.Model):
    task_type = models.CharField(max_length=64, db_index=True)
    title = models.CharField(max_length=160)
    dataset_version = models.CharField(max_length=80, blank=True)
    code_version = models.CharField(max_length=80, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    artifacts = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.task_type}: {self.title}"


class RelatedMarketJudgment(models.Model):
    graph_run = models.ForeignKey(
        GraphRun,
        on_delete=models.CASCADE,
        related_name="related_market_judgments",
    )
    candidate_key = models.CharField(max_length=300)
    candidate_title = models.CharField(max_length=300)
    candidate_summary = models.TextField(blank=True)
    candidate_source_url = models.URLField(blank=True)
    candidate_rank = models.PositiveIntegerField(default=0)
    candidate_confidence = models.FloatField(default=0.0)
    usefulness_label = models.CharField(max_length=24, db_index=True, default="watch")
    notes = models.TextField(blank=True)
    reviewer = models.CharField(max_length=80, blank=True)
    reviewer_key = models.CharField(max_length=80, db_index=True, default="anonymous")
    source = models.CharField(max_length=64, default="manual-web")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "candidate_rank", "candidate_title"]
        constraints = [
            models.UniqueConstraint(
                fields=["graph_run", "candidate_key", "reviewer_key"],
                name="web_related_market_judgment_reviewer_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.graph_run_id}:{self.candidate_title} [{self.usefulness_label}]"


class AgentTrace(models.Model):
    graph_run = models.ForeignKey(
        GraphRun,
        on_delete=models.CASCADE,
        related_name="agent_traces",
    )
    stage = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=32, default="completed")
    detail = models.TextField(blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    token_input = models.PositiveIntegerField(default=0)
    token_output = models.PositiveIntegerField(default=0)
    cost_usd = models.FloatField(default=0.0)
    citations = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["graph_run", "stage"],
                condition=Q(
                    stage__in=["planner", "retriever", "graph_editor", "critic", "verifier"]
                ),
                name="web_agent_trace_required_stage_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.graph_run_id} [{self.stage}:{self.status}]"


class CrossVenueMarketMap(models.Model):
    venue = models.CharField(max_length=24, db_index=True)
    market_id = models.CharField(max_length=160)
    market_slug = models.CharField(max_length=240, blank=True)
    event_slug = models.CharField(max_length=300, blank=True, db_index=True)
    title = models.CharField(max_length=300)
    url = models.URLField(blank=True)
    category = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=32, default="open")
    outcome_type = models.CharField(max_length=32, default="binary")
    tags = models.JSONField(default=list, blank=True)
    resolution_text = models.TextField(blank=True)
    resolution_window = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["venue", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["venue", "market_id"],
                name="web_crossvenue_market_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.venue}:{self.market_id} - {self.title}"


class MarketEventTick(models.Model):
    venue = models.CharField(max_length=24, db_index=True)
    market_map = models.ForeignKey(
        CrossVenueMarketMap,
        on_delete=models.SET_NULL,
        related_name="event_ticks",
        null=True,
        blank=True,
    )
    market_id = models.CharField(max_length=160, db_index=True)
    market_slug = models.CharField(max_length=240, blank=True)
    event_type = models.CharField(max_length=32, default="ticker")
    status = models.CharField(max_length=32, blank=True)
    exchange_timestamp = models.DateTimeField(db_index=True)
    received_at = models.DateTimeField(db_index=True)
    sequence_id = models.CharField(max_length=120, blank=True)
    last_price = models.FloatField(default=0.0)
    yes_bid = models.FloatField(default=0.0)
    yes_ask = models.FloatField(default=0.0)
    no_bid = models.FloatField(default=0.0)
    no_ask = models.FloatField(default=0.0)
    bid_size = models.FloatField(default=0.0)
    ask_size = models.FloatField(default=0.0)
    trade_size = models.FloatField(default=0.0)
    volume = models.FloatField(default=0.0)
    open_interest = models.FloatField(default=0.0)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-exchange_timestamp", "-created_at"]
        indexes = [
            models.Index(
                fields=["venue", "market_id", "exchange_timestamp"],
                name="web_tick_market_time_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.venue}:{self.market_id} @{self.exchange_timestamp.isoformat()}"


class OrderBookLevelSnapshot(models.Model):
    venue = models.CharField(max_length=24, db_index=True)
    market_map = models.ForeignKey(
        CrossVenueMarketMap,
        on_delete=models.SET_NULL,
        related_name="orderbook_snapshots",
        null=True,
        blank=True,
    )
    tick = models.ForeignKey(
        MarketEventTick,
        on_delete=models.SET_NULL,
        related_name="orderbook_snapshots",
        null=True,
        blank=True,
    )
    market_id = models.CharField(max_length=160, db_index=True)
    captured_at = models.DateTimeField(db_index=True)
    best_yes_bid = models.FloatField(default=0.0)
    best_yes_ask = models.FloatField(default=0.0)
    best_no_bid = models.FloatField(default=0.0)
    best_no_ask = models.FloatField(default=0.0)
    total_bid_depth = models.FloatField(default=0.0)
    total_ask_depth = models.FloatField(default=0.0)
    bids = models.JSONField(default=list, blank=True)
    asks = models.JSONField(default=list, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-captured_at", "-created_at"]
        indexes = [
            models.Index(
                fields=["venue", "market_id", "captured_at"],
                name="web_book_market_time_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.venue}:{self.market_id} book @{self.captured_at.isoformat()}"


class LeadLagPair(models.Model):
    pair_type = models.CharField(max_length=32, db_index=True)
    leader_market = models.ForeignKey(
        CrossVenueMarketMap,
        on_delete=models.CASCADE,
        related_name="lead_pairs",
    )
    follower_market = models.ForeignKey(
        CrossVenueMarketMap,
        on_delete=models.CASCADE,
        related_name="follow_pairs",
    )
    semantic_score = models.FloatField(default=0.0)
    causal_score = models.FloatField(default=0.0)
    resolution_score = models.FloatField(default=0.0)
    stability_score = models.FloatField(default=0.0)
    composite_score = models.FloatField(default=0.0)
    expected_latency_seconds = models.PositiveIntegerField(default=0)
    direction_reason = models.TextField(blank=True)
    is_trade_eligible = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-composite_score", "pair_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["leader_market", "follower_market", "pair_type"],
                name="web_leadlag_pair_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.leader_market_id}->{self.follower_market_id} [{self.pair_type}]"


class LeadLagSignal(models.Model):
    pair = models.ForeignKey(
        LeadLagPair,
        on_delete=models.CASCADE,
        related_name="signals",
    )
    leader_tick = models.ForeignKey(
        MarketEventTick,
        on_delete=models.SET_NULL,
        related_name="leader_signals",
        null=True,
        blank=True,
    )
    follower_tick = models.ForeignKey(
        MarketEventTick,
        on_delete=models.SET_NULL,
        related_name="follower_signals",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=32, default="candidate", db_index=True)
    signal_direction = models.CharField(max_length=24, default="buy_yes")
    leader_price_move = models.FloatField(default=0.0)
    follower_gap = models.FloatField(default=0.0)
    expected_edge = models.FloatField(default=0.0)
    cost_estimate = models.FloatField(default=0.0)
    latency_ms = models.PositiveIntegerField(default=0)
    liquidity_score = models.FloatField(default=0.0)
    rationale = models.TextField(blank=True)
    no_trade_reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Signal {self.id} [{self.status}]"


class PaperTrade(models.Model):
    signal = models.ForeignKey(
        LeadLagSignal,
        on_delete=models.CASCADE,
        related_name="paper_trades",
    )
    status = models.CharField(max_length=32, default="open", db_index=True)
    side = models.CharField(max_length=24, default="buy_yes")
    quantity = models.FloatField(default=1.0)
    entry_price = models.FloatField(default=0.0)
    exit_price = models.FloatField(default=0.0)
    gross_pnl = models.FloatField(default=0.0)
    net_pnl = models.FloatField(default=0.0)
    fee_paid = models.FloatField(default=0.0)
    slippage_paid = models.FloatField(default=0.0)
    max_adverse_excursion = models.FloatField(default=0.0)
    time_to_exit_seconds = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    opened_at = models.DateTimeField(db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-opened_at"]

    def __str__(self) -> str:
        return f"PaperTrade {self.id} [{self.status}]"
