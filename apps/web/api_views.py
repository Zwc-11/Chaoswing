from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import GraphRun, LeadLagPair, PaperTrade
from .services import GraphWorkflowService
from .services.api_reference import API_REFERENCE_VERSION, ApiReferenceService
from .services.leadlag import LeadLagMonitorService
from .services.link_verifier import LinkVerificationService
from .services.market_intelligence import (
    BenchmarkSummaryService,
    MarketBriefService,
    RelatedMarketJudgmentService,
    WatchlistService,
)
from .services.polymarket import TrendingMarketsService

logger = logging.getLogger("apps.web.api_views")


def _brief_run_queryset():
    return GraphRun.objects.prefetch_related("agent_traces")


def _is_valid_polymarket_url(url: str) -> bool:
    parsed = urlparse(url)
    return bool(parsed.scheme in {"http", "https"} and "polymarket.com" in parsed.netloc)


def api_json_response(payload: dict, *, status: int = 200) -> JsonResponse:
    response = JsonResponse(payload, status=status)
    response["X-ChaosWing-Api-Version"] = API_REFERENCE_VERSION
    response["X-ChaosWing-Api-Docs"] = reverse("web:api_docs")
    response["Link"] = (
        f'<{reverse("web:api_docs")}>; rel="describedby", '
        f'<{reverse("web:openapi_spec")}>; rel="service-desc"'
    )
    return response


def _serialize_run_summary(run: GraphRun) -> dict:
    """Lightweight summary used in list views."""
    return {
        "id": str(run.id),
        "event_title": run.event_title or "Untitled run",
        "event_slug": run.event_slug or "",
        "source_url": run.source_url or "",
        "mode": run.mode or "deterministic-fallback",
        "status": run.status or "completed",
        "graph_stats": run.graph_stats or {},
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "detail_url": reverse("web:graph_run_detail", kwargs={"run_id": run.id}),
        "brief_url": reverse("web:market_brief", kwargs={"run_id": run.id}),
        "related_markets_url": reverse("web:graph_run_related_markets", kwargs={"run_id": run.id}),
        "changes_url": reverse("web:graph_run_changes", kwargs={"run_id": run.id}),
    }


@require_GET
def api_root(request):
    return api_json_response(ApiReferenceService().build_api_index(request))


@require_GET
def openapi_spec(request):
    return api_json_response(ApiReferenceService().build_openapi(request))


@csrf_exempt
@require_POST
def graph_from_url(request):
    if "application/json" in request.headers.get("Content-Type", ""):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return api_json_response({"error": "Request body must be valid JSON."}, status=400)
    else:
        body = request.POST

    source_url = (body.get("url") or "").strip()
    if not source_url:
        return api_json_response({"error": "The `url` field is required."}, status=400)

    if not _is_valid_polymarket_url(source_url):
        return api_json_response({"error": "Use a full Polymarket event URL."}, status=400)

    workflow = GraphWorkflowService()
    try:
        payload = workflow.run(source_url)
    except ValueError as exc:
        return api_json_response({"error": str(exc)}, status=400)
    except Exception:
        return api_json_response(
            {"error": "ChaosWing could not generate a graph run."},
            status=500,
        )

    # Attach a convenience detail URL so the client can permalink the run
    run_id = payload.get("run", {}).get("id")
    if run_id:
        try:
            payload["run"]["detail_url"] = reverse("web:graph_run_detail", kwargs={"run_id": run_id})
            payload["run"]["brief_url"] = reverse("web:market_brief", kwargs={"run_id": run_id})
            payload["run"]["related_markets_url"] = reverse("web:graph_run_related_markets", kwargs={"run_id": run_id})
            payload["run"]["changes_url"] = reverse("web:graph_run_changes", kwargs={"run_id": run_id})
        except Exception:
            pass

    return api_json_response(payload)


@require_GET
def list_graph_runs(request):
    """Return a paginated list of recent GraphRun records (newest first)."""
    try:
        raw_limit = int(request.GET.get("limit", 20))
    except (TypeError, ValueError):
        raw_limit = 20

    limit = max(1, min(raw_limit, 50))

    try:
        raw_offset = int(request.GET.get("offset", 0))
    except (TypeError, ValueError):
        raw_offset = 0

    offset = max(0, raw_offset)

    qs = GraphRun.objects.order_by("-created_at")
    total = qs.count()
    runs = qs[offset : offset + limit]

    return api_json_response(
        {
            "runs": [_serialize_run_summary(run) for run in runs],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


@require_GET
def graph_run_detail(request, run_id):
    run = get_object_or_404(GraphRun, pk=run_id)
    return api_json_response(
        {
            "id": str(run.id),
            "status": run.status,
            "mode": run.mode,
            "model_name": run.model_name,
            "source_url": run.source_url,
            "event_slug": run.event_slug,
            "event_title": run.event_title,
            "source_snapshot": run.source_snapshot,
            "graph_stats": run.graph_stats,
            "workflow_log": run.workflow_log,
            "payload": run.payload,
            "detail_url": reverse("web:graph_run_detail", kwargs={"run_id": run.id}),
            "brief_url": reverse("web:market_brief", kwargs={"run_id": run.id}),
            "related_markets_url": reverse("web:graph_run_related_markets", kwargs={"run_id": run.id}),
            "changes_url": reverse("web:graph_run_changes", kwargs={"run_id": run.id}),
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
        }
    )


@require_GET
def graph_run_brief(request, run_id):
    run = get_object_or_404(_brief_run_queryset(), pk=run_id)
    brief = MarketBriefService().build(run)
    return api_json_response(
        {
            "id": str(run.id),
            "brief_url": reverse("web:market_brief", kwargs={"run_id": run.id}),
            "brief": brief,
        }
    )


@require_GET
def graph_run_related_markets(request, run_id):
    run = get_object_or_404(GraphRun, pk=run_id)
    ranking = MarketBriefService().related_market_ranking(run)
    return api_json_response(
        {
            "id": str(run.id),
            "event_title": run.event_title,
            "ranking": ranking,
            "count": len(ranking),
        }
    )


@require_GET
def graph_run_changes(request, run_id):
    run = get_object_or_404(GraphRun, pk=run_id)
    summary = MarketBriefService().change_summary(run)
    return api_json_response(
        {
            "id": str(run.id),
            "changes": summary,
        }
    )


@csrf_exempt
@require_POST
def review_graph_run(request, run_id):
    run = get_object_or_404(GraphRun, pk=run_id)
    workflow = GraphWorkflowService()

    try:
        review = workflow.review_saved_run(run)
    except ValueError as exc:
        return api_json_response({"error": str(exc)}, status=400)
    except Exception:
        return api_json_response(
            {"error": "ChaosWing could not review that graph run."},
            status=500,
        )

    return api_json_response(
        {
            "id": str(run.id),
            "review": review,
            "mode": run.mode,
            "model_name": run.model_name,
        }
    )


@require_GET
def trending_markets(request):
    """Return the top trending Polymarket events by 24-hour volume."""
    try:
        raw_limit = int(request.GET.get("limit", 6))
    except (TypeError, ValueError):
        raw_limit = 6
    limit = max(1, min(raw_limit, 20))

    service = TrendingMarketsService()
    events = service.get_trending(limit=limit)

    return api_json_response(
        {
            "markets": events,
            "count": len(events),
            "source": "polymarket-gamma-api",
        }
    )


@require_GET
def benchmark_summary(request):
    return api_json_response(BenchmarkSummaryService().build_cached())


@require_GET
def related_market_review_queue(request):
    return api_json_response(RelatedMarketJudgmentService().review_queue())


@csrf_exempt
@require_POST
def submit_related_market_judgment(request):
    if "application/json" in request.headers.get("Content-Type", ""):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return api_json_response({"error": "Request body must be valid JSON."}, status=400)
    else:
        body = request.POST

    run_id = str(body.get("run_id") or "").strip()
    if not run_id:
        return api_json_response({"error": "The `run_id` field is required."}, status=400)

    run = get_object_or_404(GraphRun, pk=run_id)
    service = RelatedMarketJudgmentService()
    try:
        judgment = service.upsert_judgment(
            run,
            candidate_key=str(body.get("candidate_key") or ""),
            candidate_title=str(body.get("candidate_title") or ""),
            usefulness_label=str(body.get("usefulness_label") or ""),
            notes=str(body.get("notes") or ""),
            reviewer=str(body.get("reviewer") or ""),
            source="manual-api",
            candidate_summary=str(body.get("candidate_summary") or ""),
            candidate_source_url=str(body.get("candidate_source_url") or ""),
            candidate_rank=int(body.get("candidate_rank") or 0),
            candidate_confidence=float(body.get("candidate_confidence") or 0.0),
        )
    except (TypeError, ValueError) as exc:
        return api_json_response({"error": str(exc)}, status=400)

    return api_json_response(
        {
            "status": "ok",
            "judgment": {
                "id": judgment.id,
                "run_id": str(judgment.graph_run_id),
                "candidate_key": judgment.candidate_key,
                "candidate_title": judgment.candidate_title,
                "usefulness_label": judgment.usefulness_label,
                "notes": judgment.notes,
                "reviewer": judgment.reviewer,
                "reviewer_key": judgment.reviewer_key,
                "updated_at": judgment.updated_at.isoformat(),
            },
        }
    )


@require_GET
def leadlag_summary(request):
    return api_json_response(LeadLagMonitorService().build_cached())


@require_GET
def leadlag_signals(request):
    summary = LeadLagMonitorService().build_cached()
    return api_json_response(
        {
            "signals": summary["recent_signals"],
            "count": len(summary["recent_signals"]),
            "totals": summary["totals"],
        }
    )


@require_GET
def leadlag_pairs(request):
    summary = LeadLagMonitorService().build_cached()
    return api_json_response(
        {
            "pairs": summary["pair_diagnostics"],
            "count": len(summary["pair_diagnostics"]),
        }
    )


@require_GET
def leadlag_pair_detail(request, pair_id):
    pair = get_object_or_404(
        LeadLagPair.objects.filter(is_active=True).select_related("leader_market", "follower_market"),
        pk=pair_id,
    )
    signals = [
        {
            "id": signal.id,
            "status": signal.status,
            "signal_direction": signal.signal_direction,
            "expected_edge": signal.expected_edge,
            "created_at": signal.created_at.isoformat(),
        }
        for signal in pair.signals.order_by("-created_at")[:10]
    ]
    trades = [
        {
            "id": trade.id,
            "status": trade.status,
            "side": trade.side,
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "net_pnl": trade.net_pnl,
            "opened_at": trade.opened_at.isoformat(),
            "closed_at": trade.closed_at.isoformat() if trade.closed_at else "",
        }
        for trade in PaperTrade.objects.filter(signal__pair=pair)
        .order_by("-opened_at")[:10]
    ]
    return api_json_response(
        {
            "id": pair.id,
            "pair_type": pair.pair_type,
            "leader_market": {
                "venue": pair.leader_market.venue,
                "market_id": pair.leader_market.market_id,
                "title": pair.leader_market.title,
                "url": pair.leader_market.url,
            },
            "follower_market": {
                "venue": pair.follower_market.venue,
                "market_id": pair.follower_market.market_id,
                "title": pair.follower_market.title,
                "url": pair.follower_market.url,
            },
            "scores": {
                "semantic_score": pair.semantic_score,
                "causal_score": pair.causal_score,
                "resolution_score": pair.resolution_score,
                "stability_score": pair.stability_score,
                "composite_score": pair.composite_score,
            },
            "expected_latency_seconds": pair.expected_latency_seconds,
            "is_trade_eligible": pair.is_trade_eligible,
            "direction_reason": pair.direction_reason,
            "readiness": {
                "status": (pair.metadata or {}).get("readiness_status", "needs_history"),
                "reason": (pair.metadata or {}).get("readiness_reason", ""),
                "stability_samples": (pair.metadata or {}).get("stability_samples", 0),
                "move_samples": (pair.metadata or {}).get("move_samples", 0),
                "leader_first_ratio": (pair.metadata or {}).get("leader_first_ratio", 0.0),
                "avg_lead_seconds": (pair.metadata or {}).get("avg_lead_seconds", 0.0),
            },
            "signals": signals,
            "paper_trades": trades,
        }
    )


@require_GET
def list_watchlists(request):
    watchlists = WatchlistService().all()
    return api_json_response({"watchlists": watchlists, "count": len(watchlists)})


@csrf_exempt
@require_POST
def verify_links(request):
    """Verify a batch of URLs and return their reachability status.

    Accepts JSON body: {"urls": ["https://...", ...]}
    Returns: {"results": {"url": true/false, ...}}
    """
    if "application/json" not in request.headers.get("Content-Type", ""):
        return api_json_response({"error": "Content-Type must be application/json."}, status=400)

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return api_json_response({"error": "Invalid JSON body."}, status=400)

    urls = body.get("urls", [])
    if not isinstance(urls, list) or len(urls) > 20:
        return api_json_response({"error": "Provide a list of up to 20 URLs."}, status=400)

    clean_urls = [str(u).strip() for u in urls if isinstance(u, str) and str(u).strip()]
    verifier = LinkVerificationService()
    results = verifier.verify_batch(clean_urls)

    return api_json_response({"results": results})
