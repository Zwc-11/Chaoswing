from __future__ import annotations

from django.urls import reverse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from .models import GraphRun
from .mock_graph import DEFAULT_CONTROLS, LAYOUT_OPTIONS, NODE_TYPE_OPTIONS, SAMPLE_POLYMARKET_LINKS


def landing(request):
    """Marketing landing page at the root URL."""
    try:
        total_runs = GraphRun.objects.count()
        recent_titles = list(
            GraphRun.objects.order_by("-created_at")
            .values_list("event_title", flat=True)
            .exclude(event_title="")[:6]
        )
    except Exception:
        total_runs = 0
        recent_titles = []

    return render(
        request,
        "web/landing.html",
        {
            "sample_links": SAMPLE_POLYMARKET_LINKS,
            "stats": {
                "total_runs": total_runs,
                "recent_titles": recent_titles,
            },
            "features": [
                {
                    "icon": "graph",
                    "title": "Butterfly Graphs",
                    "copy": "Every market becomes an interactive causal graph — nodes for events, entities, evidence, rules, and hypotheses, all connected by typed edges.",
                },
                {
                    "icon": "resolve",
                    "title": "Live Polymarket Resolution",
                    "copy": "ChaosWing pulls live event data from Polymarket Gamma, attaches real probabilities and contract metadata, and keeps every node grounded in source truth.",
                },
                {
                    "icon": "discover",
                    "title": "Related Market Discovery",
                    "copy": "Shared tags, overlapping terms, and narrative proximity surface adjacent contracts you would never have searched for manually.",
                },
                {
                    "icon": "ai",
                    "title": "AI Graph Expansion",
                    "copy": "Optional Claude-powered pass enriches the graph with additional causal nodes, refines edge explanations, and flags structural weaknesses.",
                },
                {
                    "icon": "inspect",
                    "title": "Deep Node Inspection",
                    "copy": "Click any node or edge to lock the graph and read its full profile — source market, confidence signals, evidence snippets, and metadata.",
                },
                {
                    "icon": "history",
                    "title": "Persistent Run History",
                    "copy": "Every analysis is saved as a GraphRun record. Reload any past graph instantly, trigger a fresh AI review, or compare runs side by side.",
                },
            ],
            "how_it_works": [
                {
                    "step": "01",
                    "title": "Paste a Polymarket URL",
                    "copy": "Drop any Polymarket event link into the input. ChaosWing accepts raw event URLs and resolves them automatically.",
                },
                {
                    "step": "02",
                    "title": "ChaosWing resolves the graph",
                    "copy": "The backend fetches live event data, discovers related contracts, builds the causal graph, and optionally runs an AI expansion pass.",
                },
                {
                    "step": "03",
                    "title": "Explore the butterfly effect",
                    "copy": "Hover nodes to preview, click to lock, filter by type, trace the strongest impact path, and open any source market in one click.",
                },
            ],
        },
    )


@ensure_csrf_cookie
def dashboard(request):
    """Main application dashboard."""
    try:
        recent_runs = list(
            GraphRun.objects.order_by("-created_at")
            .values("id", "event_title", "event_slug", "source_url", "mode", "status", "graph_stats", "created_at")[:20]
        )
        recent_run_count = GraphRun.objects.count()
    except Exception:
        recent_runs = []
        recent_run_count = 0

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
            "version": "1.0",
            "shell": "Django templates, routes, and partials",
            "sessionMode": "Persisted backend run",
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
            "node_types": NODE_TYPE_OPTIONS,
            "layout_options": LAYOUT_OPTIONS,
            "default_layout": DEFAULT_CONTROLS["layout"],
            "initial_state": initial_state,
            "recent_runs": serialized_runs,
            "total_runs": recent_run_count,
            "shortcut_hints": [
                {"key": "1", "label": "Load a market"},
                {"key": "2", "label": "Hover to preview"},
                {"key": "3", "label": "Click to lock and read"},
            ],
        },
    )
