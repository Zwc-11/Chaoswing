from __future__ import annotations

import json
from typing import Any

from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST


def _load_json_body(request) -> dict[str, Any]:
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Request body must be valid JSON.") from exc


def _humanize_type(value: str) -> str:
    mapping = {
        "Event": "Event",
        "Entity": "Entity",
        "RelatedMarket": "Related market",
        "Evidence": "Evidence",
        "Rule": "Rule",
        "Hypothesis": "Hypothesis",
    }
    return mapping.get(value, value or "Unknown")


def _humanize_relationship(value: str) -> str:
    return " ".join(word.capitalize() for word in (value or "").split("_")) or "Unknown"


def _confidence_percent(value: Any) -> int:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0
    return max(0, min(100, round(confidence * 100)))


def _format_confidence(value: Any) -> str:
    return f"{_confidence_percent(value)}%"


def _clean_metadata(items: Any) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []

    metadata = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        if label and value:
            metadata.append({"label": label, "value": value})
    return metadata


def _clean_snippets(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(item).strip() for item in items if str(item).strip()]


def _bar(label: str, value: int, caption: str, tone: str = "accent") -> dict[str, Any]:
    bounded = max(0, min(100, int(value)))
    return {
        "label": label,
        "value": bounded,
        "caption": caption,
        "tone": tone,
    }


def _node_bars(
    confidence: Any, metadata: list[dict[str, str]], snippets: list[str], summary: str
) -> list[dict[str, Any]]:
    confidence_score = _confidence_percent(confidence)
    context_score = min(100, 18 + len(metadata) * 18 + (14 if summary else 0))
    support_score = min(100, len(snippets) * 32)
    clarity_score = min(100, 24 + min(len(summary), 140) // 2) if summary else 20

    return [
        _bar("Confidence", confidence_score, "How strong this signal looks in the current graph.", "accent"),
        _bar("Context", context_score, "How much supporting metadata is attached to this node.", "cool"),
        _bar("Support", support_score, "How much evidence text is available for quick review.", "warm"),
        _bar("Clarity", clarity_score, "How easy this selection is to understand at a glance.", "violet"),
    ]


def _edge_directness(relationship: str) -> int:
    mapping = {
        "affects_directly": 92,
        "governed_by_rule": 86,
        "supported_by": 78,
        "involves": 70,
        "related_to": 62,
        "affects_indirectly": 58,
        "mentions": 44,
    }
    return mapping.get(relationship, 60)


def _edge_bars(confidence: Any, relationship: str, explanation: str) -> list[dict[str, Any]]:
    confidence_score = _confidence_percent(confidence)
    directness_score = _edge_directness(relationship)
    explanation_score = min(100, 18 + min(len(explanation), 180) // 2) if explanation else 24
    signal_score = round((confidence_score + directness_score + explanation_score) / 3)

    return [
        _bar("Confidence", confidence_score, "How strong this relationship is in the current graph.", "accent"),
        _bar("Directness", directness_score, "Whether the link is direct, indirect, or rule-based.", "warm"),
        _bar("Explanation", explanation_score, "How much context is available for this connection.", "cool"),
        _bar("Overall signal", signal_score, "A simplified blended view for quick comparison.", "violet"),
    ]


@require_GET
def inspector_empty(request):
    return render(
        request,
        "web/partials/inspector_empty.html",
        {
            "shortcut_hints": [
                {"key": "1", "label": "Load a market"},
                {"key": "2", "label": "Hover to preview"},
                {"key": "3", "label": "Click to lock and read"},
            ]
        },
    )


@require_POST
def inspector_node(request):
    try:
        payload = _load_json_body(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    label = str(payload.get("label") or "").strip()
    node_type = str(payload.get("type") or "").strip()
    if not label or not node_type:
        return HttpResponseBadRequest("Node payload must include `label` and `type`.")

    summary = str(payload.get("summary") or "").strip()
    metadata = _clean_metadata(payload.get("metadata"))
    evidence_snippets = _clean_snippets(payload.get("evidence_snippets"))

    return render(
        request,
        "web/partials/inspector_node.html",
        {
            "label": label,
            "icon_url": str(payload.get("icon_url") or "").strip(),
            "source_url": str(payload.get("source_url") or "").strip(),
            "source_title": str(payload.get("source_title") or "").strip(),
            "source_description": str(payload.get("source_description") or "").strip(),
            "summary": summary,
            "bars": _node_bars(payload.get("confidence"), metadata, evidence_snippets, summary),
            "detail_items": [
                {"label": "Type", "value": _humanize_type(node_type)},
                {"label": "Confidence", "value": _format_confidence(payload.get("confidence"))},
            ],
            "metadata": metadata,
            "evidence_snippets": evidence_snippets,
            "type_label": _humanize_type(node_type),
        },
    )


@require_POST
def inspector_edge(request):
    try:
        payload = _load_json_body(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    source_label = str(payload.get("source_label") or "").strip()
    target_label = str(payload.get("target_label") or "").strip()
    relationship = str(payload.get("type") or "").strip()
    if not source_label or not target_label or not relationship:
        return HttpResponseBadRequest(
            "Edge payload must include `source_label`, `target_label`, and `type`."
        )

    explanation = str(payload.get("explanation") or "").strip()

    return render(
        request,
        "web/partials/inspector_edge.html",
        {
            "headline": f"{source_label} -> {target_label}",
            "explanation": explanation,
            "bars": _edge_bars(payload.get("confidence"), relationship, explanation),
            "detail_items": [
                {"label": "Relationship", "value": _humanize_relationship(relationship)},
                {"label": "Confidence", "value": _format_confidence(payload.get("confidence"))},
                {"label": "Source", "value": source_label},
                {"label": "Target", "value": target_label},
            ],
        },
    )
