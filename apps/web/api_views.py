from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .models import GraphRun
from .services import GraphWorkflowService
from .services.link_verifier import LinkVerificationService
from .services.polymarket import TrendingMarketsService

logger = logging.getLogger("apps.web.api_views")


def _is_valid_polymarket_url(url: str) -> bool:
    parsed = urlparse(url)
    return bool(parsed.scheme in {"http", "https"} and "polymarket.com" in parsed.netloc)


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
    }


@require_POST
def graph_from_url(request):
    if "application/json" in request.headers.get("Content-Type", ""):
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    else:
        body = request.POST

    source_url = (body.get("url") or "").strip()
    if not source_url:
        return JsonResponse({"error": "The `url` field is required."}, status=400)

    if not _is_valid_polymarket_url(source_url):
        return JsonResponse({"error": "Use a full Polymarket event URL."}, status=400)

    workflow = GraphWorkflowService()
    try:
        payload = workflow.run(source_url)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception:
        return JsonResponse({"error": "ChaosWing could not generate a graph run."}, status=500)

    # Attach a convenience detail URL so the client can permalink the run
    run_id = payload.get("run", {}).get("id")
    if run_id:
        try:
            payload["run"]["detail_url"] = reverse("web:graph_run_detail", kwargs={"run_id": run_id})
        except Exception:
            pass

    return JsonResponse(payload)


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

    return JsonResponse(
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
    return JsonResponse(
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
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
        }
    )


@require_POST
def review_graph_run(request, run_id):
    run = get_object_or_404(GraphRun, pk=run_id)
    workflow = GraphWorkflowService()

    try:
        review = workflow.review_saved_run(run)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception:
        return JsonResponse({"error": "ChaosWing could not review that graph run."}, status=500)

    return JsonResponse(
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

    return JsonResponse({
        "markets": events,
        "count": len(events),
        "source": "polymarket-gamma-api",
    })


@require_POST
def verify_links(request):
    """Verify a batch of URLs and return their reachability status.

    Accepts JSON body: {"urls": ["https://...", ...]}
    Returns: {"results": {"url": true/false, ...}}
    """
    if "application/json" not in request.headers.get("Content-Type", ""):
        return JsonResponse({"error": "Content-Type must be application/json."}, status=400)

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    urls = body.get("urls", [])
    if not isinstance(urls, list) or len(urls) > 20:
        return JsonResponse({"error": "Provide a list of up to 20 URLs."}, status=400)

    clean_urls = [str(u).strip() for u in urls if isinstance(u, str) and str(u).strip()]
    verifier = LinkVerificationService()
    results = verifier.verify_batch(clean_urls)

    return JsonResponse({"results": results})
