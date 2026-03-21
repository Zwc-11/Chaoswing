from __future__ import annotations

import logging

from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import GraphRun
from .mock_graph import DEFAULT_CONTROLS, LAYOUT_OPTIONS, NODE_TYPE_OPTIONS, SAMPLE_POLYMARKET_LINKS
from .services.api_reference import ApiReferenceService
from .services.leadlag import LeadLagMonitorService
from .services.market_intelligence import (
    BenchmarkSummaryService,
    LandingStatsService,
    MarketBriefService,
    RelatedMarketJudgmentService,
    WatchlistService,
)
from .services.polymarket import TrendingMarketsService

logger = logging.getLogger("apps.web.views")


def _build_share_meta(
    request,
    *,
    title: str,
    description: str,
    url: str,
) -> dict[str, str]:
    return {
        "title": title,
        "description": description,
        "url": request.build_absolute_uri(url),
        "image_url": request.build_absolute_uri(static("web/img/chaoswing-social-card.jpg")),
        "image_alt": "ChaosWing landing page showing the butterfly graph workspace",
    }


def _marketing_nav_links() -> list[dict[str, str]]:
    return [
        {"label": "Research tasks", "href": f"{reverse('web:landing')}#use-cases"},
        {"label": "API", "href": reverse("web:api_docs")},
        {"label": "Watchlists", "href": reverse("web:watchlists")},
        {"label": "Lead-lag", "href": reverse("web:leadlag_monitor")},
        {"label": "Benchmarks", "href": reverse("web:benchmarks")},
        {"label": "Markets", "href": f"{reverse('web:landing')}#markets"},
        {"label": "GitHub", "href": "https://github.com/Zwc-11/Chaoswing", "external": "1"},
    ]


def _brief_run_queryset():
    return GraphRun.objects.prefetch_related("agent_traces")


def landing(request):
    """Marketing landing page at the root URL."""
    description = (
        "ChaosWing is a prediction-market research and evaluation platform for "
        "resolution forecasting, related-market ranking, and cross-venue lead-lag analysis."
    )
    try:
        stats = LandingStatsService().build_cached()
    except Exception:
        stats = {"total_runs": 0, "recent_titles": []}

    trending = []
    try:
        service = TrendingMarketsService()
        trending = service.get_trending(limit=6)
    except Exception:
        logger.debug("Trending markets fetch failed, using static samples")

    display_markets = trending if trending else SAMPLE_POLYMARKET_LINKS
    benchmark_summary = BenchmarkSummaryService().build_cached()
    featured_watchlists = WatchlistService().featured()

    return render(
        request,
        "web/landing.html",
        {
            "marketing_nav_links": _marketing_nav_links(),
            "sample_links": display_markets,
            "trending_markets": trending,
            "has_trending": bool(trending),
            "share_meta": _build_share_meta(
                request,
                title="ChaosWing - Prediction-Market Research Platform",
                description=description,
                url=reverse("web:landing"),
            ),
            "stats": stats,
            "use_cases": [
                {
                    "title": "Resolution forecasting",
                    "copy": "Start from historical market snapshots and test whether structured snapshot features beat the market-implied YES probability on rolling event-resolution backtests.",
                },
                {
                    "title": "Related-market ranking",
                    "copy": "Use one source market to rank adjacent contracts by usefulness and spillover relevance, then compare lexical overlap against the context-aware reranker.",
                },
                {
                    "title": "Lead-lag falsification",
                    "copy": "Track mapped Polymarket and Kalshi pairs, score candidate timing signals, and stress-test the thesis with latency-aware paper-trade evaluation.",
                },
            ],
            "features": [
                {
                    "icon": "graph",
                    "title": "Graph-based analyst interface",
                    "copy": "Each saved run becomes a shareable analyst brief plus a graph workspace that makes ranking, spillover paths, and evidence inspectable.",
                },
                {
                    "icon": "discover",
                    "title": "Ranking benchmark track",
                    "copy": "ChaosWing persists source-market and candidate-market examples so lexical overlap can be compared against a context-aware reranker on repeatable benchmarks.",
                },
                {
                    "icon": "inspect",
                    "title": "Evidence and trust inspection",
                    "copy": "Review quality, evidence density, follow-up actions, and trace coverage make the graph legible as an evaluated system instead of ungrounded AI output.",
                },
                {
                    "icon": "resolve",
                    "title": "Snapshot and label capture",
                    "copy": "Every run still resolves against live Polymarket data while persisting snapshots, labels, and contract context for forecasting and evaluation.",
                },
                {
                    "icon": "history",
                    "title": "Reusable research watchlists",
                    "copy": "Curated watchlists turn one-off demos into recurring macro, commodity, and political research workflows with reusable starting points.",
                },
                {
                    "icon": "ai",
                    "title": "Benchmark-ready experiment layer",
                    "copy": "Saved run data, heuristic quality scoring, judged labels, and structured exports make ChaosWing legible as a research and evaluation system.",
                },
                {
                    "icon": "history",
                    "title": "Cross-venue lead-lag monitor",
                    "copy": "ChaosWing tracks Polymarket and Kalshi market mappings, live research ticks, candidate spillover alerts, and paper trades instead of making vague correlation claims.",
                },
            ],
            "how_it_works": [
                {
                    "step": "01",
                    "title": "Load a source market",
                    "copy": "Paste any Polymarket event URL or open a starter market from the landing page to anchor the research workflow.",
                },
                {
                    "step": "02",
                    "title": "Generate a reproducible run",
                    "copy": "ChaosWing discovers related markets, builds the graph, and stores the full run for replay, briefs, datasets, and benchmarking.",
                },
                {
                    "step": "03",
                    "title": "Inspect the analyst surfaces",
                    "copy": "Open the run-specific brief, graph, and benchmark context to inspect strongest paths, ranking results, evidence, and trust signals.",
                },
            ],
            "featured_watchlists": featured_watchlists,
            "benchmark_summary": benchmark_summary,
        },
    )


@ensure_csrf_cookie
def dashboard(request):
    """Main application dashboard."""
    description = (
        "Load one Polymarket URL and inspect a live market-intelligence workspace "
        "with causal spillover, evidence, and related-market ranking."
    )
    try:
        recent_runs = list(
            GraphRun.objects.order_by("-created_at")
            .values("id", "event_title", "event_slug", "source_url", "mode", "status", "graph_stats", "created_at")[:20]
        )
        recent_run_count = GraphRun.objects.count()
    except Exception:
        recent_runs = []
        recent_run_count = 0
    featured_watchlists = WatchlistService().featured()

    serialized_runs = [
        {
            "id": str(run["id"]),
            "event_title": run["event_title"] or "Untitled run",
            "event_slug": run["event_slug"] or "",
            "source_url": run["source_url"] or "",
            "mode": run["mode"] or "deterministic-fallback",
            "status": run["status"] or "completed",
            "graph_stats": run["graph_stats"] or {},
            "created_at": run["created_at"].isoformat() if run["created_at"] else "",
        }
        for run in recent_runs
    ]

    initial_state = {
        "app": {
            "name": "ChaosWing",
            "version": "1.2",
            "shell": "Django templates, routes, and partials",
            "sessionMode": "Persisted market-intelligence run",
        },
        "controls": DEFAULT_CONTROLS,
        "samples": SAMPLE_POLYMARKET_LINKS,
        "recentRuns": serialized_runs,
        "totalRuns": recent_run_count,
        "endpoints": {
            "graph": reverse("web:graph_from_url"),
            "runs": reverse("web:list_graph_runs"),
            "inspectorEmpty": reverse("web:inspector_empty_partial"),
            "inspectorNode": reverse("web:inspector_node_partial"),
            "inspectorEdge": reverse("web:inspector_edge_partial"),
        },
    }

    return render(
        request,
        "web/dashboard.html",
        {
            "sample_links": SAMPLE_POLYMARKET_LINKS,
            "featured_watchlists": featured_watchlists,
            "node_types": NODE_TYPE_OPTIONS,
            "layout_options": LAYOUT_OPTIONS,
            "default_layout": DEFAULT_CONTROLS["layout"],
            "initial_state": initial_state,
            "share_meta": _build_share_meta(
                request,
                title="ChaosWing App - Market Intelligence Workspace",
                description=description,
                url=reverse("web:dashboard"),
            ),
            "recent_runs": serialized_runs,
            "total_runs": recent_run_count,
            "shortcut_hints": [
                {"key": "1", "label": "Load a market"},
                {"key": "2", "label": "Hover to preview"},
                {"key": "3", "label": "Click to lock and read"},
            ],
        },
    )


def market_brief(request, run_id):
    run = get_object_or_404(_brief_run_queryset(), pk=run_id)
    brief = MarketBriefService().build(run)
    event = brief["event"]
    description = (
        f"Market brief for {event['title']}: strongest spillover path, related markets, "
        "evidence, trust signals, and confidence caveats."
    )
    return render(
        request,
        "web/market_brief.html",
        {
            "marketing_nav_links": _marketing_nav_links(),
            "brief": brief,
            "app_url": f"{reverse('web:dashboard')}?url={event['source_url']}",
            "share_meta": _build_share_meta(
                request,
                title=f"ChaosWing Brief - {event['title']}",
                description=description,
                url=reverse("web:market_brief", kwargs={"run_id": run.id}),
            ),
        },
    )


def benchmarks(request):
    summary = BenchmarkSummaryService().build_cached()
    return render(
        request,
        "web/benchmarks.html",
        {
            "marketing_nav_links": _marketing_nav_links(),
            "benchmark_summary": summary,
            "share_meta": _build_share_meta(
                request,
                title="ChaosWing Benchmarks - Measured Market Intelligence",
                description=(
                    "Explore ChaosWing's live benchmark layer: graph quality scoring, "
                    "coverage proxies, recent runs, and the next evaluation tracks."
                ),
                url=reverse("web:benchmarks"),
            ),
        },
    )


@require_http_methods(["GET", "POST"])
def related_market_review(request):
    judgment_service = RelatedMarketJudgmentService()
    error_message = ""

    if request.method == "POST":
        run = get_object_or_404(GraphRun, pk=request.POST.get("run_id"))
        try:
            judgment_service.upsert_judgment(
                run,
                candidate_key=request.POST.get("candidate_key", ""),
                candidate_title=request.POST.get("candidate_title", ""),
                usefulness_label=request.POST.get("usefulness_label", ""),
                notes=request.POST.get("notes", ""),
                reviewer=request.POST.get("reviewer", ""),
                source="manual-page",
                candidate_summary=request.POST.get("candidate_summary", ""),
                candidate_source_url=request.POST.get("candidate_source_url", ""),
                candidate_rank=request.POST.get("candidate_rank", "0"),
                candidate_confidence=request.POST.get("candidate_confidence", "0"),
            )
            return HttpResponseRedirect(f"{reverse('web:related_market_review')}?saved=1")
        except ValueError as exc:
            error_message = str(exc)

    review_queue = judgment_service.review_queue()
    return render(
        request,
        "web/related_market_review.html",
        {
            "marketing_nav_links": _marketing_nav_links(),
            "review_queue": review_queue,
            "saved": request.GET.get("saved") == "1",
            "error_message": error_message,
            "share_meta": _build_share_meta(
                request,
                title="ChaosWing Review Queue - Human Ranking Labels",
                description=(
                    "Review and label which related markets are genuinely useful so "
                    "ChaosWing's ranking benchmarks can graduate from silver labels to ground truth."
                ),
                url=reverse("web:related_market_review"),
            ),
        },
    )


def watchlists(request):
    featured_watchlists = WatchlistService().all()
    return render(
        request,
        "web/watchlists.html",
        {
            "marketing_nav_links": _marketing_nav_links(),
            "featured_watchlists": featured_watchlists,
            "share_meta": _build_share_meta(
                request,
                title="ChaosWing Watchlists - Reusable Prediction Market Narratives",
                description=(
                    "Start from curated macro, commodity, and politics watchlists built "
                    "for recurring research workflows, not one-off demos."
                ),
                url=reverse("web:watchlists"),
            ),
        },
    )


def api_docs(request):
    reference = ApiReferenceService().build_docs_context(request)
    return render(
        request,
        "web/api_docs.html",
        {
            "marketing_nav_links": _marketing_nav_links(),
            "api_reference": reference,
            "share_meta": _build_share_meta(
                request,
                title="ChaosWing API - Public Developer Reference",
                description=(
                    "Explore the public ChaosWing API with endpoint reference, "
                    "request and response examples, rate limits, and the OpenAPI schema."
                ),
                url=reverse("web:api_docs"),
            ),
        },
    )


def leadlag_monitor(request):
    summary = LeadLagMonitorService().build_cached()
    return render(
        request,
        "web/leadlag_monitor.html",
        {
            "marketing_nav_links": _marketing_nav_links(),
            "leadlag_summary": summary,
            "share_meta": _build_share_meta(
                request,
                title="ChaosWing Lead-Lag Monitor - Cross-Venue Spillover Research",
                description=(
                    "Inspect cross-venue market mappings, lead-lag candidate signals, "
                    "and the paper-trade ledger for ChaosWing's prediction-market research system."
                ),
                url=reverse("web:leadlag_monitor"),
            ),
        },
    )
