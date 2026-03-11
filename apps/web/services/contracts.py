from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class PolymarketTag:
    id: str
    label: str
    slug: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PolymarketMarket:
    id: str
    slug: str
    question: str
    description: str
    resolution_source: str
    image_url: str
    icon_url: str
    category: str
    outcomes: list[str] = field(default_factory=list)
    outcome_prices: list[float] = field(default_factory=list)
    volume: float = 0.0
    liquidity: float = 0.0
    end_date: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PolymarketEventSnapshot:
    source_url: str
    canonical_url: str
    event_id: str
    slug: str
    title: str
    description: str
    resolution_source: str
    image_url: str
    icon_url: str
    status: str
    category: str
    tags: list[str] = field(default_factory=list)
    tag_ids: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    updated_at: str = ""
    volume: float = 0.0
    liquidity: float = 0.0
    open_interest: float = 0.0
    markets: list[PolymarketMarket] = field(default_factory=list)
    source_kind: str = "gamma-api"
    subtitle: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["markets"] = [market.as_dict() for market in self.markets]
        return payload


@dataclass(slots=True)
class RelatedEventCandidate:
    snapshot: PolymarketEventSnapshot
    confidence: float
    rationale: str
    shared_tags: list[str] = field(default_factory=list)
    shared_terms: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot.as_dict(),
            "confidence": self.confidence,
            "rationale": self.rationale,
            "shared_tags": self.shared_tags,
            "shared_terms": self.shared_terms,
        }
