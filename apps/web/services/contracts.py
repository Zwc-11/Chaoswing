from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PolymarketMarket":
        return cls(
            id=str(payload.get("id") or ""),
            slug=str(payload.get("slug") or ""),
            question=str(payload.get("question") or ""),
            description=str(payload.get("description") or ""),
            resolution_source=str(payload.get("resolution_source") or ""),
            image_url=str(payload.get("image_url") or ""),
            icon_url=str(payload.get("icon_url") or ""),
            category=str(payload.get("category") or ""),
            outcomes=[str(item) for item in payload.get("outcomes") or [] if str(item)],
            outcome_prices=[
                _to_float(item)
                for item in payload.get("outcome_prices") or []
                if isinstance(item, (float, int, str)) and str(item).strip()
            ],
            volume=_to_float(payload.get("volume")),
            liquidity=_to_float(payload.get("liquidity")),
            end_date=str(payload.get("end_date") or ""),
            updated_at=str(payload.get("updated_at") or ""),
        )


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

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PolymarketEventSnapshot":
        markets = [
            PolymarketMarket.from_dict(item)
            for item in payload.get("markets") or []
            if isinstance(item, dict)
        ]
        return cls(
            source_url=str(payload.get("source_url") or ""),
            canonical_url=str(payload.get("canonical_url") or payload.get("source_url") or ""),
            event_id=str(payload.get("event_id") or ""),
            slug=str(payload.get("slug") or ""),
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or ""),
            resolution_source=str(payload.get("resolution_source") or ""),
            image_url=str(payload.get("image_url") or ""),
            icon_url=str(payload.get("icon_url") or ""),
            status=str(payload.get("status") or "open"),
            category=str(payload.get("category") or ""),
            tags=[str(item) for item in payload.get("tags") or [] if str(item)],
            tag_ids=[str(item) for item in payload.get("tag_ids") or [] if str(item)],
            outcomes=[str(item) for item in payload.get("outcomes") or [] if str(item)],
            updated_at=str(payload.get("updated_at") or ""),
            volume=_to_float(payload.get("volume")),
            liquidity=_to_float(payload.get("liquidity")),
            open_interest=_to_float(payload.get("open_interest")),
            markets=markets,
            source_kind=str(payload.get("source_kind") or "persisted-run"),
            subtitle=str(payload.get("subtitle") or ""),
        )


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

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RelatedEventCandidate":
        return cls(
            snapshot=PolymarketEventSnapshot.from_dict(
                payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
            ),
            confidence=_to_float(payload.get("confidence")),
            rationale=str(payload.get("rationale") or ""),
            shared_tags=[str(item) for item in payload.get("shared_tags") or [] if str(item)],
            shared_terms=[str(item) for item in payload.get("shared_terms") or [] if str(item)],
        )
