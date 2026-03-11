from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urlparse

from apps.web.mock_graph import build_mock_graph_payload

from .contracts import PolymarketEventSnapshot, PolymarketMarket, RelatedEventCandidate
from .polymarket import _market_deep_link


NODE_ID_RE = re.compile(r"[^a-z0-9]+")
TITLE_ENTITY_RE = re.compile(r"\b(?:[A-Z][a-z]+|[A-Z]{2,}|[A-Za-z]+[+-][A-Za-z0-9]+|[A-Z]{2,}[0-9]+)\b")
ENTITY_STOPWORDS = {
    "All",
    "Before",
    "By",
    "Can",
    "Could",
    "Election",
    "Event",
    "In",
    "Market",
    "Markets",
    "The",
    "Will",
    "Yes",
    "No",
}


def _slugify(value: str) -> str:
    slug = NODE_ID_RE.sub("-", value.lower()).strip("-")
    return slug[:48] or "node"


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _shorten(text: str, limit: int = 84) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def _host_label(url: str) -> str:
    host = urlparse(url or "").netloc.replace("www.", "").strip()
    return host or "Public resolution source"


def _confidence(value: float) -> float:
    return round(max(0.45, min(value, 0.99)), 2)


def _normalize_probability(value: float) -> float | None:
    if value <= 0:
        return None
    if value > 1:
        value = value / 100
    return round(max(0, min(value, 1)) * 100, 1)


def _market_probability(market) -> float | None:
    if not getattr(market, "outcome_prices", None):
        return None
    try:
        price = float(market.outcome_prices[0])
    except (TypeError, ValueError, IndexError):
        return None
    return _normalize_probability(price)


def _snapshot_probability(snapshot: PolymarketEventSnapshot) -> float | None:
    for market in snapshot.markets:
        probability = _market_probability(market)
        if probability is not None:
            return probability
    return None


class GraphConstructionService:
    def build(
        self,
        snapshot: PolymarketEventSnapshot,
        related_candidates: list[RelatedEventCandidate],
    ) -> dict:
        if snapshot.source_kind == "fallback" and not related_candidates:
            payload = build_mock_graph_payload(snapshot.canonical_url)
            payload["run"]["builder"] = "apps.web.mock_graph.build_mock_graph_payload"
            return payload

        nodes: list[dict] = []
        edges: list[dict] = []

        event_node = self._event_node(snapshot)
        nodes.append(event_node)

        entity_nodes = self._entity_nodes(snapshot)
        evidence_nodes = self._evidence_nodes(snapshot)
        related_nodes = self._related_market_nodes(snapshot, related_candidates)
        rule_node = self._rule_node(snapshot)
        hypothesis_nodes = self._hypothesis_nodes(snapshot, related_candidates)

        nodes.extend(entity_nodes)
        nodes.extend(related_nodes)
        nodes.extend(evidence_nodes)
        if rule_node:
            nodes.append(rule_node)
        nodes.extend(hypothesis_nodes)

        edges.extend(self._event_edges(entity_nodes, evidence_nodes, related_nodes, rule_node, hypothesis_nodes))
        edges.extend(self._cross_market_edges(entity_nodes, evidence_nodes, related_nodes, hypothesis_nodes))

        return {
            "event": {
                "id": event_node["id"],
                "title": snapshot.title,
                "source_url": snapshot.canonical_url,
                "description": snapshot.description,
                "status": snapshot.status,
                "tags": snapshot.tags,
                "outcomes": snapshot.outcomes or ["Yes", "No"],
                "updated_at": snapshot.updated_at or _iso_now(),
            },
            "run": {
                "id": None,
                "mode": "resolved-backend",
                "persistence": "ephemeral",
                "builder": "apps.web.services.graph_builder.GraphConstructionService",
                "generated_at": _iso_now(),
            },
            "graph": {
                "nodes": nodes,
                "edges": edges,
            },
        }

    def _event_node(self, snapshot: PolymarketEventSnapshot) -> dict:
        probability = _snapshot_probability(snapshot)
        metadata = [
            {"label": "Category", "value": snapshot.category},
            {"label": "Liquidity", "value": f"${snapshot.liquidity:,.0f}" if snapshot.liquidity else "Unavailable"},
            {"label": "Volume", "value": f"${snapshot.volume:,.0f}" if snapshot.volume else "Unavailable"},
            {"label": "Open interest", "value": f"${snapshot.open_interest:,.0f}" if snapshot.open_interest else "Unavailable"},
        ]
        if probability is not None:
            metadata.insert(1, {"label": "Implied chance", "value": f"{probability}%"})

        return {
            "id": "evt_001",
            "label": snapshot.title,
            "type": "Event",
            "confidence": 1.0,
            "probability": probability,
            "probability_label": snapshot.outcomes[0] if snapshot.outcomes else "Implied chance",
            "summary": snapshot.description or snapshot.title,
            "metadata": metadata,
            "evidence_snippets": [],
            "source_url": snapshot.canonical_url,
        }

    def _entity_nodes(self, snapshot: PolymarketEventSnapshot) -> list[dict]:
        labels: list[str] = []
        seen = set()

        for match in TITLE_ENTITY_RE.findall(snapshot.title):
            label = match.strip()
            if label in ENTITY_STOPWORDS or label.isdigit():
                continue
            if label not in seen:
                labels.append(label)
                seen.add(label)

        for tag in snapshot.tags:
            clean = tag.replace("-", " ").strip().title()
            if not clean or clean in ENTITY_STOPWORDS:
                continue
            if clean not in seen:
                labels.append(clean)
                seen.add(clean)

        if not labels:
            labels = [snapshot.category, "Market participants"]

        nodes = []
        for index, label in enumerate(labels[:4], start=1):
            lower = label.lower()
            if lower in {"oil", "energy"}:
                summary = "This topic anchors the main narrative carried by the market contract."
            elif lower in {"shipping", "shipping lanes"}:
                summary = "Transit conditions are a direct channel for market spillover and repricing."
            else:
                summary = f"{label} is a central actor or topic in the event narrative."
            nodes.append(
                {
                    "id": f"ent_{index}_{_slugify(label)}",
                    "label": label,
                    "type": "Entity",
                    "confidence": _confidence(0.66 + index * 0.05),
                    "summary": summary,
                    "metadata": [
                        {"label": "Extraction source", "value": "Event title and tag parsing"},
                        {"label": "Role", "value": "Topic or actor affecting market interpretation"},
                    ],
                    "evidence_snippets": [],
                }
            )
        return nodes

    def _evidence_nodes(self, snapshot: PolymarketEventSnapshot) -> list[dict]:
        nodes = [
            {
                "id": "ev_description",
                "label": "Market description",
                "type": "Evidence",
                "confidence": 0.87,
                "summary": _shorten(snapshot.description or snapshot.title, 150),
                "metadata": [
                    {"label": "Source", "value": "Polymarket event description"},
                    {"label": "Why it matters", "value": "This is the primary statement of what the contract measures."},
                ],
                "evidence_snippets": [_shorten(snapshot.description or snapshot.title, 180)],
            }
        ]

        if snapshot.volume or snapshot.liquidity:
            nodes.append(
                {
                    "id": "ev_market_structure",
                    "label": "Volume and liquidity signal",
                    "type": "Evidence",
                    "confidence": 0.79,
                    "summary": (
                        f"The market has ${snapshot.volume:,.0f} volume and ${snapshot.liquidity:,.0f} liquidity, "
                        "which helps estimate how much attention and pricing depth the event already carries."
                    ),
                    "metadata": [
                        {"label": "Volume", "value": f"${snapshot.volume:,.0f}"},
                        {"label": "Liquidity", "value": f"${snapshot.liquidity:,.0f}"},
                    ],
                    "evidence_snippets": [],
                }
            )

        if snapshot.updated_at:
            nodes.append(
                {
                    "id": "ev_timing",
                    "label": "Market timing context",
                    "type": "Evidence",
                    "confidence": 0.73,
                    "summary": "Recency matters because prediction markets often reprice before the underlying event fully resolves.",
                    "metadata": [
                        {"label": "Last updated", "value": snapshot.updated_at},
                        {"label": "Source kind", "value": snapshot.source_kind},
                    ],
                    "evidence_snippets": [],
                }
            )

        return nodes

    def _related_market_nodes(
        self,
        snapshot: PolymarketEventSnapshot,
        related_candidates: list[RelatedEventCandidate],
    ) -> list[dict]:
        nodes = []
        for index, candidate in enumerate(related_candidates[:4], start=1):
            related = candidate.snapshot
            probability = _snapshot_probability(related)
            metadata = [
                {"label": "Category", "value": related.category},
                {"label": "Shared tags", "value": ", ".join(candidate.shared_tags) or "None surfaced"},
                {"label": "Shared terms", "value": ", ".join(candidate.shared_terms) or "Narrative overlap"},
                {"label": "Observed volume", "value": f"${related.volume:,.0f}" if related.volume else "Unavailable"},
            ]
            if probability is not None:
                metadata.insert(1, {"label": "Implied chance", "value": f"{probability}%"})
            nodes.append(
                {
                    "id": f"mkt_{index}_{_slugify(related.slug or related.title)}",
                    "label": related.title,
                    "type": "RelatedMarket",
                    "confidence": candidate.confidence,
                    "probability": probability,
                    "probability_label": related.outcomes[0] if related.outcomes else "Implied chance",
                    "summary": candidate.rationale,
                    "metadata": metadata,
                    "evidence_snippets": [],
                    "source_url": related.canonical_url,
                }
            )

        if nodes:
            return nodes

        fallback_markets = snapshot.markets[:3]
        fallback_nodes = []
        for index, market in enumerate(fallback_markets, start=1):
            label = market.question or market.slug or f"Adjacent market {index}"
            probability = _market_probability(market)
            metadata = [
                {"label": "Category", "value": market.category or snapshot.category},
                {"label": "Volume", "value": f"${market.volume:,.0f}" if market.volume else "Unavailable"},
            ]
            if probability is not None:
                metadata.insert(1, {"label": "Implied chance", "value": f"{probability}%"})
            fallback_nodes.append(
                {
                    "id": f"mkt_fallback_{index}_{_slugify(label)}",
                    "label": label,
                    "type": "RelatedMarket",
                    "confidence": _confidence(0.61 - index * 0.03),
                    "probability": probability,
                    "probability_label": market.outcomes[0] if market.outcomes else "Implied chance",
                    "summary": "No nearby Polymarket contract was discovered, so ChaosWing is surfacing a directly associated market from the event bundle.",
                    "metadata": metadata,
                    "evidence_snippets": [],
                    "source_url": _canonical_market_source(
                        event_slug=snapshot.slug,
                        market=market,
                        fallback_url=snapshot.canonical_url,
                    ),
                }
            )
        return fallback_nodes

    def _rule_node(self, snapshot: PolymarketEventSnapshot) -> dict | None:
        if not snapshot.resolution_source:
            return None
        return {
            "id": "rule_resolution",
            "label": f"Resolution source: {_host_label(snapshot.resolution_source)}",
            "type": "Rule",
            "confidence": 0.96,
            "summary": "Resolution rules define the public source that ultimately settles the contract.",
            "metadata": [
                {"label": "Resolution source", "value": snapshot.resolution_source},
                {"label": "Why it matters", "value": "Settlement rules cap interpretation risk."},
            ],
            "evidence_snippets": [],
        }

    def _hypothesis_nodes(
        self,
        snapshot: PolymarketEventSnapshot,
        related_candidates: list[RelatedEventCandidate],
    ) -> list[dict]:
        nodes = []
        for index, candidate in enumerate(related_candidates[:2], start=1):
            short_title = _shorten(candidate.snapshot.title, 54)
            nodes.append(
                {
                    "id": f"hyp_{index}_{_slugify(candidate.snapshot.slug or candidate.snapshot.title)}",
                    "label": f"{short_title} reprices through narrative spillover",
                    "type": "Hypothesis",
                    "confidence": _confidence(candidate.confidence - 0.05),
                    "summary": (
                        "If the source event changes trader conviction, adjacent contracts with overlapping tags or "
                        "shared narrative terms may move before hard evidence arrives."
                    ),
                    "metadata": [
                        {"label": "Derived from", "value": candidate.snapshot.title},
                        {"label": "Reason", "value": candidate.rationale},
                    ],
                    "evidence_snippets": [],
                }
            )

        if nodes:
            return nodes

        return [
            {
                "id": "hyp_fallback_narrative",
                "label": "Narrative spillover reprices adjacent contracts",
                "type": "Hypothesis",
                "confidence": 0.61,
                "summary": "Even when the direct contract is isolated, traders often carry the same thesis into nearby markets.",
                "metadata": [
                    {"label": "Derived from", "value": snapshot.category or "Prediction Market"},
                    {"label": "Reason", "value": "Cross-market narrative transfer"},
                ],
                "evidence_snippets": [],
            }
        ]

    def _event_edges(
        self,
        entity_nodes: list[dict],
        evidence_nodes: list[dict],
        related_nodes: list[dict],
        rule_node: dict | None,
        hypothesis_nodes: list[dict],
    ) -> list[dict]:
        edges = []

        for node in entity_nodes:
            edges.append(
                self._edge("evt_001", node["id"], "involves", node["confidence"], "The event narrative directly references this actor or topic.")
            )
        for node in evidence_nodes:
            edges.append(
                self._edge("evt_001", node["id"], "supported_by", node["confidence"], "This evidence helps justify why the event matters or how it resolves.")
            )
        for node in related_nodes:
            edges.append(
                self._edge("evt_001", node["id"], "related_to", node["confidence"], node["summary"])
            )
        if rule_node:
            edges.append(
                self._edge("evt_001", rule_node["id"], "governed_by_rule", rule_node["confidence"], "Settlement follows the public resolution source attached to the event.")
            )
        for node in hypothesis_nodes:
            edges.append(
                self._edge("evt_001", node["id"], "affects_indirectly", node["confidence"], node["summary"])
            )

        return edges

    def _cross_market_edges(
        self,
        entity_nodes: list[dict],
        evidence_nodes: list[dict],
        related_nodes: list[dict],
        hypothesis_nodes: list[dict],
    ) -> list[dict]:
        edges = []
        if related_nodes:
            primary_related = related_nodes[0]
            for entity in entity_nodes[:2]:
                edges.append(
                    self._edge(
                        entity["id"],
                        primary_related["id"],
                        "affects_directly",
                        _confidence(min(entity["confidence"], primary_related["confidence"]) - 0.04),
                        "This topic is one of the clearest channels through which the source event could reprice the adjacent contract.",
                    )
                )
            for evidence in evidence_nodes[:2]:
                edges.append(
                    self._edge(
                        evidence["id"],
                        primary_related["id"],
                        "mentions",
                        _confidence(min(evidence["confidence"], primary_related["confidence"]) - 0.08),
                        "The source evidence gives context for why this related contract belongs in the butterfly graph.",
                    )
                )
            for hypothesis in hypothesis_nodes[:2]:
                edges.append(
                    self._edge(
                        hypothesis["id"],
                        primary_related["id"],
                        "affects_indirectly",
                        _confidence(min(hypothesis["confidence"], primary_related["confidence"]) - 0.03),
                        "The hypothesis describes how conviction could travel into this related market.",
                    )
                )
        return edges

    def _edge(
        self,
        source: str,
        target: str,
        edge_type: str,
        confidence: float,
        explanation: str,
    ) -> dict:
        edge_id = f"edge_{_slugify(source)}_{_slugify(target)}_{edge_type}"
        return {
            "id": edge_id,
            "source": source,
            "target": target,
            "type": edge_type,
            "confidence": _confidence(confidence),
            "explanation": _shorten(explanation, 220),
        }


def _canonical_market_source(
    event_slug: str,
    market: PolymarketMarket,
    fallback_url: str,
) -> str:
    """Build a Polymarket URL for a market, using event#conditionId deep link when possible."""
    if not event_slug:
        return fallback_url
    return _market_deep_link(event_slug, market.id or "")
