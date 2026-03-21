from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db.models import Prefetch
from django.urls import reverse

from apps.web.models import (
    AgentTrace,
    CrossVenueMarketMap,
    ExperimentRun,
    GraphRun,
    LeadLagPair,
    LeadLagSignal,
    MarketEventTick,
    MarketSnapshot,
    OrderBookLevelSnapshot,
    PaperTrade,
    RelatedMarketJudgment,
    ResolutionLabel,
    Watchlist,
)
from apps.web.services.leadlag import LeadLagMonitorService
from apps.web.services.ml_hooks import (
    BinaryLogisticRegression,
    GraphFeatures,
    PredictionModel,
    SnapshotFeatureExtractor,
)

EDGE_SCORE_BONUSES = {
    "mentions": 0.02,
    "involves": 0.03,
    "supported_by": 0.02,
    "related_to": 0.04,
    "affects_directly": 0.08,
    "affects_indirectly": 0.05,
    "governed_by_rule": -0.02,
}

ACRONYM_LABELS = {
    "api": "API",
    "id": "ID",
    "llm": "LLM",
    "mae": "MAE",
    "ndcg": "NDCG",
    "rmse": "RMSE",
    "sql": "SQL",
    "url": "URL",
    "usd": "USD",
}

MONTH_PATTERN = re.compile(
    r"\b("
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?|q[1-4]|week|quarter|fomc|cpi|jobs report|"
    r"earnings|meeting|election|debate|deadline"
    r")\b",
    re.IGNORECASE,
)
RANKING_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
RANKING_STOPWORDS = {
    "a",
    "all",
    "an",
    "and",
    "are",
    "be",
    "before",
    "by",
    "for",
    "from",
    "in",
    "market",
    "markets",
    "of",
    "on",
    "or",
    "question",
    "the",
    "this",
    "to",
    "what",
    "who",
    "will",
    "with",
    "yes",
    "no",
    "2024",
    "2025",
    "2026",
    "2027",
    "2028",
}
MIN_LIVE_RESOLUTION_EVAL_EXAMPLES = 8

AFFIRMATIVE_OUTCOMES = {"yes"}
NEGATIVE_OUTCOMES = {"no"}
RELATED_MARKET_USEFULNESS_LABELS = {
    "core": "Core signal",
    "watch": "Watchlist only",
    "reject": "Not useful",
}
RELATED_MARKET_REVIEW_STATE_LABELS = {
    "pending": "Pending review",
    "needs_second_review": "Needs second review",
    "contested": "Reviewer disagreement",
    "agreed": "Consensus reached",
}

DEFAULT_WATCHLISTS = [
    {
        "slug": "macro-rate-spillover",
        "title": "Macro Rate Spillover",
        "thesis": "Track how Fed path repricing spreads into inflation, USD, and adjacent macro contracts.",
        "summary": "Built for macro research on rates, inflation, dollar strength, and second-order market reactions.",
        "cadence": "Daily",
        "items": [
            {
                "label": "Fed decision in March",
                "url": "https://polymarket.com/event/fed-decision-in-march-885",
                "note": "Anchor market for near-term monetary-policy repricing.",
            },
            {
                "label": "How many Fed rate cuts in 2026?",
                "url": "https://polymarket.com/event/how-many-fed-rate-cuts-in-2026",
                "note": "Longer-duration spillover from the same macro narrative.",
            },
            {
                "label": "Largest company end of March?",
                "url": "https://polymarket.com/event/largest-company-end-of-march",
                "note": "Equity sentiment beneficiary when rates reprice growth and tech exposure.",
            },
        ],
    },
    {
        "slug": "commodity-shock-watch",
        "title": "Commodity Shock Watch",
        "thesis": "Monitor crude, inflation, and downstream macro spillover from a single commodity catalyst.",
        "summary": "Useful when one crude headline can propagate into inflation expectations, growth fears, and correlated contracts.",
        "cadence": "Daily",
        "items": [
            {
                "label": "Crude oil by end of March",
                "url": "https://polymarket.com/event/will-crude-oil-hit-80-by-end-of-march",
                "note": "Primary commodity trigger market.",
            },
            {
                "label": "Will Crude Oil (CL) hit by end of March?",
                "url": "https://polymarket.com/event/will-crude-oil-cl-hit-end-of-march",
                "note": "Settlement-sensitive variant worth comparing for narrative drift.",
            },
            {
                "label": "CPI path / inflation-linked market",
                "url": "https://polymarket.com/event/will-cpi-come-in-above-expectations-next-release",
                "note": "Use as downstream macro confirmation when energy reprices inflation.",
            },
        ],
    },
    {
        "slug": "political-narrative-cluster",
        "title": "Political Narrative Cluster",
        "thesis": "Watch a nomination or election market together with coalition, turnout, and media-proxy contracts.",
        "summary": "Designed for narrative-first political research instead of browsing isolated contracts.",
        "cadence": "Weekly",
        "items": [
            {
                "label": "Democratic nominee 2028",
                "url": "https://polymarket.com/event/democratic-nominee-2028",
                "note": "Anchor market for coalition shifts and adjacent candidate narratives.",
            },
            {
                "label": "Election media or debate catalyst",
                "url": "https://polymarket.com/event/will-there-be-a-presidential-debate",
                "note": "Catalyst proxy for narrative acceleration.",
            },
            {
                "label": "Turnout or battleground market",
                "url": "https://polymarket.com/event/will-democrats-win-the-popular-vote-2028",
                "note": "Second-order expression of campaign momentum.",
            },
        ],
    },
]

BENCHMARK_SUMMARY_CACHE_KEY = "chaoswing:benchmark_summary:v1"
LANDING_STATS_CACHE_KEY = "chaoswing:landing_stats:v1"
WATCHLISTS_ALL_CACHE_KEY = "chaoswing:watchlists:all:v1"
WATCHLISTS_FEATURED_CACHE_KEY = "chaoswing:watchlists:featured:v1"
WATCHLIST_CACHE_TTL_SECONDS = 600
CONCEPTUAL_NODE_TYPES = {"Entity", "Evidence", "Rule", "Hypothesis"}
CITATION_REQUIRED_STAGES = {"retriever", "graph_editor"}


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _humanize_relationship(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip()


def _humanize_identifier(value: str) -> str:
    words = _humanize_relationship(value).split()
    return " ".join(ACRONYM_LABELS.get(word.lower(), word.capitalize()) for word in words)


def _truncate_copy(value: str, *, limit: int = 120) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _format_metric_value(label: str, value: Any) -> str:
    key = label.lower()
    if isinstance(value, bool):
        return "Yes" if value else "No"

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)

    if "cost" in key:
        return f"${numeric:.4f}"
    if "latency" in key:
        return f"{numeric:.0f} ms"
    if "token" in key:
        return f"{numeric:,.0f}"
    if "rate" in key and abs(numeric) <= 1:
        return f"{numeric:.0%}"
    if "confidence" in key and abs(numeric) <= 1:
        return f"{numeric:.0%}"
    if numeric.is_integer():
        return f"{numeric:,.0f}"
    return f"{numeric:.2f}"


def _format_metric_items(metrics: dict[str, Any], *, limit: int | None = 4) -> list[dict[str, str]]:
    items = []
    for key, value in metrics.items():
        items.append(
            {
                "key": key,
                "label": _humanize_identifier(key),
                "value": _format_metric_value(key, value),
            }
        )
    return items if limit is None else items[:limit]


def _normalize_outcome_label(value: str) -> str:
    return str(value or "").strip().lower()


def _clip_probability(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _related_market_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _related_market_key_aliases(value: str) -> set[str]:
    normalized = _related_market_key(value)
    if not normalized:
        return set()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    aliases = {normalized}
    if slug:
        aliases.add(slug)
    return aliases


def _ranking_tokens(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if isinstance(value, list):
            values_to_scan = [str(item or "") for item in value]
        else:
            values_to_scan = [str(value or "")]
        for text in values_to_scan:
            for token in RANKING_TOKEN_PATTERN.findall(text.lower()):
                if len(token) < 3 or token in RANKING_STOPWORDS:
                    continue
                tokens.add(token)
    return tokens


def _jaccard_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _usefulness_label_to_relevance(label: str) -> int:
    normalized = str(label or "").strip().lower()
    if normalized == "core":
        return 3
    if normalized == "watch":
        return 1
    return 0


def _usefulness_label_display(label: str) -> str:
    return RELATED_MARKET_USEFULNESS_LABELS.get(str(label or "").strip().lower(), "Unreviewed")


def _normalized_reviewer_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return normalized[:80] or "anonymous"


def _review_state_display(state: str) -> str:
    return RELATED_MARKET_REVIEW_STATE_LABELS.get(str(state or "").strip().lower(), "Pending review")


def _snapshot_market_payloads(snapshot: MarketSnapshot) -> list[dict[str, Any]]:
    payload = snapshot.payload if isinstance(snapshot.payload, dict) else {}
    if isinstance(payload.get("markets"), list):
        return payload.get("markets") or []
    nested_snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    if isinstance(nested_snapshot.get("markets"), list):
        return nested_snapshot.get("markets") or []
    return []


def _snapshot_yes_probability(snapshot: MarketSnapshot) -> float | None:
    normalized_outcomes = [_normalize_outcome_label(outcome) for outcome in snapshot.outcomes or []]
    if set(normalized_outcomes) != AFFIRMATIVE_OUTCOMES | NEGATIVE_OUTCOMES:
        return None

    yes_index = normalized_outcomes.index("yes")
    market_payloads = _snapshot_market_payloads(snapshot)
    for market in market_payloads:
        market_outcomes = [_normalize_outcome_label(outcome) for outcome in market.get("outcomes") or []]
        outcome_prices = market.get("outcome_prices") or market.get("outcomePrices") or []
        if set(market_outcomes) != AFFIRMATIVE_OUTCOMES | NEGATIVE_OUTCOMES:
            continue
        if len(market_outcomes) != len(outcome_prices):
            continue
        try:
            return _clip_probability(float(outcome_prices[market_outcomes.index("yes")]))
        except (TypeError, ValueError, IndexError):
            continue

    if len(normalized_outcomes) != 2:
        return None
    implied_probability = _clip_probability(_to_float(snapshot.implied_probability))
    return implied_probability if yes_index == 0 else _clip_probability(1.0 - implied_probability)


def _snapshot_resolution_target(snapshot: MarketSnapshot) -> int | None:
    if not hasattr(snapshot, "resolution_label") or snapshot.resolution_label is None:
        return None
    normalized_outcome = _normalize_outcome_label(snapshot.resolution_label.resolved_outcome)
    if normalized_outcome in AFFIRMATIVE_OUTCOMES:
        return 1
    if normalized_outcome in NEGATIVE_OUTCOMES:
        return 0
    return None


def _agent_trust_example(run: GraphRun) -> dict[str, Any]:
    payload = run.payload or {}
    graph = payload.get("graph", {}) if isinstance(payload.get("graph"), dict) else {}
    run_data = payload.get("run", {}) if isinstance(payload.get("run"), dict) else {}
    review = run_data.get("review", {}) if isinstance(run_data.get("review"), dict) else {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    traces = list(run.agent_traces.all())

    conceptual_nodes = [node for node in nodes if str(node.get("type") or "") in CONCEPTUAL_NODE_TYPES]
    unsupported_nodes = [
        node
        for node in conceptual_nodes
        if not str(node.get("source_url") or "").strip()
        and not (node.get("evidence_snippets") or [])
        and not (node.get("metadata") or [])
    ]
    explained_edges = [
        edge for edge in edges if str(edge.get("explanation") or "").strip()
    ]
    source_backed_nodes = [
        node for node in nodes if str(node.get("source_url") or "").strip()
    ]
    evidence_backed_nodes = [
        node for node in conceptual_nodes if (node.get("evidence_snippets") or [])
    ]
    citation_stage_traces = [
        trace
        for trace in traces
        if trace.stage in CITATION_REQUIRED_STAGES and trace.status == "completed"
    ]
    citation_backed_traces = [
        trace for trace in citation_stage_traces if trace.citations
    ]
    telemetry_traces = [
        trace
        for trace in traces
        if trace.stage in {"graph_editor", "critic", "llm_expansion", "llm_review"}
    ]
    telemetry_backed_traces = [
        trace
        for trace in telemetry_traces
        if trace.latency_ms > 0 or (trace.token_input + trace.token_output) > 0 or trace.cost_usd > 0
    ]

    conceptual_count = len(conceptual_nodes)
    node_count = len(nodes)
    edge_count = len(edges)
    unsupported_claim_rate = (
        len(unsupported_nodes) / conceptual_count if conceptual_count else 0.0
    )
    supported_conceptual_rate = 1.0 - unsupported_claim_rate if conceptual_count else 1.0
    explained_edge_rate = len(explained_edges) / edge_count if edge_count else 1.0
    source_backed_node_rate = len(source_backed_nodes) / node_count if node_count else 0.0
    evidence_backed_rate = len(evidence_backed_nodes) / conceptual_count if conceptual_count else 0.0
    citation_stage_rate = (
        len(citation_backed_traces) / len(citation_stage_traces)
        if citation_stage_traces
        else 1.0
    )
    telemetry_coverage_rate = (
        len(telemetry_backed_traces) / len(telemetry_traces)
        if telemetry_traces
        else 0.0
    )
    issue_count = len(review.get("issues") or [])
    follow_up_count = len(review.get("follow_up_actions") or [])
    review_issue_penalty = min(issue_count / 3.0, 1.0)
    trust_score = (
        0.35 * supported_conceptual_rate
        + 0.20 * explained_edge_rate
        + 0.20 * citation_stage_rate
        + 0.15 * (1.0 if review.get("approved") else 0.0)
        + 0.10 * (1.0 - review_issue_penalty)
    )
    return {
        "run_id": str(run.id),
        "event_slug": run.event_slug,
        "event_title": run.event_title,
        "mode": run.mode,
        "node_count": node_count,
        "edge_count": edge_count,
        "conceptual_node_count": conceptual_count,
        "unsupported_node_count": len(unsupported_nodes),
        "unsupported_claim_rate": unsupported_claim_rate,
        "supported_conceptual_rate": supported_conceptual_rate,
        "explained_edge_rate": explained_edge_rate,
        "source_backed_node_rate": source_backed_node_rate,
        "evidence_backed_conceptual_rate": evidence_backed_rate,
        "citation_stage_rate": citation_stage_rate,
        "telemetry_coverage_rate": telemetry_coverage_rate,
        "issue_count": issue_count,
        "follow_up_count": follow_up_count,
        "approved": bool(review.get("approved")),
        "quality_score": _to_float(review.get("quality_score")),
        "trust_score": max(0.0, min(trust_score, 1.0)),
    }


class MarketBriefService:
    def build(self, run: GraphRun) -> dict[str, Any]:
        payload = run.payload or {}
        event = payload.get("event", {})
        graph = payload.get("graph", {})
        run_data = payload.get("run", {})
        review = run_data.get("review", {})

        all_related_markets = self.related_market_ranking(run)
        related_markets = all_related_markets[:4]
        strongest_path = self._strongest_path(graph)
        all_evidence = self._key_evidence(graph, limit=None)
        evidence = all_evidence[:3]
        catalyst_timeline = self.catalyst_timeline(run)
        catalyst = catalyst_timeline[0] if catalyst_timeline else None
        caveats = self._confidence_caveats(run, review, graph, related_markets)
        change_summary = self.change_summary(run)
        trace_summary = self.trace_summary(run)

        overview = event.get("description") or (
            "ChaosWing resolved this Polymarket event into a shareable analyst brief."
        )
        if strongest_path["summary"]:
            overview = f"{overview} Strongest spillover path: {strongest_path['summary']}."

        return {
            "run_id": str(run.id),
            "event": {
                "title": event.get("title") or run.event_title or "Untitled market",
                "description": event.get("description") or run.event_title or "",
                "status": event.get("status") or run.status,
                "source_url": event.get("source_url") or run.source_url,
                "updated_at": event.get("updated_at") or run.updated_at.isoformat(),
                "tags": event.get("tags") or [],
                "outcomes": event.get("outcomes") or [],
                "image_url": event.get("image_url") or "",
            },
            "overview": overview,
            "strongest_path": strongest_path,
            "top_related_markets": related_markets,
            "related_market_ranking": all_related_markets,
            "key_evidence": evidence,
            "next_catalyst": catalyst,
            "catalyst_timeline": catalyst_timeline,
            "change_summary": change_summary,
            "confidence_caveats": caveats,
            "trust": {
                "quality_score": _to_float(review.get("quality_score")),
                "approved": bool(review.get("approved")),
                "issue_count": len(review.get("issues") or []),
                "related_market_count": len(all_related_markets),
                "evidence_count": len(all_evidence),
                "mode": run.mode,
                "graph_nodes": len(graph.get("nodes") or []),
                "graph_edges": len(graph.get("edges") or []),
                "follow_up_actions": review.get("follow_up_actions") or [],
                "trace_summary": trace_summary,
                "support_summary": _agent_trust_example(run),
            },
            "workflow": {
                "mode": run.mode,
                "model_name": run.model_name,
                "review": review,
                "workflow_log": run.workflow_log or [],
            },
        }

    def related_market_ranking(self, run: GraphRun) -> list[dict[str, Any]]:
        graph = (run.payload or {}).get("graph", {})
        return self._top_related_markets(graph, limit=None)

    def change_summary(self, run: GraphRun) -> dict[str, Any]:
        previous_run = (
            GraphRun.objects.filter(event_slug=run.event_slug)
            .exclude(pk=run.pk)
            .order_by("-created_at")
            .first()
        )
        if previous_run is None and run.source_url:
            previous_run = (
                GraphRun.objects.filter(source_url=run.source_url)
                .exclude(pk=run.pk)
                .order_by("-created_at")
                .first()
            )

        if previous_run is None:
            return {
                "status": "first_run",
                "summary": "This is the first saved run for this market, so there is no prior baseline yet.",
                "previous_run_id": "",
                "previous_created_at": "",
                "previous_title": "",
                "previous_mode": "",
                "new_nodes": [],
                "removed_nodes": [],
                "new_evidence": [],
                "moved_related_markets": [],
            }

        current_graph = (run.payload or {}).get("graph", {})
        previous_graph = (previous_run.payload or {}).get("graph", {})
        current_node_map = {
            (node.get("type") or "", node.get("label") or ""): node
            for node in current_graph.get("nodes") or []
        }
        previous_node_map = {
            (node.get("type") or "", node.get("label") or ""): node
            for node in previous_graph.get("nodes") or []
        }
        current_nodes = set(current_node_map)
        previous_nodes = set(previous_node_map)

        new_nodes = sorted(label for _, label in current_nodes - previous_nodes if label)[:5]
        removed_nodes = sorted(label for _, label in previous_nodes - current_nodes if label)[:5]
        new_evidence = [
            label
            for node_type, label in current_nodes - previous_nodes
            if node_type == "Evidence" and label
        ][:3]

        current_related = {
            node.get("label") or "Related market": _to_float(node.get("confidence"))
            for node in current_graph.get("nodes") or []
            if node.get("type") == "RelatedMarket"
        }
        previous_related = {
            node.get("label") or "Related market": _to_float(node.get("confidence"))
            for node in previous_graph.get("nodes") or []
            if node.get("type") == "RelatedMarket"
        }
        moved_related = []
        for title in sorted(set(current_related) & set(previous_related)):
            delta = round(current_related[title] - previous_related[title], 2)
            if abs(delta) >= 0.01:
                moved_related.append(
                    {
                        "title": title,
                        "delta": delta,
                        "current_confidence": current_related[title],
                        "previous_confidence": previous_related[title],
                    }
                )
        moved_related.sort(key=lambda item: abs(item["delta"]), reverse=True)

        current_edges = {
            (edge.get("source"), edge.get("target"), edge.get("type"))
            for edge in current_graph.get("edges") or []
        }
        previous_edges = {
            (edge.get("source"), edge.get("target"), edge.get("type"))
            for edge in previous_graph.get("edges") or []
        }
        edge_delta = len(current_edges) - len(previous_edges)

        summary_bits = []
        if new_nodes:
            summary_bits.append(f"{len(new_nodes)} new nodes surfaced")
        if removed_nodes:
            summary_bits.append(f"{len(removed_nodes)} nodes dropped")
        if edge_delta:
            direction = "more" if edge_delta > 0 else "fewer"
            summary_bits.append(f"{abs(edge_delta)} {direction} edges than the prior run")
        if moved_related:
            direction = "up" if moved_related[0]["delta"] > 0 else "down"
            summary_bits.append(
                f"top related confidence moved {direction} in {moved_related[0]['title']}"
            )

        moved_related_display = []
        for item in moved_related[:4]:
            direction = "up" if item["delta"] > 0 else "down"
            moved_related_display.append(
                {
                    **item,
                    "direction": direction,
                    "direction_label": "Moved up" if direction == "up" else "Moved down",
                    "delta_label": f"{item['delta']:+.2f}",
                    "current_confidence_label": f"{item['current_confidence']:.2f}",
                    "previous_confidence_label": f"{item['previous_confidence']:.2f}",
                }
            )

        return {
            "status": "changed" if summary_bits else "stable",
            "summary": "; ".join(summary_bits) or "No structural changes were detected versus the prior saved run.",
            "previous_run_id": str(previous_run.id),
            "previous_created_at": previous_run.created_at.isoformat(),
            "previous_title": previous_run.event_title or "Prior saved run",
            "previous_mode": previous_run.mode,
            "node_delta": len(current_nodes) - len(previous_nodes),
            "edge_delta": edge_delta,
            "new_nodes": new_nodes,
            "removed_nodes": removed_nodes,
            "new_evidence": new_evidence,
            "moved_related_markets": moved_related_display,
        }

    def catalyst_timeline(self, run: GraphRun) -> list[dict[str, Any]]:
        payload = run.payload or {}
        event = payload.get("event", {})
        graph = payload.get("graph", {})
        candidates = []
        seen = set()
        for node in graph.get("nodes") or []:
            label = (node.get("label") or "").strip()
            summary = " ".join(
                part for part in [node.get("summary") or "", node.get("description") or ""] if part
            ).strip()
            if not label:
                continue
            search_text = f"{label} {summary}"
            if not MONTH_PATTERN.search(search_text):
                continue
            key = (node.get("type") or "", label)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "title": label,
                    "summary": summary,
                    "type": node.get("type") or "",
                    "confidence": _to_float(node.get("confidence")),
                    "source_url": node.get("source_url") or event.get("source_url") or "",
                }
            )
        candidates.sort(key=lambda item: item["confidence"], reverse=True)
        return candidates[:5]

    def trace_summary(self, run: GraphRun) -> dict[str, Any]:
        traces = list(run.agent_traces.all())
        if not traces:
            return {
                "stage_count": 0,
                "completed_count": 0,
                "fallback_count": 0,
                "failed_count": 0,
                "manual_reviews": 0,
                "citation_count": 0,
                "total_latency_ms": 0,
                "total_token_count": 0,
                "total_cost_usd": 0.0,
                "health_copy": "No structured trace rows are persisted for this run yet.",
                "stages": [],
            }
        statuses = Counter(trace.status for trace in traces)
        total_latency = sum(trace.latency_ms for trace in traces)
        total_tokens = sum(trace.token_input + trace.token_output for trace in traces)
        total_cost = sum(trace.cost_usd for trace in traces)
        total_citations = sum(len(trace.citations or []) for trace in traces)
        stages = [
            {
                "name": _humanize_identifier(trace.stage),
                "stage": trace.stage,
                "status": trace.status,
                "status_label": _humanize_identifier(trace.status),
                "detail": (trace.detail or "No stage detail was recorded.").strip(),
                "detail_short": _truncate_copy(trace.detail or "No stage detail was recorded."),
                "latency_label": f"{trace.latency_ms:,} ms" if trace.latency_ms else "n/a",
                "token_label": (
                    f"{trace.token_input + trace.token_output:,} tokens"
                    if (trace.token_input or trace.token_output)
                    else "No token usage"
                ),
                "cost_label": f"${trace.cost_usd:.4f}",
                "citation_count": len(trace.citations or []),
            }
            for trace in traces
        ]
        if statuses.get("failed", 0):
            health_copy = "At least one workflow stage failed. Treat the brief as partial and inspect source context before acting."
        elif statuses.get("fallback", 0):
            health_copy = "Some workflow stages fell back to deterministic behavior, so the brief is inspectable but not fully agent-expanded."
        else:
            health_copy = "Trace coverage is clean for this run: the saved brief can be inspected stage by stage."
        return {
            "stage_count": len(traces),
            "completed_count": statuses.get("completed", 0),
            "fallback_count": statuses.get("fallback", 0),
            "failed_count": statuses.get("failed", 0),
            "manual_reviews": sum(1 for trace in traces if trace.stage == "manual_review"),
            "citation_count": total_citations,
            "total_latency_ms": total_latency,
            "total_token_count": total_tokens,
            "total_cost_usd": round(total_cost, 4),
            "health_copy": health_copy,
            "stages": stages,
        }

    def _top_related_markets(self, graph: dict[str, Any], limit: int | None = 4) -> list[dict[str, Any]]:
        markets = []
        for node in graph.get("nodes") or []:
            if node.get("type") != "RelatedMarket":
                continue
            markets.append(
                {
                    "id": node.get("id", ""),
                    "title": node.get("label") or "Related market",
                    "summary": node.get("summary") or node.get("description") or "",
                    "confidence": _to_float(node.get("confidence")),
                    "source_url": node.get("source_url") or "",
                    "probability": _to_float(node.get("probability")),
                    "probability_label": node.get("probability_label") or "",
                    "metadata": node.get("metadata") or [],
                }
            )
        markets.sort(key=lambda item: item["confidence"], reverse=True)
        return markets if limit is None else markets[:limit]

    def _key_evidence(self, graph: dict[str, Any], limit: int | None = 3) -> list[dict[str, Any]]:
        evidence_nodes = []
        for node in graph.get("nodes") or []:
            if node.get("type") != "Evidence":
                continue
            evidence_nodes.append(
                {
                    "id": node.get("id", ""),
                    "title": node.get("label") or "Evidence",
                    "summary": node.get("summary") or node.get("description") or "",
                    "confidence": _to_float(node.get("confidence")),
                    "source_url": node.get("source_url") or "",
                }
            )
        evidence_nodes.sort(key=lambda item: item["confidence"], reverse=True)
        return evidence_nodes if limit is None else evidence_nodes[:limit]

    def _strongest_path(self, graph: dict[str, Any]) -> dict[str, Any]:
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        event_node = next((node for node in nodes if node.get("type") == "Event"), None)
        if not event_node:
            return {"score": 0.0, "summary": "", "nodes": [], "edges": []}

        node_map = {node.get("id"): node for node in nodes if node.get("id")}
        adjacency: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if not source or not target:
                continue
            adjacency.setdefault(source, []).append({"edge": edge, "next_id": target})
            adjacency.setdefault(target, []).append({"edge": edge, "next_id": source})

        best = {"score": float("-inf"), "node_ids": [event_node.get("id")], "edges": []}

        def walk(current_id: str, visited: set[str], path_nodes: list[str], path_edges: list[dict[str, Any]]) -> None:
            nonlocal best
            if len(path_edges) >= 2:
                score = sum(
                    _to_float(edge.get("confidence"))
                    + EDGE_SCORE_BONUSES.get(edge.get("type", ""), 0.0)
                    for edge in path_edges
                )
                if score > best["score"]:
                    best = {
                        "score": score,
                        "node_ids": list(path_nodes),
                        "edges": list(path_edges),
                    }
            if len(path_edges) == 4:
                return
            for neighbor in adjacency.get(current_id, []):
                next_id = neighbor["next_id"]
                if next_id in visited:
                    continue
                visited.add(next_id)
                path_nodes.append(next_id)
                path_edges.append(neighbor["edge"])
                walk(next_id, visited, path_nodes, path_edges)
                path_edges.pop()
                path_nodes.pop()
                visited.remove(next_id)

        event_id = event_node.get("id")
        if event_id:
            walk(event_id, {event_id}, [event_id], [])

        resolved_nodes = [
            {
                "id": node_id,
                "label": (node_map.get(node_id) or {}).get("label") or "Node",
                "type": (node_map.get(node_id) or {}).get("type") or "",
            }
            for node_id in best["node_ids"]
            if node_id in node_map
        ]
        resolved_edges = [
            {
                "id": edge.get("id", ""),
                "type": edge.get("type", ""),
                "relationship": _humanize_relationship(edge.get("type", "")),
                "confidence": _to_float(edge.get("confidence")),
                "explanation": edge.get("explanation") or "",
            }
            for edge in best["edges"]
        ]
        summary = ""
        if resolved_nodes:
            summary = " -> ".join(node["label"] for node in resolved_nodes)

        return {
            "score": round(max(best["score"], 0.0), 2) if best["edges"] else 0.0,
            "summary": summary,
            "nodes": resolved_nodes,
            "edges": resolved_edges,
        }

    def _next_catalyst(self, graph: dict[str, Any], event: dict[str, Any]) -> dict[str, Any] | None:
        candidates = []
        for node in graph.get("nodes") or []:
            label = (node.get("label") or "").strip()
            summary = " ".join(
                part for part in [node.get("summary") or "", node.get("description") or ""] if part
            ).strip()
            search_text = f"{label} {summary}"
            if not MONTH_PATTERN.search(search_text):
                continue
            candidates.append(
                {
                    "title": label,
                    "summary": summary,
                    "type": node.get("type") or "",
                    "confidence": _to_float(node.get("confidence")),
                    "source_url": node.get("source_url") or event.get("source_url") or "",
                }
            )
        if not candidates:
            return None
        candidates.sort(key=lambda item: item["confidence"], reverse=True)
        return candidates[0]

    def _confidence_caveats(
        self,
        run: GraphRun,
        review: dict[str, Any],
        graph: dict[str, Any],
        related_markets: list[dict[str, Any]],
    ) -> list[str]:
        issues = [str(issue).strip() for issue in review.get("issues") or [] if str(issue).strip()]
        if issues:
            caveats = issues[:3]
        else:
            caveats = []

        quality_score = _to_float(review.get("quality_score"))
        evidence_count = sum(1 for node in graph.get("nodes") or [] if node.get("type") == "Evidence")

        if quality_score and quality_score < 0.75:
            caveats.append("Graph quality is below the target review band, so treat the ranking as exploratory.")
        if evidence_count < 2:
            caveats.append("Evidence coverage is still thin; verify settlement mechanics and source text before acting.")
        if len(related_markets) < 2:
            caveats.append("Few adjacent markets were surfaced, which can mean the narrative is narrow or discovery recall is limited.")
        if run.mode == "deterministic-fallback":
            caveats.append("This run used the deterministic fallback path, so no model-backed enrichment or citation review was applied.")
        if not caveats:
            caveats.append("Narrative spillover is directional context, not a guarantee. Use the source market and linked contracts to confirm timing.")
        return caveats[:4]


class BenchmarkSummaryService:
    def __init__(self, data_path: Path | None = None):
        self.data_path = data_path or Path("ml_data") / "training_data.jsonl"

    def build_cached(self, *, force_refresh: bool = False) -> dict[str, Any]:
        if force_refresh:
            self.invalidate_cached_summary()

        if self._is_default_data_path():
            cached_summary = cache.get(BENCHMARK_SUMMARY_CACHE_KEY)
            if cached_summary is not None:
                return cached_summary

        summary = self.build()
        if self._is_default_data_path():
            cache.set(
                BENCHMARK_SUMMARY_CACHE_KEY,
                summary,
                timeout=getattr(settings, "CHAOSWING_BENCHMARK_CACHE_TTL", 120),
            )
        return summary

    def build(self) -> dict[str, Any]:
        examples = self._load_examples()
        runs = list(
            GraphRun.objects.prefetch_related(
                Prefetch(
                    "agent_traces",
                    queryset=AgentTrace.objects.only("id", "graph_run_id"),
                )
            ).order_by("-created_at")[:8]
        )
        experiments = list(ExperimentRun.objects.order_by("-created_at")[:8])
        latest_experiments = self._latest_experiments_by_task(
            "resolution_backtest",
            "leadlag_backtest",
            "related_market_ranking",
            "related_market_usefulness",
            "agent_eval",
            "agent_trust",
        )
        dataset_counts = {
            "runs_in_db": GraphRun.objects.count(),
            "snapshots": MarketSnapshot.objects.count(),
            "resolution_labels": ResolutionLabel.objects.count(),
            "agent_traces": AgentTrace.objects.count(),
            "related_market_judgments": RelatedMarketJudgment.objects.count(),
        }

        quality_metrics = self._quality_metrics(examples)
        coverage_metrics = self._coverage_metrics(runs, examples)
        mode_breakdown = Counter(
            example.get("mode", "unknown")
            for example in examples
            if example.get("mode")
        )
        if not mode_breakdown:
            mode_breakdown = Counter(run.mode or "unknown" for run in runs)
        latest_resolution_experiment = latest_experiments.get("resolution_backtest")
        latest_leadlag_experiment = latest_experiments.get("leadlag_backtest")
        latest_ranking_experiment = latest_experiments.get("related_market_ranking")
        latest_human_ranking_experiment = latest_experiments.get("related_market_usefulness")
        latest_agent_experiment = latest_experiments.get("agent_eval")
        latest_agent_trust_experiment = latest_experiments.get("agent_trust")
        leadlag_totals = LeadLagMonitorService().build_cached()
        human_label_review = RelatedMarketJudgmentService().summary()

        recent_cases = []
        for run in runs[:5]:
            payload = run.payload or {}
            review = payload.get("run", {}).get("review", {})
            graph = payload.get("graph", {})
            top_related = [
                _to_float(node.get("confidence"))
                for node in graph.get("nodes") or []
                if node.get("type") == "RelatedMarket"
            ]
            recent_cases.append(
                {
                    "id": str(run.id),
                    "title": run.event_title or payload.get("event", {}).get("title") or "Untitled run",
                    "quality_score": _to_float(review.get("quality_score")),
                    "approved": bool(review.get("approved")),
                    "review_state": "Approved" if review.get("approved") else "Needs review",
                    "related_market_count": len(top_related),
                    "top_related_confidence": round(max(top_related), 2) if top_related else 0.0,
                    "mode": run.mode,
                    "mode_label": _humanize_identifier(run.mode),
                    "updated_at": run.updated_at.isoformat(),
                }
            )

        experiment_runs = [self._serialize_experiment_run(experiment) for experiment in experiments]
        agent_benchmark = {
            "name": "Agent trace coverage",
            "status": "Live",
            "primary_metric": f"{dataset_counts['agent_traces']} trace rows",
            "secondary_metric": f"{coverage_metrics['avg_trace_rows']:.1f} traces/run",
            "description": "Workflow stages are now persisted as agent traces so evaluation does not depend on frontend state alone.",
        }
        if latest_agent_experiment:
            agent_metrics = latest_agent_experiment.metrics or {}
            agent_benchmark = {
                "name": "Agent instrumentation coverage",
                "status": "Live",
                "primary_metric": f"Run coverage {_to_float(agent_metrics.get('run_coverage_rate')):.0%}",
                "secondary_metric": (
                    f"Stages {_to_float(agent_metrics.get('required_stage_coverage_rate')):.0%} | "
                    f"citations {_to_float(agent_metrics.get('citation_coverage_rate')):.0%}"
                ),
                "description": (
                    "Persisted traces now report whether each run captured the staged planner/retriever/"
                    "graph-editor/verifier/critic workflow, plus citation, latency, token, and cost metadata."
                ),
            }
        live_benchmarks = [
            {
                "name": "Graph quality scoring baseline",
                "status": "Live",
                "primary_metric": f"MAE {quality_metrics['mae']:.2f}",
                "secondary_metric": f"RMSE {quality_metrics['rmse']:.2f}",
                "description": "Current heuristic scorer measured against persisted review-quality labels from saved runs.",
            },
            {
                "name": "Related-market coverage proxy",
                "status": "Live",
                "primary_metric": f"{coverage_metrics['avg_related_markets']:.1f} related markets/run",
                "secondary_metric": f"Top confidence {coverage_metrics['avg_top_related_confidence']:.0%}",
                "description": "Coverage signal for how many adjacent contracts ChaosWing surfaces and how strong the top-ranked match looks.",
            },
            {
                "name": "Evidence density tracking",
                "status": "Live",
                "primary_metric": f"{coverage_metrics['avg_evidence_nodes']:.1f} evidence nodes/run",
                "secondary_metric": f"{coverage_metrics['avg_edges']:.1f} edges/run",
                "description": "Structural proxy for whether each brief is grounded enough to inspect instead of just visualizing a sparse graph.",
            },
            agent_benchmark,
        ]
        next_benchmarks = [
            {
                "name": "Resolution forecasting",
                "target_metrics": "Brier score, log loss, calibration error",
                "description": "Historical YES/NO outcome forecasting against market-implied probability and boosted tabular baselines.",
            },
            {
                "name": "Cross-venue lead-lag paper trading",
                "target_metrics": "Net PnL, hit rate, slippage-adjusted return, latency decay",
                "description": "Research whether Polymarket and Kalshi pairs show tradable leader-follower gaps after costs instead of relying on hand-wavy correlation claims.",
            },
            {
                "name": "Related-market ranking",
                "target_metrics": "Recall@K, NDCG, coverage by category",
                "description": "Evaluate whether top-ranked adjacent markets are actually useful to traders rather than merely semantically similar.",
            },
            {
                "name": "Human-labeled related-market usefulness",
                "target_metrics": "Precision@K, analyst agreement, category coverage",
                "description": "Upgrade the current silver benchmark with explicit human labels for whether a surfaced adjacent market is genuinely useful for trading decisions.",
            },
            {
                "name": "Agent trust benchmark",
                "target_metrics": "Citation precision, unsupported-claim rate, latency, token cost",
                "description": "Measure planner/retriever/verifier/critic workflows instead of treating LLM output as unscored product copy.",
            },
        ]
        resolution_live_ready = False
        if latest_resolution_experiment:
            metrics = latest_resolution_experiment.metrics or {}
            evaluated_examples = int(metrics.get("example_count") or 0)
            resolution_live_ready = evaluated_examples >= MIN_LIVE_RESOLUTION_EVAL_EXAMPLES
            if resolution_live_ready:
                live_benchmarks.insert(
                    0,
                    {
                        "name": "Resolution forecasting rolling backtest",
                        "status": "Live",
                        "primary_metric": f"Model Brier {_to_float(metrics.get('model_brier')):.3f}",
                        "secondary_metric": f"Lift {_to_float(metrics.get('brier_lift')):+.3f} vs market-implied",
                        "description": "Expanding-window backtest over labeled YES/NO snapshots using market-implied probability as the baseline and a lightweight logistic model as the challenger.",
                    },
                )
                next_benchmarks = [
                    benchmark
                    for benchmark in next_benchmarks
                    if benchmark["name"] != "Resolution forecasting"
                ]
            else:
                next_benchmarks = [
                    {
                        **benchmark,
                        "description": (
                            "Historical YES/NO outcome forecasting against market-implied probability and boosted "
                            f"tabular baselines. Current readiness: {dataset_counts['resolution_labels']} labeled "
                            f"snapshots and {evaluated_examples} evaluated examples; ChaosWing promotes this benchmark "
                            f"to live at {MIN_LIVE_RESOLUTION_EVAL_EXAMPLES}+ evaluated examples."
                        ),
                    }
                    if benchmark["name"] == "Resolution forecasting"
                    else benchmark
                    for benchmark in next_benchmarks
                ]
        if latest_leadlag_experiment:
            metrics = latest_leadlag_experiment.metrics or {}
            live_benchmarks.insert(
                1 if resolution_live_ready else 0,
                {
                    "name": "Cross-venue lead-lag paper trading",
                    "status": "Live",
                    "primary_metric": f"Net PnL {_to_float(metrics.get('net_pnl')):+.3f}",
                    "secondary_metric": f"Hit rate {_to_float(metrics.get('hit_rate')):.0%}",
                    "description": "Paper-trading benchmark for candidate spillover opportunities across Polymarket and Kalshi, including fees, slippage, and no-trade filtering.",
                },
            )
            next_benchmarks = [
                benchmark
                for benchmark in next_benchmarks
                if benchmark["name"] != "Cross-venue lead-lag paper trading"
            ]
        if latest_ranking_experiment:
            metrics = latest_ranking_experiment.metrics or {}
            insert_index = 0
            if resolution_live_ready:
                insert_index += 1
            if latest_leadlag_experiment:
                insert_index += 1
            live_benchmarks.insert(
                insert_index,
                {
                    "name": "Related-market ranking silver benchmark",
                    "status": "Live",
                    "primary_metric": f"NDCG@5 {_to_float(metrics.get('model_ndcg_at_5')):.3f}",
                    "secondary_metric": f"Recall@3 {_to_float(metrics.get('model_recall_at_3')):.0%}",
                    "description": (
                        "Silver-label ranking evaluation over persisted related-market selections, "
                        "comparing a lexical baseline with context-aware reranking."
                    ),
                },
            )
            next_benchmarks = [
                benchmark
                for benchmark in next_benchmarks
                if benchmark["name"] != "Related-market ranking"
            ]
        if latest_human_ranking_experiment:
            metrics = latest_human_ranking_experiment.metrics or {}
            insert_index = 0
            if resolution_live_ready:
                insert_index += 1
            if latest_leadlag_experiment:
                insert_index += 1
            live_benchmarks.insert(
                insert_index,
                {
                    "name": "Human-labeled related-market usefulness",
                    "status": "Live",
                    "primary_metric": f"NDCG@5 {_to_float(metrics.get('model_ndcg_at_5')):.3f}",
                    "secondary_metric": (
                        f"MRR {_to_float(metrics.get('model_mrr')):.3f} | "
                        f"agreement {_to_float(metrics.get('avg_agreement_rate')):.0%}"
                    ),
                    "description": (
                        "Benchmark over manually judged related markets with reviewer-aware consensus "
                        "labels, so the ranking track is grounded in actual trader usefulness signals "
                        "instead of only persisted selections."
                    ),
                },
            )
            next_benchmarks = [
                benchmark
                for benchmark in next_benchmarks
                if benchmark["name"] != "Human-labeled related-market usefulness"
            ]
        if latest_agent_trust_experiment:
            metrics = latest_agent_trust_experiment.metrics or {}
            live_benchmarks.append(
                {
                    "name": "Agent trust benchmark",
                    "status": "Live",
                    "primary_metric": f"Trust score {_to_float(metrics.get('avg_trust_score')):.2f}",
                    "secondary_metric": (
                        f"Unsupported {_to_float(metrics.get('avg_unsupported_claim_rate')):.0%} | "
                        f"cited stages {_to_float(metrics.get('avg_citation_stage_rate')):.0%}"
                    ),
                    "description": (
                        "Saved graphs are scored on supported conceptual claims, explained edges, "
                        "citation-backed retrieval and graph-edit stages, and review issue burden."
                    ),
                }
            )
            next_benchmarks = [
                benchmark
                for benchmark in next_benchmarks
                if benchmark["name"] != "Agent trust benchmark"
            ]

        return {
            "dataset": {
                "path": str(self.data_path),
                "examples": len(examples),
                **dataset_counts,
            },
            "summary_cards": [
                {
                    "label": "Persisted graph runs",
                    "value": dataset_counts["runs_in_db"],
                    "copy": "Saved runs available for replay, brief generation, and benchmark review.",
                },
                {
                    "label": "Historical snapshots",
                    "value": dataset_counts["snapshots"],
                    "copy": "Event snapshots captured from run creation or snapshot collection commands.",
                },
                {
                    "label": "Approval rate",
                    "value": f"{quality_metrics['approval_rate']:.0%}",
                    "copy": "Share of saved runs that cleared the current review gate.",
                },
                {
                    "label": "Avg review quality",
                    "value": f"{quality_metrics['avg_quality_score']:.2f}",
                    "copy": "Mean persisted review score across the benchmark dataset.",
                },
                {
                    "label": "Lead-lag signals",
                    "value": leadlag_totals["totals"]["signals"],
                    "copy": "Cross-venue candidate and no-trade signals recorded for the research monitor.",
                },
                {
                    "label": "Human ranking labels",
                    "value": dataset_counts["related_market_judgments"],
                    "copy": "Manual related-market usefulness judgments captured through the benchmark review queue.",
                },
            ],
            "live_benchmarks": live_benchmarks,
            "next_benchmarks": next_benchmarks,
            "mode_breakdown": [
                {"mode": mode, "label": _humanize_identifier(mode), "count": count}
                for mode, count in mode_breakdown.most_common()
            ],
            "recent_cases": recent_cases,
            "experiment_runs": experiment_runs,
            "human_label_review": human_label_review,
            "methodology": {
                "commands": [
                    "python manage.py collect_market_snapshots",
                    "python manage.py label_resolved_markets --refresh-remote",
                    "python manage.py sync_crossvenue_market_map",
                    "python manage.py stream_live_ticks --duration-seconds 60 --iterations 0 --rebuild-pairs-every 1 --scan-signals-every 1 --run-paper-trader --transport hybrid --active-pairs-only",
                    "python manage.py collect_live_ticks --iterations 12 --poll-seconds 5 --active-pairs-only",
                    "python manage.py build_leadlag_pairs",
                    "python manage.py run_leadlag_backtest",
                    "python manage.py run_paper_trader",
                    "python manage.py build_benchmark_dataset",
                    "python manage.py run_resolution_backtest --refresh-labels --refresh-remote",
                    "python manage.py run_related_market_ranking_benchmark",
                    "python manage.py run_related_market_usefulness_benchmark --min-reviewers-per-candidate 1",
                    "python manage.py run_golden_dataset_eval --strategy baseline --log-mlflow",
                    "python manage.py run_quality_backtest",
                    "python manage.py backfill_agent_pipeline_traces",
                    "python manage.py run_agent_eval --backfill-missing",
                    "python manage.py run_agent_trust_benchmark",
                    "python manage.py export_benchmark_report --pretty",
                ],
                "notes": [
                    "Quality labels come from persisted graph reviews saved at run time.",
                    "Resolution forecasting only goes live after labeled YES/NO snapshots exist, final outcomes have been propagated across event histories, and a rolling backtest has cleared the minimum evaluated-example threshold.",
                    "The human-labeled related-market usefulness set doubles as ChaosWing's clearest local golden dataset, and it can be logged to MLflow without needing Databricks.",
                    "Lead-lag research is benchmarked as candidate signals and paper trades, not claimed as live arbitrage.",
                    "Related-market ranking now has both a silver benchmark and a reviewer-aware human-judgment upgrade path through the review queue.",
                    "Historical runs can be backfilled into the staged planner/retriever/graph-editor/verifier/critic trace model so benchmark coverage reflects the actual saved artifacts, not only newly generated runs.",
                    "Agent trust is benchmarked directly from saved graphs and staged traces, so the trust score reflects structural support quality rather than only instrumentation presence.",
                ],
            },
        }

    def _load_examples(self) -> list[dict[str, Any]]:
        if not self.data_path.exists():
            return []
        records = []
        with self.data_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def _quality_metrics(self, examples: list[dict[str, Any]]) -> dict[str, float]:
        if not examples:
            return {
                "avg_quality_score": 0.0,
                "approval_rate": 0.0,
                "mae": 0.0,
                "rmse": 0.0,
            }

        model = PredictionModel()
        targets = []
        predictions = []
        approvals = 0
        for example in examples:
            features = GraphFeatures(**(example.get("features") or {}))
            target = _to_float(example.get("quality_score"))
            prediction = model.predict_quality(features)
            targets.append(target)
            predictions.append(prediction)
            approvals += 1 if (example.get("labels") or {}).get("approved") else 0

        errors = [abs(pred - target) for pred, target in zip(predictions, targets, strict=False)]
        squared_errors = [(pred - target) ** 2 for pred, target in zip(predictions, targets, strict=False)]

        return {
            "avg_quality_score": sum(targets) / len(targets),
            "approval_rate": approvals / len(examples),
            "mae": sum(errors) / len(errors),
            "rmse": math.sqrt(sum(squared_errors) / len(squared_errors)),
        }

    def _serialize_experiment_run(self, experiment: ExperimentRun) -> dict[str, Any]:
        metric_items = _format_metric_items(experiment.metrics, limit=5)
        return {
            "id": experiment.id,
            "task_type": experiment.task_type,
            "title": experiment.title,
            "dataset_version": experiment.dataset_version,
            "metrics": experiment.metrics,
            "metric_items": metric_items,
            "metric_summary": ", ".join(
                f"{item['label']} {item['value']}"
                for item in metric_items[:3]
            ),
            "artifact_count": len(experiment.artifacts or {}),
            "notes": experiment.notes,
            "created_at": experiment.created_at.isoformat(),
            "created_label": experiment.created_at.strftime("%Y-%m-%d"),
        }

    def _latest_experiments_by_task(self, *task_types: str) -> dict[str, ExperimentRun]:
        if not task_types:
            return {}
        latest: dict[str, ExperimentRun] = {}
        queryset = ExperimentRun.objects.filter(task_type__in=task_types).order_by("-created_at")
        for experiment in queryset:
            latest.setdefault(experiment.task_type, experiment)
            if len(latest) == len(task_types):
                break
        return latest

    def _coverage_metrics(self, runs: list[GraphRun], examples: list[dict[str, Any]]) -> dict[str, float]:
        if examples:
            avg_edges = sum(_to_float((example.get("features") or {}).get("edge_count")) for example in examples) / len(examples)
            avg_evidence = sum(_to_float((example.get("features") or {}).get("evidence_count")) for example in examples) / len(examples)
            avg_related = sum(_to_float((example.get("features") or {}).get("related_market_count")) for example in examples) / len(examples)
        else:
            avg_edges = 0.0
            avg_evidence = 0.0
            avg_related = 0.0

        top_related_confidences = []
        for run in runs:
            nodes = (run.payload or {}).get("graph", {}).get("nodes") or []
            related_confidences = [
                _to_float(node.get("confidence"))
                for node in nodes
                if node.get("type") == "RelatedMarket"
            ]
            if related_confidences:
                top_related_confidences.append(max(related_confidences))

        return {
            "avg_edges": avg_edges,
            "avg_evidence_nodes": avg_evidence,
            "avg_related_markets": avg_related,
            "avg_top_related_confidence": (
                sum(top_related_confidences) / len(top_related_confidences)
                if top_related_confidences
                else 0.0
            ),
            "avg_trace_rows": (
                sum(self._trace_count_for_run(run) for run in runs) / len(runs)
                if runs
                else 0.0
            ),
        }

    def _trace_count_for_run(self, run: GraphRun) -> int:
        prefetched = getattr(run, "_prefetched_objects_cache", {}).get("agent_traces")
        if prefetched is not None:
            return len(prefetched)
        return run.agent_traces.count()

    def _is_default_data_path(self) -> bool:
        return self.data_path == Path("ml_data") / "training_data.jsonl"

    @staticmethod
    def invalidate_cached_summary() -> None:
        cache.delete(BENCHMARK_SUMMARY_CACHE_KEY)


class ResolutionForecastService:
    def __init__(self, *, min_train_size: int = 8):
        self.min_train_size = min_train_size
        self.feature_extractor = SnapshotFeatureExtractor()

    def build_examples(self) -> list[dict[str, Any]]:
        examples: list[dict[str, Any]] = []
        snapshots = (
            MarketSnapshot.objects.select_related("resolution_label")
            .filter(resolution_label__isnull=False)
            .order_by("snapshot_at", "created_at")
        )
        for snapshot in snapshots:
            yes_probability = _snapshot_yes_probability(snapshot)
            target = _snapshot_resolution_target(snapshot)
            if yes_probability is None or target is None:
                continue
            features = self.feature_extractor.extract(snapshot, yes_probability=yes_probability)
            examples.append(
                {
                    "snapshot_id": snapshot.id,
                    "event_slug": snapshot.event_slug,
                    "event_title": snapshot.event_title,
                    "snapshot_at": snapshot.snapshot_at.isoformat(),
                    "source_kind": snapshot.source_kind,
                    "status": snapshot.status,
                    "yes_probability": yes_probability,
                    "target": target,
                    "features": features,
                    "feature_vector": features.as_vector(),
                }
            )
        return examples

    def export_examples(self) -> list[dict[str, Any]]:
        return [
            {
                "snapshot_id": example["snapshot_id"],
                "event_slug": example["event_slug"],
                "event_title": example["event_title"],
                "snapshot_at": example["snapshot_at"],
                "source_kind": example["source_kind"],
                "status": example["status"],
                "yes_probability": example["yes_probability"],
                "target": example["target"],
                "features": example["features"].as_dict(),
                "feature_vector": example["feature_vector"],
            }
            for example in self.build_examples()
        ]

    def run(self, *, persist: bool = True) -> dict[str, Any]:
        examples = self.build_examples()
        evaluation_rows: list[dict[str, Any]] = []
        latest_coefficients: list[float] = []

        if len(examples) > self.min_train_size:
            for index in range(self.min_train_size, len(examples)):
                train_examples = examples[:index]
                target_example = examples[index]
                model = BinaryLogisticRegression()
                model.fit(
                    [example["feature_vector"] for example in train_examples],
                    [int(example["target"]) for example in train_examples],
                )
                model_probability = _clip_probability(
                    model.predict_proba(target_example["feature_vector"])
                )
                baseline_probability = _clip_probability(target_example["yes_probability"])
                evaluation_rows.append(
                    {
                        "snapshot_id": target_example["snapshot_id"],
                        "event_slug": target_example["event_slug"],
                        "snapshot_at": target_example["snapshot_at"],
                        "target": int(target_example["target"]),
                        "baseline_probability": baseline_probability,
                        "model_probability": model_probability,
                    }
                )
                latest_coefficients = model.coefficients()

        metrics = self._forecast_metrics(evaluation_rows)
        report = {
            "task_type": "resolution_backtest",
            "title": "Resolution forecasting rolling backtest",
            "dataset_version": f"resolution_labels:{len(examples)}",
            "metrics": metrics,
            "example_count": len(examples),
            "evaluated_examples": len(evaluation_rows),
            "minimum_train_size": self.min_train_size,
        }
        if persist and evaluation_rows:
            ExperimentRun.objects.create(
                task_type="resolution_backtest",
                title="Resolution forecasting rolling backtest",
                dataset_version=f"resolution_labels:{len(examples)}",
                metrics=metrics,
                artifacts={
                    "evaluated_examples": evaluation_rows,
                    "latest_coefficients": latest_coefficients,
                },
                notes="Rolling expanding-window backtest against the market-implied YES probability baseline.",
            )
            BenchmarkSummaryService.invalidate_cached_summary()
        return report

    def _forecast_metrics(self, evaluation_rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not evaluation_rows:
            return {
                "example_count": 0,
                "positive_rate": 0.0,
                "baseline_brier": 0.0,
                "model_brier": 0.0,
                "brier_lift": 0.0,
                "baseline_log_loss": 0.0,
                "model_log_loss": 0.0,
                "log_loss_lift": 0.0,
                "baseline_accuracy": 0.0,
                "model_accuracy": 0.0,
                "baseline_calibration_error": 0.0,
                "model_calibration_error": 0.0,
            }

        targets = [int(row["target"]) for row in evaluation_rows]
        baseline_predictions = [_clip_probability(row["baseline_probability"]) for row in evaluation_rows]
        model_predictions = [_clip_probability(row["model_probability"]) for row in evaluation_rows]

        baseline_brier = self._brier_score(targets, baseline_predictions)
        model_brier = self._brier_score(targets, model_predictions)
        baseline_log_loss = self._log_loss(targets, baseline_predictions)
        model_log_loss = self._log_loss(targets, model_predictions)

        return {
            "example_count": len(evaluation_rows),
            "positive_rate": sum(targets) / len(targets),
            "baseline_brier": baseline_brier,
            "model_brier": model_brier,
            "brier_lift": baseline_brier - model_brier,
            "baseline_log_loss": baseline_log_loss,
            "model_log_loss": model_log_loss,
            "log_loss_lift": baseline_log_loss - model_log_loss,
            "baseline_accuracy": self._accuracy(targets, baseline_predictions),
            "model_accuracy": self._accuracy(targets, model_predictions),
            "baseline_calibration_error": self._calibration_error(targets, baseline_predictions),
            "model_calibration_error": self._calibration_error(targets, model_predictions),
        }

    def _brier_score(self, targets: list[int], predictions: list[float]) -> float:
        return sum((prediction - target) ** 2 for target, prediction in zip(targets, predictions, strict=False)) / len(targets)

    def _log_loss(self, targets: list[int], predictions: list[float]) -> float:
        losses = []
        for target, prediction in zip(targets, predictions, strict=False):
            bounded = min(max(prediction, 1e-6), 1.0 - 1e-6)
            losses.append(-(target * math.log(bounded) + (1 - target) * math.log(1.0 - bounded)))
        return sum(losses) / len(losses)

    def _accuracy(self, targets: list[int], predictions: list[float]) -> float:
        hits = 0
        for target, prediction in zip(targets, predictions, strict=False):
            hits += 1 if int(prediction >= 0.5) == target else 0
        return hits / len(targets)

    def _calibration_error(self, targets: list[int], predictions: list[float], *, bins: int = 5) -> float:
        bucket_size = 1.0 / bins
        error = 0.0
        sample_count = len(targets)
        for bucket_index in range(bins):
            lower = bucket_index * bucket_size
            upper = lower + bucket_size
            bucket_items = [
                (target, prediction)
                for target, prediction in zip(targets, predictions, strict=False)
                if lower <= prediction < upper or (bucket_index == bins - 1 and prediction == 1.0)
            ]
            if not bucket_items:
                continue
            target_mean = sum(target for target, _ in bucket_items) / len(bucket_items)
            prediction_mean = sum(prediction for _, prediction in bucket_items) / len(bucket_items)
            error += (len(bucket_items) / sample_count) * abs(target_mean - prediction_mean)
        return error


class RelatedMarketRankingBenchmarkService:
    def __init__(self, *, hard_negative_pool_size: int = 12):
        self.hard_negative_pool_size = hard_negative_pool_size

    def build_examples(self) -> list[dict[str, Any]]:
        runs = list(GraphRun.objects.order_by("created_at"))
        catalog = self._candidate_catalog(runs)
        examples: list[dict[str, Any]] = []

        for run in runs:
            payload = run.payload or {}
            graph = payload.get("graph", {})
            positives = self._positive_related_markets(graph)
            if not positives or len(catalog) <= len(positives):
                continue

            event = payload.get("event", {})
            source_snapshot = run.source_snapshot or payload.get("context", {}).get("source_snapshot", {})
            source_tokens = _ranking_tokens(
                event.get("title"),
                event.get("description"),
                event.get("tags") or source_snapshot.get("tags") or [],
                source_snapshot.get("category"),
            )
            context_tokens = set(source_tokens)
            context_tokens.update(self._graph_context_tokens(graph))
            source_category = str(source_snapshot.get("category") or event.get("category") or "").strip().lower()

            negatives = []
            for candidate_key, candidate in catalog.items():
                if candidate_key in positives:
                    continue
                baseline_score = self._baseline_score(source_tokens, candidate["title_tokens"])
                negatives.append((baseline_score, candidate_key, candidate))
            negatives.sort(key=lambda item: (item[0], item[2]["selection_count"]), reverse=True)
            selected_negatives = negatives[: self.hard_negative_pool_size]

            candidate_rows = []
            for candidate_key, candidate in positives.items():
                candidate_rows.append(
                    self._candidate_row(
                        candidate_key=candidate_key,
                        candidate=candidate,
                        source_tokens=source_tokens,
                        context_tokens=context_tokens,
                        source_category=source_category,
                        relevance=candidate["relevance"],
                    )
                )
            for _baseline_score_value, candidate_key, candidate in selected_negatives:
                candidate_rows.append(
                    self._candidate_row(
                        candidate_key=candidate_key,
                        candidate=candidate,
                        source_tokens=source_tokens,
                        context_tokens=context_tokens,
                        source_category=source_category,
                        relevance=0,
                    )
                )

            if len(candidate_rows) <= len(positives):
                continue

            examples.append(
                {
                    "run_id": str(run.id),
                    "event_title": run.event_title or event.get("title") or "Untitled run",
                    "event_slug": run.event_slug,
                    "candidate_count": len(candidate_rows),
                    "positive_count": len(positives),
                    "source_tokens": sorted(source_tokens),
                    "context_tokens": sorted(context_tokens),
                    "candidates": sorted(candidate_rows, key=lambda item: item["title"]),
                }
            )
        return examples

    def export_examples(self) -> list[dict[str, Any]]:
        return self.build_examples()

    def run(self, *, persist: bool = True) -> dict[str, Any]:
        examples = self.build_examples()
        metrics = self._ranking_metrics(examples)
        report = {
            "task_type": "related_market_ranking",
            "title": "Related-market ranking silver benchmark",
            "dataset_version": f"graph_runs:{GraphRun.objects.count()}",
            "metrics": metrics,
            "example_count": len(examples),
            "hard_negative_pool_size": self.hard_negative_pool_size,
        }
        if persist and examples:
            ExperimentRun.objects.create(
                task_type="related_market_ranking",
                title="Related-market ranking silver benchmark",
                dataset_version=report["dataset_version"],
                metrics=metrics,
                artifacts={
                    "examples": examples[:20],
                    "hard_negative_pool_size": self.hard_negative_pool_size,
                },
                notes=(
                    "Silver-label ranking benchmark over persisted related-market selections. "
                    "Each query uses selected related markets as positives and hard negatives mined "
                    "from the global related-market catalog."
                ),
            )
            BenchmarkSummaryService.invalidate_cached_summary()
        return report

    def _candidate_catalog(self, runs: list[GraphRun]) -> dict[str, dict[str, Any]]:
        catalog: dict[str, dict[str, Any]] = {}
        for run in runs:
            payload = run.payload or {}
            graph = payload.get("graph", {})
            source_snapshot = run.source_snapshot or payload.get("context", {}).get("source_snapshot", {})
            source_category = str(source_snapshot.get("category") or "").strip()
            for node in graph.get("nodes") or []:
                if node.get("type") != "RelatedMarket":
                    continue
                title = str(node.get("label") or "").strip()
                if not title:
                    continue
                key = _related_market_key(title)
                candidate = catalog.setdefault(
                    key,
                    {
                        "title": title,
                        "summary": str(node.get("summary") or node.get("description") or "").strip(),
                        "categories": Counter(),
                        "selection_count": 0,
                        "avg_confidence": 0.0,
                        "metadata_text": [],
                        "title_tokens": set(),
                        "full_tokens": set(),
                    },
                )
                candidate["selection_count"] += 1
                candidate["avg_confidence"] += _to_float(node.get("confidence"))
                if source_category:
                    candidate["categories"][source_category.lower()] += 1
                for item in node.get("metadata") or []:
                    if isinstance(item, dict):
                        value = str(item.get("value") or "").strip()
                        if value:
                            candidate["metadata_text"].append(value)
                candidate["title_tokens"] = _ranking_tokens(candidate["title"])
                candidate["full_tokens"] = _ranking_tokens(
                    candidate["title"],
                    candidate["summary"],
                    candidate["metadata_text"],
                )
        for candidate in catalog.values():
            if candidate["selection_count"]:
                candidate["avg_confidence"] = candidate["avg_confidence"] / candidate["selection_count"]
        return catalog

    def _positive_related_markets(self, graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
        positives: dict[str, dict[str, Any]] = {}
        for node in graph.get("nodes") or []:
            if node.get("type") != "RelatedMarket":
                continue
            title = str(node.get("label") or "").strip()
            if not title:
                continue
            key = _related_market_key(title)
            positives[key] = {
                "title": title,
                "summary": str(node.get("summary") or node.get("description") or "").strip(),
                "categories": Counter(),
                "selection_count": 1,
                "avg_confidence": _to_float(node.get("confidence")),
                "metadata_text": [
                    str(item.get("value") or "").strip()
                    for item in node.get("metadata") or []
                    if isinstance(item, dict) and str(item.get("value") or "").strip()
                ],
                "title_tokens": _ranking_tokens(title),
                "full_tokens": _ranking_tokens(
                    title,
                    str(node.get("summary") or node.get("description") or "").strip(),
                    [
                        str(item.get("value") or "").strip()
                        for item in node.get("metadata") or []
                        if isinstance(item, dict)
                    ],
                ),
                "relevance": self._relevance_from_confidence(_to_float(node.get("confidence"))),
            }
        return positives

    def _graph_context_tokens(self, graph: dict[str, Any]) -> set[str]:
        context_tokens: set[str] = set()
        for node in graph.get("nodes") or []:
            if node.get("type") not in {"Entity", "Evidence", "Hypothesis"}:
                continue
            context_tokens.update(
                _ranking_tokens(
                    node.get("label"),
                    node.get("summary"),
                    node.get("description"),
                    [
                        str(item.get("value") or "").strip()
                        for item in node.get("metadata") or []
                        if isinstance(item, dict)
                    ],
                )
            )
        return context_tokens

    def _candidate_row(
        self,
        *,
        candidate_key: str,
        candidate: dict[str, Any],
        source_tokens: set[str],
        context_tokens: set[str],
        source_category: str,
        relevance: int,
    ) -> dict[str, Any]:
        baseline_score = self._baseline_score(source_tokens, candidate["title_tokens"])
        model_score = self._context_score(
            source_tokens=source_tokens,
            context_tokens=context_tokens,
            candidate_tokens=candidate["full_tokens"],
            source_category=source_category,
            candidate_categories=candidate["categories"],
            popularity=candidate["selection_count"],
        )
        return {
            "candidate_key": candidate_key,
            "title": candidate["title"],
            "relevance": relevance,
            "baseline_score": round(baseline_score, 4),
            "model_score": round(model_score, 4),
            "selection_count": candidate["selection_count"],
            "avg_confidence": round(_to_float(candidate["avg_confidence"]), 4),
        }

    def _baseline_score(self, source_tokens: set[str], candidate_tokens: set[str]) -> float:
        overlap = len(source_tokens & candidate_tokens)
        return (_jaccard_score(source_tokens, candidate_tokens) * 0.75) + min(overlap * 0.08, 0.32)

    def _context_score(
        self,
        *,
        source_tokens: set[str],
        context_tokens: set[str],
        candidate_tokens: set[str],
        source_category: str,
        candidate_categories: Counter,
        popularity: int,
    ) -> float:
        title_overlap = _jaccard_score(source_tokens, candidate_tokens)
        context_overlap = _jaccard_score(context_tokens, candidate_tokens)
        context_only_overlap = len((context_tokens - source_tokens) & candidate_tokens)
        category_bonus = 0.08 if source_category and candidate_categories.get(source_category, 0) else 0.0
        popularity_penalty = min(max(popularity - 1, 0) * 0.01, 0.08)
        return (
            title_overlap * 0.4
            + context_overlap * 0.35
            + min(context_only_overlap * 0.08, 0.24)
            + category_bonus
            - popularity_penalty
        )

    def _ranking_metrics(self, examples: list[dict[str, Any]]) -> dict[str, Any]:
        if not examples:
            return {
                "example_count": 0,
                "avg_candidate_count": 0.0,
                "avg_positive_count": 0.0,
                "baseline_recall_at_3": 0.0,
                "model_recall_at_3": 0.0,
                "recall_at_3_lift": 0.0,
                "baseline_ndcg_at_5": 0.0,
                "model_ndcg_at_5": 0.0,
                "ndcg_at_5_lift": 0.0,
                "baseline_mrr": 0.0,
                "model_mrr": 0.0,
                "mrr_lift": 0.0,
            }

        avg_candidate_count = sum(example["candidate_count"] for example in examples) / len(examples)
        avg_positive_count = sum(example["positive_count"] for example in examples) / len(examples)
        baseline_rows = [self._per_example_metrics(example["candidates"], score_key="baseline_score") for example in examples]
        model_rows = [self._per_example_metrics(example["candidates"], score_key="model_score") for example in examples]

        baseline_recall = sum(row["recall_at_3"] for row in baseline_rows) / len(baseline_rows)
        model_recall = sum(row["recall_at_3"] for row in model_rows) / len(model_rows)
        baseline_ndcg = sum(row["ndcg_at_5"] for row in baseline_rows) / len(baseline_rows)
        model_ndcg = sum(row["ndcg_at_5"] for row in model_rows) / len(model_rows)
        baseline_mrr = sum(row["mrr"] for row in baseline_rows) / len(baseline_rows)
        model_mrr = sum(row["mrr"] for row in model_rows) / len(model_rows)

        return {
            "example_count": len(examples),
            "avg_candidate_count": round(avg_candidate_count, 2),
            "avg_positive_count": round(avg_positive_count, 2),
            "baseline_recall_at_3": round(baseline_recall, 4),
            "model_recall_at_3": round(model_recall, 4),
            "recall_at_3_lift": round(model_recall - baseline_recall, 4),
            "baseline_ndcg_at_5": round(baseline_ndcg, 4),
            "model_ndcg_at_5": round(model_ndcg, 4),
            "ndcg_at_5_lift": round(model_ndcg - baseline_ndcg, 4),
            "baseline_mrr": round(baseline_mrr, 4),
            "model_mrr": round(model_mrr, 4),
            "mrr_lift": round(model_mrr - baseline_mrr, 4),
        }

    def _per_example_metrics(self, candidates: list[dict[str, Any]], *, score_key: str) -> dict[str, float]:
        ranked = sorted(
            candidates,
            key=lambda item: (_to_float(item.get(score_key)), _to_float(item.get("avg_confidence")), item.get("title", "")),
            reverse=True,
        )
        positives = [candidate for candidate in ranked if _to_float(candidate.get("relevance")) > 0]
        if not positives:
            return {"recall_at_3": 0.0, "ndcg_at_5": 0.0, "mrr": 0.0}
        positive_count = len(positives)
        top3 = ranked[:3]
        recall_at_3 = sum(1 for candidate in top3 if _to_float(candidate.get("relevance")) > 0) / positive_count
        ndcg_at_5 = self._ndcg_at_k(ranked, k=5)
        reciprocal_rank = 0.0
        for index, candidate in enumerate(ranked, start=1):
            if _to_float(candidate.get("relevance")) > 0:
                reciprocal_rank = 1.0 / index
                break
        return {
            "recall_at_3": recall_at_3,
            "ndcg_at_5": ndcg_at_5,
            "mrr": reciprocal_rank,
        }

    def _ndcg_at_k(self, ranked: list[dict[str, Any]], *, k: int) -> float:
        def dcg(items: list[dict[str, Any]]) -> float:
            total = 0.0
            for index, item in enumerate(items, start=1):
                relevance = _to_float(item.get("relevance"))
                if relevance <= 0:
                    continue
                total += (2**relevance - 1) / math.log2(index + 1)
            return total

        actual = dcg(ranked[:k])
        ideal = dcg(sorted(ranked, key=lambda item: _to_float(item.get("relevance")), reverse=True)[:k])
        if ideal <= 0:
            return 0.0
        return actual / ideal

    def _relevance_from_confidence(self, confidence: float) -> int:
        if confidence >= 0.82:
            return 3
        if confidence >= 0.68:
            return 2
        return 1


class RelatedMarketJudgmentService:
    ALLOWED_LABELS = frozenset(RELATED_MARKET_USEFULNESS_LABELS)

    def review_queue(self, *, limit_runs: int = 8, per_run_limit: int = 5) -> dict[str, Any]:
        runs = list(
            GraphRun.objects.prefetch_related(
                Prefetch(
                    "related_market_judgments",
                    queryset=RelatedMarketJudgment.objects.order_by("candidate_key", "-updated_at"),
                )
            )
            .order_by("-updated_at", "-created_at")[:limit_runs]
        )
        cases = []
        for run in runs:
            ranking = MarketBriefService().related_market_ranking(run)[:per_run_limit]
            if not ranking:
                continue
            judgment_map: dict[str, list[RelatedMarketJudgment]] = {}
            for judgment in run.related_market_judgments.all():
                judgment_map.setdefault(judgment.candidate_key, []).append(judgment)
            candidates = []
            for index, candidate in enumerate(ranking, start=1):
                candidate_title = str(candidate.get("title") or "").strip()
                candidate_key = _related_market_key(candidate_title)
                candidate_judgments = [
                    judgment
                    for alias in _related_market_key_aliases(candidate_title)
                    for judgment in judgment_map.get(alias, [])
                ]
                unique_judgments = {
                    judgment.id: judgment
                    for judgment in candidate_judgments
                }
                consensus = self._candidate_consensus(list(unique_judgments.values()))
                priority_score = self._candidate_priority(
                    confidence=_to_float(candidate.get("confidence")),
                    rank=index,
                    consensus=consensus,
                )
                candidates.append(
                    {
                        "candidate_key": candidate_key,
                        "title": candidate_title,
                        "summary": candidate.get("summary") or "",
                        "confidence": _to_float(candidate.get("confidence")),
                        "source_url": candidate.get("source_url") or "",
                        "rank": index,
                        "judgment": consensus["latest_judgment"],
                        "judgments": consensus["judgments"],
                        "consensus": consensus,
                        "review_state": consensus["review_state"],
                        "review_state_display": consensus["review_state_display"],
                        "needs_attention": consensus["review_state"] != "agreed",
                        "is_pending": consensus["review_state"] == "pending",
                        "priority_score": priority_score,
                    }
                )
            candidates.sort(key=lambda item: (-item["priority_score"], item["rank"], item["title"]))
            pending_count = sum(1 for candidate in candidates if candidate["review_state"] == "pending")
            second_review_count = sum(
                1 for candidate in candidates if candidate["review_state"] == "needs_second_review"
            )
            contested_count = sum(
                1 for candidate in candidates if candidate["review_state"] == "contested"
            )
            cases.append(
                {
                    "run_id": str(run.id),
                    "event_title": run.event_title or "Untitled run",
                    "event_slug": run.event_slug or "",
                    "brief_url": reverse("web:market_brief", kwargs={"run_id": run.id}),
                    "app_url": f"{reverse('web:dashboard')}?url={run.source_url}",
                    "pending_count": pending_count,
                    "needs_second_review_count": second_review_count,
                    "contested_count": contested_count,
                    "attention_count": pending_count + second_review_count + contested_count,
                    "judged_count": len(
                        [
                            candidate
                            for candidate in candidates
                            if candidate["review_state"] != "pending"
                        ]
                    ),
                    "candidates": candidates,
                    "updated_at": run.updated_at.isoformat(),
                    "priority_score": max((candidate["priority_score"] for candidate in candidates), default=0.0),
                }
            )
        cases.sort(
            key=lambda item: (
                -int(item["attention_count"]),
                -int(item["contested_count"]),
                -int(item["needs_second_review_count"]),
                -_to_float(item["priority_score"]),
                item["event_title"],
            )
        )
        return {
            "summary": self.summary(cases=cases),
            "cases": cases,
        }

    def summary(self, *, cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        judgments = list(RelatedMarketJudgment.objects.order_by("-updated_at"))
        label_counts = Counter(judgment.usefulness_label for judgment in judgments)
        reviewed_run_ids = {judgment.graph_run_id for judgment in judgments}
        cases = cases if cases is not None else self.review_queue()["cases"]
        candidate_rows = [candidate for case in cases for candidate in case.get("candidates", [])]
        reviewer_keys = {
            judgment.reviewer_key or _normalized_reviewer_key(judgment.reviewer)
            for judgment in judgments
        }
        agreement_rates = [
            _to_float(candidate.get("consensus", {}).get("agreement_rate"))
            for candidate in candidate_rows
            if int(candidate.get("consensus", {}).get("judgment_count") or 0) > 0
        ]
        return {
            "judgment_count": len(judgments),
            "reviewed_runs": len(reviewed_run_ids),
            "pending_cases": sum(1 for case in cases if case.get("pending_count")),
            "pending_candidates": sum(1 for candidate in candidate_rows if candidate["review_state"] == "pending"),
            "needs_second_review_candidates": sum(
                1 for candidate in candidate_rows if candidate["review_state"] == "needs_second_review"
            ),
            "contested_candidates": sum(
                1 for candidate in candidate_rows if candidate["review_state"] == "contested"
            ),
            "agreed_candidates": sum(1 for candidate in candidate_rows if candidate["review_state"] == "agreed"),
            "reviewer_count": len(reviewer_keys - {"anonymous"}),
            "avg_agreement_rate": (
                round(sum(agreement_rates) / len(agreement_rates), 4) if agreement_rates else 0.0
            ),
            "multi_reviewer_candidate_rate": (
                round(
                    (
                        sum(
                            1
                            for candidate in candidate_rows
                            if int(candidate.get("consensus", {}).get("reviewer_count") or 0) > 1
                        )
                        / max(
                            sum(
                                1
                                for candidate in candidate_rows
                                if int(candidate.get("consensus", {}).get("judgment_count") or 0) > 0
                            ),
                            1,
                        )
                    ),
                    4,
                )
                if candidate_rows
                else 0.0
            ),
            "label_breakdown": [
                {
                    "label": _usefulness_label_display(label),
                    "value": label,
                    "count": count,
                }
                for label, count in label_counts.most_common()
            ],
        }

    def consensus_records(self) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[RelatedMarketJudgment]] = {}
        for judgment in RelatedMarketJudgment.objects.select_related("graph_run").order_by(
            "graph_run_id", "candidate_key", "-updated_at"
        ):
            grouped.setdefault((str(judgment.graph_run_id), judgment.candidate_key), []).append(judgment)

        records = []
        for (run_id, candidate_key), judgments in grouped.items():
            consensus = self._candidate_consensus(judgments)
            if not consensus["judgment_count"]:
                continue
            records.append(
                {
                    "graph_run_id": run_id,
                    "event_title": judgments[0].graph_run.event_title,
                    "candidate_key": candidate_key,
                    "candidate_title": judgments[0].candidate_title,
                    "consensus_label": consensus["consensus_label"],
                    "agreement_rate": consensus["agreement_rate"],
                    "review_state": consensus["review_state"],
                    "reviewer_count": consensus["reviewer_count"],
                    "average_relevance": consensus["average_relevance"],
                    "label_votes": consensus["label_votes"],
                    "latest_updated_at": consensus["latest_updated_at"],
                }
            )
        return records

    def upsert_judgment(
        self,
        run: GraphRun,
        *,
        candidate_key: str,
        candidate_title: str,
        usefulness_label: str,
        notes: str = "",
        reviewer: str = "",
        source: str = "manual-web",
        candidate_summary: str = "",
        candidate_source_url: str = "",
        candidate_rank: int = 0,
        candidate_confidence: float = 0.0,
    ) -> RelatedMarketJudgment:
        normalized_label = str(usefulness_label or "").strip().lower()
        if normalized_label not in self.ALLOWED_LABELS:
            raise ValueError("Usefulness label must be core, watch, or reject.")
        title = str(candidate_title or "").strip()
        key = _related_market_key(candidate_key or title)
        reviewer_name = str(reviewer or "").strip()
        reviewer_key = _normalized_reviewer_key(reviewer_name)
        if not key or not title:
            raise ValueError("Candidate key and title are required.")
        judgment, _created = RelatedMarketJudgment.objects.update_or_create(
            graph_run=run,
            candidate_key=key,
            reviewer_key=reviewer_key,
            defaults={
                "candidate_title": title,
                "candidate_summary": str(candidate_summary or "").strip(),
                "candidate_source_url": str(candidate_source_url or "").strip(),
                "candidate_rank": max(int(candidate_rank or 0), 0),
                "candidate_confidence": _to_float(candidate_confidence),
                "usefulness_label": normalized_label,
                "notes": str(notes or "").strip(),
                "reviewer": reviewer_name,
                "reviewer_key": reviewer_key,
                "source": str(source or "manual-web").strip() or "manual-web",
            },
        )
        BenchmarkSummaryService.invalidate_cached_summary()
        return judgment

    def _serialize_judgment(self, judgment: RelatedMarketJudgment) -> dict[str, Any]:
        return {
            "candidate_key": judgment.candidate_key,
            "candidate_title": judgment.candidate_title,
            "usefulness_label": judgment.usefulness_label,
            "usefulness_label_display": _usefulness_label_display(judgment.usefulness_label),
            "notes": judgment.notes,
            "reviewer": judgment.reviewer,
            "reviewer_key": judgment.reviewer_key or _normalized_reviewer_key(judgment.reviewer),
            "updated_at": judgment.updated_at.isoformat(),
        }

    def _candidate_consensus(self, judgments: list[RelatedMarketJudgment]) -> dict[str, Any]:
        if not judgments:
            return {
                "judgment_count": 0,
                "reviewer_count": 0,
                "consensus_label": "",
                "consensus_label_display": "Unreviewed",
                "agreement_rate": 0.0,
                "average_relevance": 0.0,
                "review_state": "pending",
                "review_state_display": _review_state_display("pending"),
                "label_votes": {},
                "judgments": [],
                "latest_judgment": None,
                "latest_updated_at": "",
            }

        ordered = sorted(judgments, key=lambda item: (item.updated_at, item.id), reverse=True)
        label_counts = Counter(judgment.usefulness_label for judgment in judgments)
        reviewer_keys = {
            judgment.reviewer_key or _normalized_reviewer_key(judgment.reviewer)
            for judgment in judgments
        }
        top_label, top_count = max(
            label_counts.items(),
            key=lambda item: (item[1], _usefulness_label_to_relevance(item[0]), item[0]),
        )
        review_state = "agreed"
        if len(judgments) == 1:
            review_state = "needs_second_review"
        elif len(label_counts) > 1:
            review_state = "contested"
        agreement_rate = top_count / len(judgments)
        average_relevance = sum(
            _usefulness_label_to_relevance(judgment.usefulness_label) for judgment in judgments
        ) / len(judgments)
        serialized = [self._serialize_judgment(judgment) for judgment in ordered]
        return {
            "judgment_count": len(judgments),
            "reviewer_count": len(reviewer_keys),
            "consensus_label": top_label,
            "consensus_label_display": _usefulness_label_display(top_label),
            "agreement_rate": round(agreement_rate, 4),
            "average_relevance": round(average_relevance, 4),
            "review_state": review_state,
            "review_state_display": _review_state_display(review_state),
            "label_votes": dict(label_counts),
            "judgments": serialized,
            "latest_judgment": serialized[0],
            "latest_updated_at": ordered[0].updated_at.isoformat(),
        }

    def _candidate_priority(self, *, confidence: float, rank: int, consensus: dict[str, Any]) -> float:
        state = consensus["review_state"]
        base = {
            "pending": 40.0,
            "needs_second_review": 28.0,
            "contested": 34.0,
            "agreed": 8.0,
        }.get(state, 0.0)
        confidence_bonus = min(max(confidence, 0.0), 1.0) * 10.0
        disagreement_bonus = (1.0 - _to_float(consensus.get("agreement_rate"))) * 6.0
        rank_bonus = max(0, 6 - max(rank, 1))
        return round(base + confidence_bonus + disagreement_bonus + rank_bonus, 4)


class RelatedMarketUsefulnessBenchmarkService:
    def __init__(self, *, min_judged_candidates: int = 3, min_reviewers_per_candidate: int = 1):
        self.min_judged_candidates = min_judged_candidates
        self.min_reviewers_per_candidate = max(int(min_reviewers_per_candidate), 1)
        self.silver_service = RelatedMarketRankingBenchmarkService()
        self.judgment_service = RelatedMarketJudgmentService()

    def build_examples(self) -> list[dict[str, Any]]:
        judgments_by_run: dict[str, dict[str, list[RelatedMarketJudgment]]] = {}
        for judgment in RelatedMarketJudgment.objects.select_related("graph_run").order_by(
            "graph_run_id", "candidate_key", "reviewer_key", "-updated_at"
        ):
            judgments_by_run.setdefault(str(judgment.graph_run_id), {}).setdefault(
                judgment.candidate_key,
                [],
            ).append(judgment)

        examples: list[dict[str, Any]] = []
        for run_id, candidate_judgments in judgments_by_run.items():
            run = next(iter(candidate_judgments.values()))[0].graph_run
            candidate_rows = self._candidate_rows_for_run(run, candidate_judgments)
            positive_count = sum(
                1 for candidate in candidate_rows if _to_float(candidate.get("relevance")) > 0
            )
            if len(candidate_rows) < self.min_judged_candidates or positive_count == 0:
                continue
            reviewed_candidates = [candidate for candidate in candidate_rows if candidate["reviewer_count"] > 0]
            multi_reviewer_candidates = [
                candidate for candidate in reviewed_candidates if candidate["reviewer_count"] > 1
            ]
            examples.append(
                {
                    "run_id": run_id,
                    "event_title": run.event_title or "Untitled run",
                    "event_slug": run.event_slug,
                    "candidate_count": len(candidate_rows),
                    "positive_count": positive_count,
                    "judged_labels": Counter(
                        candidate.get("usefulness_label") or "reject"
                        for candidate in candidate_rows
                    ),
                    "avg_agreement_rate": round(
                        (
                            sum(_to_float(candidate.get("agreement_rate")) for candidate in reviewed_candidates)
                            / len(reviewed_candidates)
                        )
                        if reviewed_candidates
                        else 0.0,
                        4,
                    ),
                    "contested_candidate_count": sum(
                        1 for candidate in candidate_rows if candidate.get("review_state") == "contested"
                    ),
                    "multi_reviewer_candidate_count": len(multi_reviewer_candidates),
                    "candidates": candidate_rows,
                }
            )
        return examples

    def export_examples(self) -> list[dict[str, Any]]:
        return [
            {
                **example,
                "judged_labels": dict(example["judged_labels"]),
            }
            for example in self.build_examples()
        ]

    def run(self, *, persist: bool = True) -> dict[str, Any]:
        examples = self.build_examples()
        metrics = self.silver_service._ranking_metrics(examples)
        judgment_count = RelatedMarketJudgment.objects.count()
        candidate_count = sum(example["candidate_count"] for example in examples)
        reviewed_candidates = [
            candidate
            for example in examples
            for candidate in example["candidates"]
            if int(candidate.get("reviewer_count") or 0) > 0
        ]
        multi_reviewer_candidates = [
            candidate for candidate in reviewed_candidates if int(candidate.get("reviewer_count") or 0) > 1
        ]
        contested_candidates = [
            candidate for candidate in reviewed_candidates if candidate.get("review_state") == "contested"
        ]
        metrics.update(
            {
                "judgment_count": judgment_count,
                "candidate_count": candidate_count,
                "avg_reviewer_count_per_candidate": round(
                    (
                        sum(int(candidate.get("reviewer_count") or 0) for candidate in reviewed_candidates)
                        / len(reviewed_candidates)
                    )
                    if reviewed_candidates
                    else 0.0,
                    4,
                ),
                "avg_agreement_rate": round(
                    (
                        sum(_to_float(candidate.get("agreement_rate")) for candidate in reviewed_candidates)
                        / len(reviewed_candidates)
                    )
                    if reviewed_candidates
                    else 0.0,
                    4,
                ),
                "multi_reviewer_candidate_rate": round(
                    (len(multi_reviewer_candidates) / len(reviewed_candidates)) if reviewed_candidates else 0.0,
                    4,
                ),
                "contested_candidate_rate": round(
                    (len(contested_candidates) / len(reviewed_candidates)) if reviewed_candidates else 0.0,
                    4,
                ),
            }
        )
        report = {
            "task_type": "related_market_usefulness",
            "title": "Human-labeled related-market usefulness benchmark",
            "dataset_version": f"judgments:{judgment_count}|runs:{GraphRun.objects.count()}",
            "metrics": metrics,
            "example_count": len(examples),
            "judgment_count": judgment_count,
            "minimum_judged_candidates": self.min_judged_candidates,
            "minimum_reviewers_per_candidate": self.min_reviewers_per_candidate,
        }
        if persist and examples:
            ExperimentRun.objects.create(
                task_type="related_market_usefulness",
                title="Human-labeled related-market usefulness benchmark",
                dataset_version=report["dataset_version"],
                metrics=metrics,
                artifacts={
                    "examples": self.export_examples()[:20],
                    "judgment_count": judgment_count,
                    "minimum_judged_candidates": self.min_judged_candidates,
                    "minimum_reviewers_per_candidate": self.min_reviewers_per_candidate,
                },
                notes=(
                    "Human-labeled ranking benchmark over judged related markets. "
                    "Judgments are captured through ChaosWing's review queue, aggregated into "
                    "reviewer-aware consensus labels, and scored against the same lexical and "
                    "context-aware rerankers used in the silver benchmark."
                ),
            )
            BenchmarkSummaryService.invalidate_cached_summary()
        return report

    def _candidate_rows_for_run(
        self,
        run: GraphRun,
        candidate_judgments: dict[str, list[RelatedMarketJudgment]],
    ) -> list[dict[str, Any]]:
        payload = run.payload or {}
        graph = payload.get("graph", {})
        event = payload.get("event", {})
        source_snapshot = run.source_snapshot or payload.get("context", {}).get("source_snapshot", {})
        source_tokens = _ranking_tokens(
            event.get("title"),
            event.get("description"),
            event.get("tags") or source_snapshot.get("tags") or [],
            source_snapshot.get("category"),
        )
        context_tokens = set(source_tokens)
        context_tokens.update(self.silver_service._graph_context_tokens(graph))
        source_category = str(source_snapshot.get("category") or event.get("category") or "").strip().lower()
        node_map: dict[str, dict[str, Any]] = {}
        for node in graph.get("nodes") or []:
            if node.get("type") != "RelatedMarket":
                continue
            label = str(node.get("label") or "").strip()
            if not label:
                continue
            for alias in _related_market_key_aliases(label):
                node_map.setdefault(alias, node)
        candidate_rows: list[dict[str, Any]] = []
        for candidate_key, judgments in candidate_judgments.items():
            consensus = self.judgment_service._candidate_consensus(judgments)
            if consensus["reviewer_count"] < self.min_reviewers_per_candidate:
                continue
            node = node_map.get(candidate_key)
            if node is None and judgments:
                latest_judgment = max(judgments, key=lambda item: (item.updated_at, item.id))
                for alias in _related_market_key_aliases(latest_judgment.candidate_title):
                    node = node_map.get(alias)
                    if node is not None:
                        break
            if node is None:
                continue
            latest_judgment = max(judgments, key=lambda item: (item.updated_at, item.id))
            metadata_values = [
                str(item.get("value") or "").strip()
                for item in node.get("metadata") or []
                if isinstance(item, dict) and str(item.get("value") or "").strip()
            ]
            categories = Counter()
            if source_category:
                categories[source_category] += 1
            candidate = {
                "title": latest_judgment.candidate_title,
                "summary": latest_judgment.candidate_summary or str(node.get("summary") or node.get("description") or "").strip(),
                "categories": categories,
                "selection_count": 1,
                "avg_confidence": latest_judgment.candidate_confidence or _to_float(node.get("confidence")),
                "metadata_text": metadata_values,
                "title_tokens": _ranking_tokens(latest_judgment.candidate_title),
                "full_tokens": _ranking_tokens(
                    latest_judgment.candidate_title,
                    latest_judgment.candidate_summary or str(node.get("summary") or node.get("description") or "").strip(),
                    metadata_values,
                ),
            }
            candidate_row = self.silver_service._candidate_row(
                candidate_key=candidate_key,
                candidate=candidate,
                source_tokens=source_tokens,
                context_tokens=context_tokens,
                source_category=source_category,
                relevance=consensus["average_relevance"],
            )
            candidate_row["usefulness_label"] = consensus["consensus_label"]
            candidate_row["review_state"] = consensus["review_state"]
            candidate_row["agreement_rate"] = consensus["agreement_rate"]
            candidate_row["reviewer_count"] = consensus["reviewer_count"]
            candidate_row["notes"] = latest_judgment.notes
            candidate_rows.append(candidate_row)
        return candidate_rows


class WatchlistService:
    def featured(self) -> list[dict[str, Any]]:
        cached = cache.get(WATCHLISTS_FEATURED_CACHE_KEY)
        if cached is not None:
            return cached
        watchlists = list(Watchlist.objects.filter(is_featured=True).order_by("title"))
        if watchlists:
            serialized = [self._serialize_watchlist(watchlist) for watchlist in watchlists]
        else:
            serialized = DEFAULT_WATCHLISTS
        cache.set(WATCHLISTS_FEATURED_CACHE_KEY, serialized, timeout=WATCHLIST_CACHE_TTL_SECONDS)
        return serialized

    def all(self) -> list[dict[str, Any]]:
        cached = cache.get(WATCHLISTS_ALL_CACHE_KEY)
        if cached is not None:
            return cached
        watchlists = list(Watchlist.objects.order_by("title"))
        if watchlists:
            serialized = [self._serialize_watchlist(watchlist) for watchlist in watchlists]
        else:
            serialized = DEFAULT_WATCHLISTS
        cache.set(WATCHLISTS_ALL_CACHE_KEY, serialized, timeout=WATCHLIST_CACHE_TTL_SECONDS)
        return serialized

    def urls(self) -> list[str]:
        urls: list[str] = []
        for watchlist in self.all():
            for item in watchlist.get("items", []):
                url = str(item.get("url") or "").strip()
                if url and url not in urls:
                    urls.append(url)
        return urls

    def _serialize_watchlist(self, watchlist: Watchlist) -> dict[str, Any]:
        return {
            "slug": watchlist.slug,
            "title": watchlist.title,
            "thesis": watchlist.thesis,
            "summary": watchlist.summary,
            "cadence": watchlist.cadence,
            "items": watchlist.items or [],
        }

    @staticmethod
    def invalidate_cache() -> None:
        cache.delete_many([WATCHLISTS_ALL_CACHE_KEY, WATCHLISTS_FEATURED_CACHE_KEY])


class LandingStatsService:
    def build(self) -> dict[str, Any]:
        return {
            "total_runs": GraphRun.objects.count(),
            "recent_titles": list(
                GraphRun.objects.order_by("-created_at")
                .values_list("event_title", flat=True)
                .exclude(event_title="")[:6]
            ),
        }

    def build_cached(self, *, force_refresh: bool = False) -> dict[str, Any]:
        if force_refresh:
            self.invalidate_cached_stats()

        cached = cache.get(LANDING_STATS_CACHE_KEY)
        if cached is not None:
            return cached

        stats = self.build()
        cache.set(
            LANDING_STATS_CACHE_KEY,
            stats,
            timeout=getattr(settings, "CHAOSWING_BENCHMARK_CACHE_TTL", 120),
        )
        return stats

    @staticmethod
    def invalidate_cached_stats() -> None:
        cache.delete(LANDING_STATS_CACHE_KEY)


class DatasetBuilderService:
    def build_records(self) -> dict[str, list[dict[str, Any]]]:
        forecast_examples = ResolutionForecastService().export_examples()
        ranking_examples = RelatedMarketRankingBenchmarkService().export_examples()
        human_ranking_examples = RelatedMarketUsefulnessBenchmarkService().export_examples()
        trust_examples = AgentTrustBenchmarkService().export_examples()
        judgment_service = RelatedMarketJudgmentService()
        return {
            "runs": [
                {
                    "id": str(run.id),
                    "event_slug": run.event_slug,
                    "event_title": run.event_title,
                    "mode": run.mode,
                    "status": run.status,
                    "graph_stats": run.graph_stats,
                    "created_at": run.created_at.isoformat(),
                    "updated_at": run.updated_at.isoformat(),
                }
                for run in GraphRun.objects.order_by("-created_at")
            ],
            "snapshots": [
                {
                    "id": snapshot.id,
                    "graph_run_id": str(snapshot.graph_run_id) if snapshot.graph_run_id else "",
                    "event_slug": snapshot.event_slug,
                    "event_title": snapshot.event_title,
                    "status": snapshot.status,
                    "category": snapshot.category,
                    "implied_probability": snapshot.implied_probability,
                    "volume": snapshot.volume,
                    "liquidity": snapshot.liquidity,
                    "open_interest": snapshot.open_interest,
                    "related_market_count": snapshot.related_market_count,
                    "evidence_count": snapshot.evidence_count,
                    "snapshot_at": snapshot.snapshot_at.isoformat(),
                }
                for snapshot in MarketSnapshot.objects.order_by("-snapshot_at")
            ],
            "resolution_labels": [
                {
                    "snapshot_id": label.market_snapshot_id,
                    "event_slug": label.event_slug,
                    "resolved_outcome": label.resolved_outcome,
                    "resolved_probability": label.resolved_probability,
                    "source": label.source,
                    "created_at": label.created_at.isoformat(),
                }
                for label in ResolutionLabel.objects.order_by("-created_at")
            ],
            "agent_traces": [
                {
                    "graph_run_id": str(trace.graph_run_id),
                    "stage": trace.stage,
                    "status": trace.status,
                    "detail": trace.detail,
                    "latency_ms": trace.latency_ms,
                    "token_input": trace.token_input,
                    "token_output": trace.token_output,
                    "cost_usd": trace.cost_usd,
                    "created_at": trace.created_at.isoformat(),
                }
                for trace in AgentTrace.objects.order_by("created_at")
            ],
            "experiments": [
                {
                    "id": experiment.id,
                    "task_type": experiment.task_type,
                    "title": experiment.title,
                    "dataset_version": experiment.dataset_version,
                    "metrics": experiment.metrics,
                    "created_at": experiment.created_at.isoformat(),
                }
                for experiment in ExperimentRun.objects.order_by("-created_at")
            ],
            "agent_trust_examples": trust_examples,
            "related_market_judgments": [
                {
                    "id": judgment.id,
                    "graph_run_id": str(judgment.graph_run_id),
                    "candidate_key": judgment.candidate_key,
                    "candidate_title": judgment.candidate_title,
                    "candidate_summary": judgment.candidate_summary,
                    "candidate_source_url": judgment.candidate_source_url,
                    "candidate_rank": judgment.candidate_rank,
                    "candidate_confidence": judgment.candidate_confidence,
                    "usefulness_label": judgment.usefulness_label,
                    "notes": judgment.notes,
                    "reviewer": judgment.reviewer,
                    "reviewer_key": judgment.reviewer_key,
                    "source": judgment.source,
                    "updated_at": judgment.updated_at.isoformat(),
                }
                for judgment in RelatedMarketJudgment.objects.order_by("-updated_at")
            ],
            "related_market_review_consensus": judgment_service.consensus_records(),
            "crossvenue_market_map": [
                {
                    "id": market.id,
                    "venue": market.venue,
                    "market_id": market.market_id,
                    "market_slug": market.market_slug,
                    "event_slug": market.event_slug,
                    "title": market.title,
                    "status": market.status,
                    "category": market.category,
                    "resolution_window": market.resolution_window,
                    "is_active": market.is_active,
                    "updated_at": market.updated_at.isoformat(),
                }
                for market in CrossVenueMarketMap.objects.order_by("venue", "title")
            ],
            "market_event_ticks": [
                {
                    "id": tick.id,
                    "venue": tick.venue,
                    "market_id": tick.market_id,
                    "market_slug": tick.market_slug,
                    "event_type": tick.event_type,
                    "status": tick.status,
                    "exchange_timestamp": tick.exchange_timestamp.isoformat(),
                    "received_at": tick.received_at.isoformat(),
                    "last_price": tick.last_price,
                    "yes_bid": tick.yes_bid,
                    "yes_ask": tick.yes_ask,
                    "bid_size": tick.bid_size,
                    "ask_size": tick.ask_size,
                    "volume": tick.volume,
                    "open_interest": tick.open_interest,
                }
                for tick in MarketEventTick.objects.order_by("-exchange_timestamp")
            ],
            "orderbook_snapshots": [
                {
                    "id": book.id,
                    "venue": book.venue,
                    "market_id": book.market_id,
                    "captured_at": book.captured_at.isoformat(),
                    "best_yes_bid": book.best_yes_bid,
                    "best_yes_ask": book.best_yes_ask,
                    "total_bid_depth": book.total_bid_depth,
                    "total_ask_depth": book.total_ask_depth,
                }
                for book in OrderBookLevelSnapshot.objects.order_by("-captured_at")
            ],
            "leadlag_pairs": [
                {
                    "id": pair.id,
                    "pair_type": pair.pair_type,
                    "leader_market_id": pair.leader_market_id,
                    "follower_market_id": pair.follower_market_id,
                    "semantic_score": pair.semantic_score,
                    "causal_score": pair.causal_score,
                    "resolution_score": pair.resolution_score,
                    "stability_score": pair.stability_score,
                    "composite_score": pair.composite_score,
                    "expected_latency_seconds": pair.expected_latency_seconds,
                    "is_trade_eligible": pair.is_trade_eligible,
                    "updated_at": pair.updated_at.isoformat(),
                }
                for pair in LeadLagPair.objects.order_by("-composite_score")
            ],
            "leadlag_signals": [
                {
                    "id": signal.id,
                    "pair_id": signal.pair_id,
                    "status": signal.status,
                    "signal_direction": signal.signal_direction,
                    "leader_price_move": signal.leader_price_move,
                    "follower_gap": signal.follower_gap,
                    "expected_edge": signal.expected_edge,
                    "cost_estimate": signal.cost_estimate,
                    "latency_ms": signal.latency_ms,
                    "created_at": signal.created_at.isoformat(),
                }
                for signal in LeadLagSignal.objects.order_by("-created_at")
            ],
            "paper_trades": [
                {
                    "id": trade.id,
                    "signal_id": trade.signal_id,
                    "status": trade.status,
                    "side": trade.side,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "gross_pnl": trade.gross_pnl,
                    "net_pnl": trade.net_pnl,
                    "fee_paid": trade.fee_paid,
                    "slippage_paid": trade.slippage_paid,
                    "opened_at": trade.opened_at.isoformat(),
                    "closed_at": trade.closed_at.isoformat() if trade.closed_at else "",
                }
                for trade in PaperTrade.objects.order_by("-opened_at")
            ],
            "resolution_forecast_examples": forecast_examples,
            "related_market_ranking_examples": ranking_examples,
            "related_market_usefulness_examples": human_ranking_examples,
        }

    def write_jsonl(self, output_dir: Path | None = None) -> dict[str, str]:
        output_root = output_dir or Path("ml_data")
        output_root.mkdir(parents=True, exist_ok=True)
        records = self.build_records()
        written = {}
        for key, items in records.items():
            path = output_root / f"{key}.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                for item in items:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            written[key] = str(path)
        self._write_duckdb(output_root, records)
        return written

    def _write_duckdb(self, output_root: Path, records: dict[str, list[dict[str, Any]]]) -> None:
        try:
            import duckdb
        except ImportError:
            return

        db_path = output_root / "chaoswing_analytics.duckdb"
        conn = duckdb.connect(str(db_path))
        try:
            for table_name, items in records.items():
                conn.execute(f"DROP TABLE IF EXISTS {table_name}")
                if not items:
                    conn.execute(
                        f"CREATE TABLE {table_name} AS SELECT * FROM (SELECT 1 AS empty) WHERE 1=0"
                    )
                    continue
                source_path = output_root / f"{table_name}.jsonl"
                conn.execute(
                    f"CREATE TABLE {table_name} AS SELECT * FROM read_json_auto(?)",
                    [str(source_path)],
                )
        finally:
            conn.close()


class QualityBacktestService:
    def __init__(self, data_path: Path | None = None):
        self.data_path = data_path or Path("ml_data") / "training_data.jsonl"

    def run(self, persist: bool = True) -> dict[str, Any]:
        summary = BenchmarkSummaryService(data_path=self.data_path)
        examples = summary._load_examples()
        metrics = summary._quality_metrics(examples)
        report = {
            "task_type": "quality_backtest",
            "title": "Graph quality heuristic backtest",
            "dataset_version": self.data_path.name,
            "metrics": metrics,
            "example_count": len(examples),
        }
        if persist:
            ExperimentRun.objects.create(
                task_type="quality_backtest",
                title="Graph quality heuristic backtest",
                dataset_version=self.data_path.name,
                metrics=metrics,
                artifacts={"examples": len(examples)},
                notes="Baseline heuristic scorer versus persisted review labels.",
            )
            BenchmarkSummaryService.invalidate_cached_summary()
        return report


class AgentEvaluationService:
    def run(self, persist: bool = True) -> dict[str, Any]:
        traces = self._normalized_traces()
        statuses = Counter(trace.status for trace in traces)
        stage_counts = Counter(trace.stage for trace in traces)
        run_count = GraphRun.objects.count()
        required_stages = {"planner", "retriever", "graph_editor", "verifier", "critic"}
        stages_by_run: dict[Any, set[str]] = {}
        for trace in traces:
            stages_by_run.setdefault(trace.graph_run_id, set()).add(trace.stage)
        traced_run_ids = {trace.graph_run_id for trace in traces}
        active_traces = [trace for trace in traces if trace.status != "skipped"]
        llm_expected_traces = [
            trace for trace in traces if self._expects_llm_telemetry(trace)
        ]
        traces_with_citations = sum(1 for trace in traces if trace.citations)
        traces_with_latency = sum(1 for trace in active_traces if trace.latency_ms > 0)
        traces_with_tokens = sum(
            1 for trace in llm_expected_traces if (trace.token_input + trace.token_output) > 0
        )
        traces_with_cost = sum(1 for trace in llm_expected_traces if trace.cost_usd > 0)
        total_tokens = sum(trace.token_input + trace.token_output for trace in traces)
        total_cost = sum(trace.cost_usd for trace in traces)
        total_citations = sum(len(trace.citations or []) for trace in traces)
        nonzero_latency = [trace.latency_ms for trace in active_traces if trace.latency_ms > 0]
        trace_count = len(traces)
        active_trace_count = len(active_traces)
        llm_trace_count = len(llm_expected_traces)
        required_stage_complete_runs = sum(
            1 for stages in stages_by_run.values() if required_stages.issubset(stages)
        )
        avg_required_stage_coverage = (
            sum(len(required_stages & stages) / len(required_stages) for stages in stages_by_run.values()) / len(stages_by_run)
            if stages_by_run
            else 0.0
        )
        metrics = {
            "trace_count": trace_count,
            "completed": statuses.get("completed", 0),
            "fallback": statuses.get("fallback", 0),
            "failed": statuses.get("failed", 0),
            "manual_reviews": stage_counts.get("manual_review", 0),
            "avg_traces_per_run": (trace_count / run_count if run_count else 0.0),
            "run_coverage_rate": (len(traced_run_ids) / run_count if run_count else 0.0),
            "required_stage_coverage_rate": (
                required_stage_complete_runs / run_count if run_count else 0.0
            ),
            "avg_required_stage_coverage": avg_required_stage_coverage,
            "completed_rate": (
                statuses.get("completed", 0) / trace_count if trace_count else 0.0
            ),
            "citation_coverage_rate": (
                traces_with_citations / trace_count if trace_count else 0.0
            ),
            "latency_coverage_rate": (
                traces_with_latency / active_trace_count if active_trace_count else 0.0
            ),
            "token_coverage_rate": (
                traces_with_tokens / llm_trace_count if llm_trace_count else 0.0
            ),
            "cost_coverage_rate": (
                traces_with_cost / llm_trace_count if llm_trace_count else 0.0
            ),
            "active_trace_count": active_trace_count,
            "llm_trace_count": llm_trace_count,
            "avg_citations_per_trace": (
                total_citations / trace_count if trace_count else 0.0
            ),
            "avg_latency_ms": (
                sum(nonzero_latency) / len(nonzero_latency) if nonzero_latency else 0.0
            ),
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 4),
        }
        report = {
            "task_type": "agent_eval",
            "title": "Agent instrumentation coverage report",
            "metrics": metrics,
            "status_breakdown": dict(statuses),
            "stage_breakdown": dict(stage_counts),
        }
        if persist:
            ExperimentRun.objects.create(
                task_type="agent_eval",
                title="Agent instrumentation coverage report",
                dataset_version=f"agent_traces:{trace_count}",
                metrics=metrics,
                artifacts={
                    "stage_breakdown": dict(stage_counts),
                    "status_breakdown": dict(statuses),
                    "required_stages": sorted(required_stages),
                },
                notes=(
                    "Instrumentation-style agent evaluation over persisted traces. This measures "
                    "coverage of the staged planner/retriever/graph-editor/verifier/critic workflow "
                    "plus citations, latency, token counts, and cost metadata. Latency coverage is "
                    "scored over non-skipped stages, while token and cost coverage are scored only "
                    "on stages that are expected to carry LLM telemetry. Required stages are normalized "
                    "per run so historical backfills and retries do not inflate coverage; it is not yet "
                    "a full hallucination or citation-precision benchmark."
                ),
            )
            BenchmarkSummaryService.invalidate_cached_summary()
        return report

    def _normalized_traces(self) -> list[AgentTrace]:
        traces = list(AgentTrace.objects.order_by("created_at"))
        required_stages = {"planner", "retriever", "graph_editor", "verifier", "critic"}
        canonical_required: dict[tuple[Any, str], AgentTrace] = {}
        normalized: list[AgentTrace] = []
        for trace in traces:
            if trace.stage in required_stages:
                canonical_required[(trace.graph_run_id, trace.stage)] = trace
                continue
            normalized.append(trace)
        normalized.extend(canonical_required.values())
        normalized.sort(key=lambda trace: trace.created_at)
        return normalized

    def _expects_llm_telemetry(self, trace: AgentTrace) -> bool:
        metadata = trace.metadata or {}
        if str(metadata.get("provider") or "").strip() or str(metadata.get("model") or "").strip():
            return True
        execution_mode = str(metadata.get("execution_mode") or "").strip().lower()
        if execution_mode == "anthropic":
            return True
        return trace.stage in {"llm_expansion", "llm_review"}


class AgentTrustBenchmarkService:
    def build_examples(self) -> list[dict[str, Any]]:
        runs = list(GraphRun.objects.prefetch_related("agent_traces").order_by("created_at"))
        return [_agent_trust_example(run) for run in runs]

    def export_examples(self) -> list[dict[str, Any]]:
        return self.build_examples()

    def run(self, persist: bool = True) -> dict[str, Any]:
        examples = self.build_examples()
        if examples:
            metrics = {
                "run_count": len(examples),
                "avg_trust_score": sum(example["trust_score"] for example in examples) / len(examples),
                "approved_rate": (
                    sum(1 for example in examples if example["approved"]) / len(examples)
                ),
                "avg_unsupported_claim_rate": (
                    sum(example["unsupported_claim_rate"] for example in examples) / len(examples)
                ),
                "avg_supported_conceptual_rate": (
                    sum(example["supported_conceptual_rate"] for example in examples) / len(examples)
                ),
                "avg_explained_edge_rate": (
                    sum(example["explained_edge_rate"] for example in examples) / len(examples)
                ),
                "avg_citation_stage_rate": (
                    sum(example["citation_stage_rate"] for example in examples) / len(examples)
                ),
                "avg_telemetry_coverage_rate": (
                    sum(example["telemetry_coverage_rate"] for example in examples) / len(examples)
                ),
                "avg_issue_count": (
                    sum(example["issue_count"] for example in examples) / len(examples)
                ),
                "avg_quality_score": (
                    sum(example["quality_score"] for example in examples) / len(examples)
                ),
            }
        else:
            metrics = {
                "run_count": 0,
                "avg_trust_score": 0.0,
                "approved_rate": 0.0,
                "avg_unsupported_claim_rate": 0.0,
                "avg_supported_conceptual_rate": 0.0,
                "avg_explained_edge_rate": 0.0,
                "avg_citation_stage_rate": 0.0,
                "avg_telemetry_coverage_rate": 0.0,
                "avg_issue_count": 0.0,
                "avg_quality_score": 0.0,
            }
        report = {
            "task_type": "agent_trust",
            "title": "Agent trust benchmark",
            "metrics": metrics,
            "example_count": len(examples),
        }
        if persist:
            ExperimentRun.objects.create(
                task_type="agent_trust",
                title="Agent trust benchmark",
                dataset_version=f"graph_runs:{len(examples)}",
                metrics=metrics,
                artifacts={
                    "examples": examples,
                    "trust_bands": {
                        "high": sum(1 for example in examples if example["trust_score"] >= 0.8),
                        "medium": sum(1 for example in examples if 0.6 <= example["trust_score"] < 0.8),
                        "low": sum(1 for example in examples if example["trust_score"] < 0.6),
                    },
                },
                notes=(
                    "Deterministic trust benchmark over saved graph payloads and staged traces. "
                    "Scores supported conceptual claims, explained edges, citation-backed retrieval/edit "
                    "stages, telemetry coverage, and review issue burden."
                ),
            )
            BenchmarkSummaryService.invalidate_cached_summary()
        return report
