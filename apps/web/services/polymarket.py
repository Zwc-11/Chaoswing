from __future__ import annotations

import html
import json
import math
import re
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from django.conf import settings

from apps.web.mock_graph import build_mock_graph_payload

from .contracts import PolymarketEventSnapshot, PolymarketMarket, PolymarketTag, RelatedEventCandidate


GAMMA_API_BASE = "https://gamma-api.polymarket.com"
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
IGNORED_TERMS = {
    "all",
    "and",
    "before",
    "can",
    "could",
    "does",
    "for",
    "from",
    "have",
    "into",
    "market",
    "markets",
    "more",
    "than",
    "that",
    "the",
    "this",
    "will",
    "with",
    "year",
    "2024",
    "2025",
    "2026",
}

META_PATTERNS = {
    "og_title": re.compile(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](?P<value>[^"\']+)',
        re.IGNORECASE,
    ),
    "og_description": re.compile(
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](?P<value>[^"\']+)',
        re.IGNORECASE,
    ),
    "description": re.compile(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](?P<value>[^"\']+)',
        re.IGNORECASE,
    ),
    "og_image": re.compile(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](?P<value>[^"\']+)',
        re.IGNORECASE,
    ),
}


def _extract_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = [part for part in path.split("/") if part]
    return parts[-1] if parts else ""


def _parse_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_json_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _canonical_event_url(slug: str) -> str:
    return f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"


def _event_status(event: dict) -> str:
    if event.get("closed"):
        return "closed"
    if event.get("active"):
        return "open"
    return "inactive"


def _market_outcomes(markets: list[PolymarketMarket]) -> list[str]:
    seen = OrderedDict()
    for market in markets:
        for outcome in market.outcomes:
            clean = str(outcome).strip()
            if clean:
                seen.setdefault(clean, None)
    return list(seen.keys())


def _tokenize(text: str) -> set[str]:
    return {
        token.lower()
        for token in TOKEN_RE.findall(text or "")
        if len(token) > 2 and token.lower() not in IGNORED_TERMS
    }


class GammaPolymarketClient:
    """Small client for the public Gamma API used by ChaosWing runtime workflows."""

    def __init__(self, timeout_seconds: float | None = None):
        self.timeout_seconds = (
            settings.CHAOSWING_HTTP_TIMEOUT_SECONDS
            if timeout_seconds is None
            else timeout_seconds
        )

    def get_event_by_slug(self, slug: str) -> dict | None:
        if not slug:
            return None
        events = self.list_events({"slug": slug})
        return events[0] if events else None

    def list_events(self, params: dict[str, object]) -> list[dict]:
        query = urlencode(
            {
                key: self._normalize_param(value)
                for key, value in params.items()
                if value not in (None, "", [])
            }
        )
        url = f"{GAMMA_API_BASE}/events"
        if query:
            url = f"{url}?{query}"
        payload = self._request_json(url)
        return payload if isinstance(payload, list) else []

    def _request_json(self, url: str):
        return _cached_json_request(url, self.timeout_seconds)

    def _normalize_param(self, value: object) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)


class PolymarketMetadataService:
    """Resolves one Polymarket event into the normalized snapshot used by the graph builder."""

    def __init__(
        self,
        client: GammaPolymarketClient | None = None,
        enable_remote_fetch: bool | None = None,
        timeout_seconds: float | None = None,
    ):
        self.client = client or GammaPolymarketClient(timeout_seconds=timeout_seconds)
        self.enable_remote_fetch = (
            settings.CHAOSWING_ENABLE_REMOTE_FETCH
            if enable_remote_fetch is None
            else enable_remote_fetch
        )
        self.timeout_seconds = (
            settings.CHAOSWING_HTTP_TIMEOUT_SECONDS
            if timeout_seconds is None
            else timeout_seconds
        )

    def hydrate(self, source_url: str) -> PolymarketEventSnapshot:
        fallback = self._fallback_snapshot(source_url)
        if not self.enable_remote_fetch:
            return fallback

        slug = _extract_slug(source_url)
        event_record = None
        try:
            event_record = self.client.get_event_by_slug(slug)
        except Exception:
            event_record = None

        if not event_record:
            html_meta = self._fetch_html_metadata(source_url)
            return self._merge_html_fallback(fallback, html_meta)

        snapshot = self.snapshot_from_event_record(event_record, source_url)
        if not snapshot.description or not snapshot.image_url or not snapshot.icon_url:
            html_meta = self._fetch_html_metadata(source_url)
            if html_meta:
                if not snapshot.description:
                    snapshot.description = html_meta.get("description", "")
                if not snapshot.image_url:
                    snapshot.image_url = html_meta.get("image_url", "")
                if not snapshot.icon_url:
                    snapshot.icon_url = snapshot.image_url

        if not snapshot.description:
            snapshot.description = fallback.description
        if not snapshot.tags:
            snapshot.tags = fallback.tags
        if not snapshot.outcomes:
            snapshot.outcomes = fallback.outcomes

        return snapshot

    def snapshot_from_event_record(self, event: dict, source_url: str) -> PolymarketEventSnapshot:
        tags = [
            PolymarketTag(
                id=str(item.get("id") or ""),
                label=str(item.get("label") or "").strip(),
                slug=str(item.get("slug") or "").strip(),
            )
            for item in event.get("tags", [])
            if str(item.get("label") or "").strip()
        ]
        markets = [self._market_from_record(item) for item in event.get("markets", []) if isinstance(item, dict)]
        slug = str(event.get("slug") or _extract_slug(source_url) or "")
        canonical_url = _canonical_event_url(slug)
        title = str(event.get("title") or "").strip() or slug.replace("-", " ").title()
        description = str(event.get("description") or "").strip()
        image_url = str(event.get("image") or "").strip()
        icon_url = str(event.get("icon") or "").strip() or image_url

        return PolymarketEventSnapshot(
            source_url=source_url,
            canonical_url=canonical_url,
            event_id=str(event.get("id") or ""),
            slug=slug,
            title=title,
            description=description,
            resolution_source=str(event.get("resolutionSource") or "").strip(),
            image_url=image_url,
            icon_url=icon_url,
            status=_event_status(event),
            category=str(event.get("category") or "").strip() or "Other",
            tags=[tag.label for tag in tags if tag.label.lower() != "all"],
            tag_ids=[tag.id for tag in tags if tag.id and tag.label.lower() != "all"],
            outcomes=_market_outcomes(markets),
            updated_at=str(event.get("updatedAt") or event.get("published_at") or "").strip(),
            volume=_parse_float(event.get("volume")),
            liquidity=_parse_float(event.get("liquidity")),
            open_interest=_parse_float(event.get("openInterest")),
            markets=markets,
            source_kind="gamma-api",
            subtitle=self._build_subtitle(event, markets),
        )

    def _market_from_record(self, market: dict) -> PolymarketMarket:
        return PolymarketMarket(
            id=str(market.get("id") or ""),
            slug=str(market.get("slug") or "").strip(),
            question=str(market.get("question") or market.get("slug") or "").strip(),
            description=str(market.get("description") or "").strip(),
            resolution_source=str(market.get("resolutionSource") or "").strip(),
            image_url=str(market.get("image") or "").strip(),
            icon_url=str(market.get("icon") or "").strip() or str(market.get("image") or "").strip(),
            category=str(market.get("category") or "").strip(),
            outcomes=_parse_json_list(market.get("outcomes")),
            outcome_prices=[_parse_float(value) for value in _parse_json_list(market.get("outcomePrices"))],
            volume=_parse_float(market.get("volumeNum") or market.get("volume")),
            liquidity=_parse_float(market.get("liquidityNum") or market.get("liquidity")),
            end_date=str(market.get("endDate") or "").strip(),
            updated_at=str(market.get("updatedAt") or "").strip(),
        )

    def _build_subtitle(self, event: dict, markets: list[PolymarketMarket]) -> str:
        parts = []
        category = str(event.get("category") or "").strip()
        if category:
            parts.append(category)
        if markets:
            parts.append(f"{len(markets)} market{'s' if len(markets) != 1 else ''}")
        volume = _parse_float(event.get("volume"))
        if volume:
            parts.append(f"${volume:,.0f} volume")
        return " | ".join(parts)

    def _fetch_html_metadata(self, source_url: str) -> dict[str, str]:
        try:
            html_text = _cached_html_request(source_url, self.timeout_seconds)
        except Exception:
            return {}

        return {
            "title": self._extract_meta(html_text, "og_title"),
            "description": self._extract_meta(html_text, "og_description")
            or self._extract_meta(html_text, "description"),
            "image_url": self._extract_meta(html_text, "og_image"),
        }

    def _extract_meta(self, html_text: str, key: str) -> str:
        match = META_PATTERNS[key].search(html_text)
        if not match:
            return ""
        return html.unescape(match.group("value")).strip()

    def _merge_html_fallback(
        self, fallback: PolymarketEventSnapshot, html_meta: dict[str, str]
    ) -> PolymarketEventSnapshot:
        if not html_meta:
            return fallback
        fallback.title = html_meta.get("title") or fallback.title
        fallback.description = html_meta.get("description") or fallback.description
        fallback.image_url = html_meta.get("image_url") or fallback.image_url
        fallback.icon_url = fallback.image_url or fallback.icon_url
        return fallback

    def _fallback_snapshot(self, source_url: str) -> PolymarketEventSnapshot:
        payload = build_mock_graph_payload(source_url)
        event = payload["event"]
        event_nodes = [node for node in payload["graph"]["nodes"] if node["type"] == "RelatedMarket"]
        markets = [
            PolymarketMarket(
                id=node["id"],
                slug=_extract_slug(node.get("source_url") or ""),
                question=node["label"],
                description=node["summary"],
                resolution_source="",
                image_url="",
                icon_url="",
                category="Prediction Market",
                outcomes=["Yes", "No"],
            )
            for node in event_nodes
        ]
        description = (
            f"Polymarket event under analysis: {event['title']}. "
            "ChaosWing is using deterministic fallback metadata because live Polymarket resolution was unavailable."
        )
        slug = _extract_slug(source_url)
        return PolymarketEventSnapshot(
            source_url=source_url,
            canonical_url=event["source_url"],
            event_id="",
            slug=slug,
            title=event["title"],
            description=description,
            resolution_source="",
            image_url="",
            icon_url="",
            status=event.get("status", "open"),
            category="Prediction Market",
            tags=event.get("tags", []),
            tag_ids=[],
            outcomes=event.get("outcomes", ["Yes", "No"]),
            updated_at=event.get("updated_at", ""),
            volume=0.0,
            liquidity=0.0,
            open_interest=0.0,
            markets=markets,
            source_kind="fallback",
            subtitle="Deterministic fallback source",
        )


class RelatedMarketDiscoveryService:
    """Finds adjacent Polymarket events that can seed related-market graph branches."""

    def __init__(
        self,
        client: GammaPolymarketClient | None = None,
        metadata_service: PolymarketMetadataService | None = None,
        enable_remote_fetch: bool | None = None,
    ):
        self.client = client or GammaPolymarketClient()
        self.metadata_service = metadata_service or PolymarketMetadataService(client=self.client)
        self.enable_remote_fetch = (
            settings.CHAOSWING_ENABLE_REMOTE_FETCH
            if enable_remote_fetch is None
            else enable_remote_fetch
        )

    def discover(self, snapshot: PolymarketEventSnapshot, limit: int = 4) -> list[RelatedEventCandidate]:
        if not self.enable_remote_fetch or snapshot.source_kind == "fallback":
            return []

        source_terms = _tokenize(" ".join([snapshot.title, snapshot.description, " ".join(snapshot.tags)]))
        source_tags = {tag.lower() for tag in snapshot.tags}
        candidates: dict[str, RelatedEventCandidate] = {}

        for record in self._candidate_records(snapshot):
            candidate_snapshot = self.metadata_service.snapshot_from_event_record(
                record,
                _canonical_event_url(str(record.get("slug") or "")),
            )
            if not candidate_snapshot.slug or candidate_snapshot.slug == snapshot.slug:
                continue
            if candidate_snapshot.status != "open":
                continue

            score = self._score_candidate(snapshot, candidate_snapshot, source_terms, source_tags)
            if not score:
                continue
            confidence, rationale, shared_tags, shared_terms = score
            existing = candidates.get(candidate_snapshot.slug)
            if existing and existing.confidence >= confidence:
                continue
            candidates[candidate_snapshot.slug] = RelatedEventCandidate(
                snapshot=candidate_snapshot,
                confidence=confidence,
                rationale=rationale,
                shared_tags=shared_tags,
                shared_terms=shared_terms,
            )

        ordered = sorted(candidates.values(), key=lambda item: item.confidence, reverse=True)
        return ordered[:limit]

    def _candidate_records(self, snapshot: PolymarketEventSnapshot) -> list[dict]:
        seen: dict[str, dict] = OrderedDict()

        for tag_id in snapshot.tag_ids[:3]:
            try:
                records = self.client.list_events(
                    {
                        "tag_id": tag_id,
                        "related_tags": True,
                        "active": True,
                        "closed": False,
                        "limit": 18,
                        "order": "volume",
                        "ascending": False,
                    }
                )
            except Exception:
                records = []
            self._remember_candidates(seen, records)

        if len(seen) < 12:
            try:
                records = self.client.list_events(
                    {
                        "active": True,
                        "closed": False,
                        "limit": 40,
                        "order": "volume24hr",
                        "ascending": False,
                    }
                )
            except Exception:
                records = []
            self._remember_candidates(seen, records)

        return list(seen.values())

    def _remember_candidates(self, seen: dict[str, dict], records: list[dict]) -> None:
        for record in records:
            slug = str(record.get("slug") or "").strip()
            if slug and slug not in seen:
                seen[slug] = record

    def _score_candidate(
        self,
        snapshot: PolymarketEventSnapshot,
        candidate: PolymarketEventSnapshot,
        source_terms: set[str],
        source_tags: set[str],
    ) -> tuple[float, str, list[str], list[str]] | None:
        candidate_tags = {tag.lower() for tag in candidate.tags}
        candidate_terms = _tokenize(" ".join([candidate.title, candidate.description, " ".join(candidate.tags)]))
        shared_tags = sorted(source_tags & candidate_tags)
        shared_terms = sorted(term for term in (source_terms & candidate_terms) if len(term) > 3)[:4]

        if not shared_tags and not shared_terms and candidate.category != snapshot.category:
            return None

        score = 0.42
        score += min(len(shared_tags), 3) * 0.11
        score += min(len(shared_terms), 4) * 0.05
        if candidate.category == snapshot.category:
            score += 0.08
        score += min(math.log10(candidate.volume + 1) / 18, 0.09)
        score += min(math.log10(candidate.liquidity + 1) / 24, 0.05)
        confidence = round(max(0.45, min(score, 0.94)), 2)

        rationale_bits = []
        if shared_tags:
            rationale_bits.append(f"shared tags: {', '.join(shared_tags[:2])}")
        if shared_terms:
            rationale_bits.append(f"title overlap: {', '.join(shared_terms[:2])}")
        if candidate.category == snapshot.category:
            rationale_bits.append(f"same category: {candidate.category}")
        if not rationale_bits:
            rationale_bits.append("adjacent active market discovered through Polymarket event ranking")

        return confidence, "; ".join(rationale_bits), shared_tags, shared_terms


class TrendingMarketsService:
    """Fetches the highest-volume active Polymarket events for the Starter Markets section.

    Results are cached with a configurable TTL to avoid hammering the Gamma API.
    """

    _cache: dict[str, tuple[list[dict], float]] = {}
    _cache_lock = threading.Lock()

    def __init__(
        self,
        client: GammaPolymarketClient | None = None,
        cache_ttl: int | None = None,
    ):
        self.client = client or GammaPolymarketClient()
        self.cache_ttl = cache_ttl or getattr(settings, "CHAOSWING_TRENDING_CACHE_TTL", 300)

    def get_trending(self, limit: int = 6) -> list[dict]:
        """Return top Polymarket events by 24h volume.

        Each entry includes: slug, title, url, volume_24h, category, status, image_url.
        """
        cache_key = f"trending:{limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        if not settings.CHAOSWING_ENABLE_REMOTE_FETCH:
            return []

        events = self._fetch_trending(limit)
        self._set_cached(cache_key, events)
        return events

    def _fetch_trending(self, limit: int) -> list[dict]:
        try:
            records = self.client.list_events({
                "active": True,
                "closed": False,
                "limit": limit * 3,
                "order": "volume24hr",
                "ascending": False,
            })
        except Exception:
            return []

        results = []
        for record in records:
            slug = str(record.get("slug") or "").strip()
            title = str(record.get("title") or "").strip()
            if not slug or not title:
                continue

            volume = _parse_float(record.get("volume"))
            category = str(record.get("category") or "Other").strip()
            image_url = str(record.get("image") or record.get("icon") or "").strip()

            results.append({
                "slug": slug,
                "title": title,
                "url": _canonical_event_url(slug),
                "caption": self._build_caption(record),
                "volume": volume,
                "category": category,
                "image_url": image_url,
                "status": _event_status(record),
            })

            if len(results) >= limit:
                break

        return results

    def _build_caption(self, record: dict) -> str:
        parts = []
        category = str(record.get("category") or "").strip()
        if category:
            parts.append(category)
        markets = record.get("markets", [])
        if isinstance(markets, list) and markets:
            parts.append(f"{len(markets)} market{'s' if len(markets) != 1 else ''}")
        volume = _parse_float(record.get("volume"))
        if volume > 0:
            parts.append(f"${volume:,.0f} total volume")
        return " · ".join(parts) if parts else "Active Polymarket event"

    def _get_cached(self, key: str) -> list[dict] | None:
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            data, ts = entry
            if time.monotonic() - ts > self.cache_ttl:
                del self._cache[key]
                return None
            return data

    def _set_cached(self, key: str, data: list[dict]) -> None:
        with self._cache_lock:
            self._cache[key] = (data, time.monotonic())


@lru_cache(maxsize=256)
def _cached_json_request(url: str, timeout_seconds: float):
    request = Request(
        url,
        headers={
            "User-Agent": "ChaosWing/0.1 (+https://chaos-wing.com)",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset, errors="ignore"))


@lru_cache(maxsize=128)
def _cached_html_request(source_url: str, timeout_seconds: float) -> str:
    request = Request(
        source_url,
        headers={
            "User-Agent": "ChaosWing/0.1 (+https://chaos-wing.com)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="ignore")
