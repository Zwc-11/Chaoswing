from __future__ import annotations

import json
import logging
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.web.models import (
    CrossVenueMarketMap,
    ExperimentRun,
    LeadLagPair,
    LeadLagSignal,
    MarketEventTick,
    OrderBookLevelSnapshot,
    PaperTrade,
)

from .polymarket import GammaPolymarketClient


logger = logging.getLogger("apps.web.services.leadlag")

LEADLAG_SUMMARY_CACHE_KEY = "chaoswing:leadlag:summary:v1"
TOKEN_RE = __import__("re").compile(r"[A-Za-z0-9]+")
TOKEN_ALIASES = {
    "presidential": "president",
    "presidency": "president",
    "democratic": "democrat",
    "democrats": "democrat",
    "republicans": "republican",
    "nomination": "nominee",
    "governorship": "governor",
    "governors": "governor",
    "crude": "oil",
    "fomc": "fed",
    "btc": "bitcoin",
    "nba": "basketball",
    "uefa": "soccer",
    "fifa": "soccer",
}
IGNORED_TOKENS = {
    "a",
    "an",
    "and",
    "be",
    "by",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "will",
    "with",
    "year",
    "2024",
    "2025",
    "2026",
    "2027",
    "2028",
    "2029",
    "2030",
}
THEME_STOPWORDS = {
    "after",
    "all",
    "any",
    "are",
    "before",
    "date",
    "during",
    "must",
    "market",
    "markets",
    "not",
    "official",
    "party",
    "person",
    "people",
    "question",
    "source",
    "term",
    "this",
    "united",
    "states",
    "what",
    "who",
    "year",
}
GENERIC_TOPIC_TOKENS = {
    "after",
    "before",
    "candidate",
    "candidates",
    "election",
    "game",
    "games",
    "half",
    "market",
    "markets",
    "meeting",
    "nomination",
    "nominee",
    "official",
    "office",
    "party",
    "politics",
    "president",
    "presidential",
    "prime",
    "question",
    "reaction",
    "resolution",
    "resolve",
    "series",
    "set",
    "states",
    "this",
    "winner",
    "winners",
    "win",
    "wins",
    "world",
    "sports",
    "elections",
    "yes",
    "next",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}
MACRO_DRIVER_TOKENS = {
    "fed",
    "fomc",
    "rates",
    "cpi",
    "inflation",
    "oil",
    "crude",
    "opec",
    "tariffs",
    "war",
    "strike",
    "iran",
    "israel",
    "gdp",
    "jobs",
    "payrolls",
    "unemployment",
}
ASSET_RESPONSE_TOKENS = {
    "bitcoin",
    "btc",
    "eth",
    "stock",
    "stocks",
    "spy",
    "nasdaq",
    "treasury",
    "yield",
    "gold",
    "tesla",
    "nvidia",
    "usd",
}
PAIR_TYPE_WEIGHTS = {
    "logical_equivalent": 1.0,
    "shared_driver": 0.9,
    "narrative_spillover": 0.75,
}


def _now() -> datetime:
    return timezone.now()


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip_probability(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _parse_timestamp(value) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        return _now()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return _now()


def _tokenize(*values: str) -> set[str]:
    return {
        TOKEN_ALIASES.get(token.lower(), token.lower())
        for value in values
        for token in TOKEN_RE.findall(value or "")
        if len(token) > 2 and TOKEN_ALIASES.get(token.lower(), token.lower()) not in IGNORED_TOKENS
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _topic_tokens(*values: str) -> set[str]:
    return {token for token in _tokenize(*values) if token not in GENERIC_TOPIC_TOKENS}


def _theme_tokens(*values: str) -> set[str]:
    return {token for token in _tokenize(*values) if token not in THEME_STOPWORDS}


def _normalize_levels(levels) -> list[dict[str, float]]:
    normalized: list[dict[str, float]] = []
    if not isinstance(levels, list):
        return normalized
    for item in levels:
        if isinstance(item, dict):
            normalized.append(
                {
                    "price": _to_float(item.get("price")),
                    "size": _to_float(item.get("size") or item.get("quantity")),
                }
            )
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            normalized.append({"price": _to_float(item[0]), "size": _to_float(item[1])})
    return [level for level in normalized if level["price"] > 0 or level["size"] > 0]


def _sum_depth(levels: list[dict[str, float]]) -> float:
    return sum(_to_float(level.get("size")) for level in levels)


def _pair_title(pair: LeadLagPair) -> str:
    return f"{pair.leader_market.title} -> {pair.follower_market.title}"


def _money_to_float(value) -> float:
    numeric = _to_float(value)
    if numeric and numeric > 1:
        return numeric / 100.0 if numeric <= 100 else numeric
    return numeric


@dataclass(slots=True)
class NormalizedTick:
    venue: str
    market_id: str
    market_slug: str = ""
    event_type: str = "ticker"
    status: str = ""
    exchange_timestamp: datetime = field(default_factory=_now)
    received_at: datetime = field(default_factory=_now)
    sequence_id: str = ""
    last_price: float = 0.0
    yes_bid: float = 0.0
    yes_ask: float = 0.0
    no_bid: float = 0.0
    no_ask: float = 0.0
    bid_size: float = 0.0
    ask_size: float = 0.0
    trade_size: float = 0.0
    volume: float = 0.0
    open_interest: float = 0.0
    bids: list[dict[str, float]] = field(default_factory=list)
    asks: list[dict[str, float]] = field(default_factory=list)
    payload: dict = field(default_factory=dict)


class KalshiRESTClient:
    def __init__(self, *, base_url: str | None = None, timeout_seconds: float | None = None):
        self.base_url = (base_url or settings.CHAOSWING_KALSHI_API_BASE).rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.CHAOSWING_HTTP_TIMEOUT_SECONDS

    def list_markets(
        self,
        *,
        limit: int = 100,
        status: str = "open",
        mve_filter: str | None = None,
    ) -> list[dict]:
        params = {"limit": limit, "status": status}
        if mve_filter:
            params["mve_filter"] = mve_filter
        payload = self._request_json(f"{self.base_url}/markets?{urlencode(params)}")
        if isinstance(payload, dict):
            markets = payload.get("markets") or payload.get("data") or []
            return markets if isinstance(markets, list) else []
        return payload if isinstance(payload, list) else []

    def list_events(
        self,
        *,
        limit: int = 100,
        status: str = "open",
        with_nested_markets: bool = True,
    ) -> list[dict]:
        params = {"limit": limit, "status": status}
        if with_nested_markets:
            params["with_nested_markets"] = "true"
        payload = self._request_json(f"{self.base_url}/events?{urlencode(params)}")
        if isinstance(payload, dict):
            events = payload.get("events") or payload.get("data") or []
            return events if isinstance(events, list) else []
        return payload if isinstance(payload, list) else []

    def get_market(self, ticker: str) -> dict:
        payload = self._request_json(f"{self.base_url}/markets/{ticker}")
        if isinstance(payload, dict) and "market" in payload and isinstance(payload["market"], dict):
            return payload["market"]
        return payload if isinstance(payload, dict) else {}

    def get_orderbook(self, ticker: str, *, depth: int = 10) -> dict:
        payload = self._request_json(
            f"{self.base_url}/markets/{ticker}/orderbook?{urlencode({'depth': depth})}"
        )
        if isinstance(payload, dict) and "orderbook" in payload and isinstance(payload["orderbook"], dict):
            return payload["orderbook"]
        return payload if isinstance(payload, dict) else {}

    def _request_json(self, url: str):
        request = Request(
            url,
            headers={
                "User-Agent": "ChaosWing/0.1 (+https://chaoswing.onrender.com)",
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset, errors="ignore"))


class CrossVenueMarketMapService:
    def __init__(
        self,
        *,
        polymarket_client: GammaPolymarketClient | None = None,
        kalshi_client: KalshiRESTClient | None = None,
    ):
        self.polymarket_client = polymarket_client or GammaPolymarketClient()
        self.kalshi_client = kalshi_client or KalshiRESTClient()

    def sync(self, *, limit_per_venue: int = 40, persist: bool = True) -> dict[str, int]:
        counts = {"polymarket": 0, "kalshi": 0, "created": 0, "updated": 0, "deactivated": 0}
        polymarket_rows = self._load_polymarket_rows(limit=limit_per_venue)
        kalshi_rows = self._load_kalshi_rows(limit=limit_per_venue, reference_rows=polymarket_rows)
        synced_ids = {"polymarket": set(), "kalshi": set()}

        if not persist:
            counts["polymarket"] = len(polymarket_rows)
            counts["kalshi"] = len(kalshi_rows)
            return counts

        for row in [*polymarket_rows, *kalshi_rows]:
            defaults = row.copy()
            venue = defaults.pop("venue")
            market_id = defaults.pop("market_id")
            obj, created = CrossVenueMarketMap.objects.update_or_create(
                venue=venue,
                market_id=market_id,
                defaults=defaults,
            )
            synced_ids[venue].add(market_id)
            counts[venue] += 1
            counts["created" if created else "updated"] += 1
            if obj.status == "closed" and obj.is_active:
                obj.is_active = False
                obj.save(update_fields=["is_active"])

        for venue, venue_market_ids in synced_ids.items():
            if venue_market_ids:
                counts["deactivated"] += self._deactivate_unsynced_rows(
                    venue=venue,
                    synced_market_ids=venue_market_ids,
                )
        counts["deactivated"] += self._deactivate_stale_kalshi_rows()
        LeadLagMonitorService.invalidate_cache()
        from .market_intelligence import BenchmarkSummaryService

        BenchmarkSummaryService.invalidate_cached_summary()
        return counts

    def _load_polymarket_rows(self, *, limit: int) -> list[dict]:
        rows: list[dict] = []
        try:
            records = self.polymarket_client.list_events(
                {
                    "active": True,
                    "closed": False,
                    "limit": max(limit * 3, 30),
                    "order": "volume24hr",
                    "ascending": False,
                }
            )
        except Exception as exc:
            logger.warning("Polymarket market-map sync failed: %s", exc)
            return rows

        for event in records:
            event_slug = str(event.get("slug") or "").strip()
            event_title = str(event.get("title") or "").strip()
            event_description = str(event.get("description") or "").strip()
            category = str(event.get("category") or "").strip()
            status = "closed" if event.get("closed") else "open"
            tags = [
                str(tag.get("label") or "").strip()
                for tag in event.get("tags", [])
                if isinstance(tag, dict) and str(tag.get("label") or "").strip()
            ]
            for market in event.get("markets", []) or []:
                if not isinstance(market, dict):
                    continue
                market_id = str(market.get("conditionId") or market.get("id") or "").strip()
                market_slug = str(market.get("slug") or "").strip()
                title = self._build_polymarket_title(
                    event_title=event_title,
                    question=str(market.get("question") or "").strip(),
                    market_slug=market_slug,
                )
                if not market_id or not title:
                    continue
                clob_token_ids = market.get("clobTokenIds") or market.get("clob_token_ids") or []
                if isinstance(clob_token_ids, str):
                    try:
                        clob_token_ids = json.loads(clob_token_ids)
                    except json.JSONDecodeError:
                        clob_token_ids = []
                rows.append(
                    {
                        "venue": "polymarket",
                        "market_id": market_id,
                        "market_slug": market_slug or event_slug,
                        "event_slug": event_slug,
                        "title": title,
                        "url": f"https://polymarket.com/event/{event_slug}" if event_slug else "",
                        "category": category,
                        "status": status,
                        "outcome_type": "binary",
                        "tags": tags,
                        "resolution_text": event_description,
                        "resolution_window": str(
                            market.get("endDate") or event.get("endDate") or ""
                        ).strip()[:32],
                        "metadata": {
                            "event_id": str(event.get("id") or ""),
                            "event_title": event_title,
                            "outcomes": market.get("outcomes") or [],
                            "outcome_prices": market.get("outcomePrices") or [],
                            "clob_token_ids": clob_token_ids if isinstance(clob_token_ids, list) else [],
                            "condition_id": market_id,
                            "volume": _to_float(market.get("volumeNum") or market.get("volume")),
                            "liquidity": _to_float(
                                market.get("liquidityNum") or market.get("liquidity")
                            ),
                        },
                        "is_active": status == "open",
                    }
                )
        return self._select_polymarket_rows(rows, limit=limit)

    def _build_polymarket_title(self, *, event_title: str, question: str, market_slug: str) -> str:
        base_title = " ".join((event_title or "").split())
        question_title = " ".join((question or "").split())
        if not question_title:
            return base_title or market_slug
        if not base_title:
            return question_title
        if _tokenize(question_title).issubset(_tokenize(base_title)):
            return base_title
        if _jaccard(_tokenize(base_title), _tokenize(question_title)) >= 0.55:
            return question_title
        joiner = " - " if base_title.endswith("?") else ": "
        return f"{base_title}{joiner}{question_title}"

    def _select_polymarket_rows(self, rows: list[dict], *, limit: int) -> list[dict]:
        best_by_market: dict[str, dict] = {}
        for row in rows:
            market_id = str(row.get("market_id") or "")
            if not market_id:
                continue
            existing = best_by_market.get(market_id)
            if existing is None or self._polymarket_row_priority(row) > self._polymarket_row_priority(existing):
                best_by_market[market_id] = row

        ranked = sorted(
            best_by_market.values(),
            key=self._polymarket_row_priority,
            reverse=True,
        )
        selected: list[dict] = []
        per_event = Counter()
        for row in ranked:
            event_key = str(row.get("event_slug") or row.get("market_id") or "")
            if per_event[event_key] >= 2:
                continue
            selected.append(row)
            per_event[event_key] += 1
            if len(selected) >= limit:
                return selected

        for row in ranked:
            if row in selected:
                continue
            selected.append(row)
            if len(selected) >= limit:
                break
        return selected

    def _polymarket_row_priority(self, row: dict) -> float:
        metadata = row.get("metadata") or {}
        title_tokens = _theme_tokens(
            str(row.get("title") or ""),
            str(metadata.get("event_title") or ""),
            " ".join(row.get("tags") or []),
        )
        volume = _to_float(metadata.get("volume"))
        liquidity = _to_float(metadata.get("liquidity"))
        depth_score = math.log1p(max(0.0, volume + liquidity))
        token_score = min(len(title_tokens), 10) * 0.15
        macro_bonus = 1.0 if title_tokens & MACRO_DRIVER_TOKENS else 0.0
        asset_bonus = 0.5 if title_tokens & ASSET_RESPONSE_TOKENS else 0.0
        return depth_score + token_score + macro_bonus + asset_bonus

    def _load_kalshi_rows(self, *, limit: int, reference_rows: list[dict] | None = None) -> list[dict]:
        rows: list[dict] = []
        try:
            events = self.kalshi_client.list_events(
                limit=200,
                status="open",
                with_nested_markets=True,
            )
        except Exception as exc:
            logger.warning("Kalshi event sync failed, falling back to market feed: %s", exc)
            events = []

        for event in events:
            if not isinstance(event, dict):
                continue
            for market in event.get("markets", []) or []:
                row = self._build_kalshi_row(market, event=event)
                if not row:
                    continue
                rows.append(row)

        try:
            records = self.kalshi_client.list_markets(
                limit=max(limit * 25, 250),
                status="open",
                mve_filter="exclude",
            )
        except Exception as exc:
            logger.warning("Kalshi market feed fallback sync failed: %s", exc)
            records = []

        for market in records:
            row = self._build_kalshi_row(market)
            if not row:
                continue
            rows.append(row)

        if not rows:
            return rows
        reference_rows = reference_rows or []
        return self._select_kalshi_rows(
            rows,
            limit=limit,
            reference_tokens=self._reference_token_counts(reference_rows),
            reference_entity_tokens=self._reference_entity_token_counts(reference_rows),
        )

    def _clean_kalshi_title(self, title: str) -> str:
        return " ".join(str(title or "").split())

    def _build_kalshi_row(self, market: dict, *, event: dict | None = None) -> dict | None:
        if not isinstance(market, dict) or not self._is_kalshi_mapping_candidate(market):
            return None
        ticker = str(market.get("ticker") or market.get("market_ticker") or "").strip()
        if not ticker:
            return None
        title = self._build_kalshi_title(event, market, fallback=ticker)
        if not title:
            return None
        status = str(market.get("status") or event and event.get("status") or "open").lower()
        resolution_text = " ".join(
            part
            for part in [
                str(market.get("rules_primary") or "").strip(),
                str(market.get("rules_secondary") or "").strip(),
                str(event.get("sub_title") if event else "").strip(),
                str(event.get("subtitle") if event else "").strip(),
                str(market.get("subtitle") or "").strip(),
            ]
            if part
        )
        category = str(
            (event or {}).get("category")
            or market.get("category")
            or (event or {}).get("series_ticker")
            or market.get("series_ticker")
            or ""
        ).strip()
        return {
            "venue": "kalshi",
            "market_id": ticker,
            "market_slug": ticker.lower(),
            "event_slug": str(
                market.get("event_ticker") or (event or {}).get("event_ticker") or ""
            ).strip().lower(),
            "title": title,
            "url": f"https://kalshi.com/markets/{ticker}",
            "category": category,
            "status": status,
            "outcome_type": "binary",
            "tags": self._build_kalshi_tags(event, market),
            "resolution_text": resolution_text,
            "resolution_window": str(
                market.get("close_time")
                or market.get("expiration_time")
                or market.get("settlement_date")
                or (event or {}).get("close_time")
                or ""
            ).strip()[:32],
            "metadata": {
                "event_ticker": str(
                    market.get("event_ticker") or (event or {}).get("event_ticker") or ""
                ),
                "event_title": str((event or {}).get("title") or ""),
                "volume": _to_float(
                    market.get("volume")
                    or market.get("volume_24h")
                    or market.get("volume_fp")
                    or market.get("volume_24h_fp")
                ),
                "open_interest": _to_float(
                    market.get("open_interest") or market.get("open_interest_fp")
                ),
                "yes_bid": _money_to_float(
                    market.get("yes_bid")
                    or market.get("yes_bid_dollars")
                    or market.get("previous_yes_bid_dollars")
                ),
                "yes_ask": _money_to_float(
                    market.get("yes_ask")
                    or market.get("yes_ask_dollars")
                    or market.get("previous_yes_ask_dollars")
                ),
                "last_price": _money_to_float(
                    market.get("last_price")
                    or market.get("last_price_dollars")
                    or market.get("previous_price_dollars")
                ),
                "liquidity": _money_to_float(
                    market.get("liquidity") or market.get("liquidity_dollars")
                ),
                "yes_sub_title": str(market.get("yes_sub_title") or "").strip(),
                "no_sub_title": str(market.get("no_sub_title") or "").strip(),
            },
            "is_active": status in {
                "open",
                "active",
                "trading",
                "initialized",
            },
        }

    def _build_kalshi_title(self, event: dict | None, market: dict, *, fallback: str) -> str:
        event_title = self._clean_kalshi_title(str((event or {}).get("title") or "").strip())
        market_title = self._clean_kalshi_title(str(market.get("title") or "").strip())
        candidate_title = self._clean_kalshi_title(
            str(
                market.get("yes_sub_title")
                or market.get("subtitle")
                or market.get("no_sub_title")
                or ""
            ).strip()
        )
        base_title = event_title or market_title or fallback
        if market_title and _jaccard(_tokenize(base_title), _tokenize(market_title)) < 0.55:
            base_title = market_title
        if candidate_title and not _tokenize(candidate_title).issubset(_tokenize(base_title)):
            joiner = " - " if base_title.endswith("?") else ": "
            return f"{base_title}{joiner}{candidate_title}"
        return base_title

    def _build_kalshi_tags(self, event: dict | None, market: dict) -> list[str]:
        values = [
            (event or {}).get("category"),
            market.get("category"),
            (event or {}).get("series_ticker"),
            market.get("series_ticker"),
            market.get("yes_sub_title"),
            market.get("no_sub_title"),
        ]
        tags: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            tags.append(text)
        return tags

    def _reference_token_counts(self, rows: list[dict]) -> Counter:
        counts: Counter = Counter()
        for row in rows:
            counts.update(
                _theme_tokens(
                    str(row.get("title") or ""),
                    " ".join(row.get("tags") or []),
                )
            )
        return counts

    def _reference_entity_token_counts(self, rows: list[dict]) -> Counter:
        counts: Counter = Counter()
        for row in rows:
            counts.update(
                _topic_tokens(
                    str(row.get("title") or ""),
                    " ".join(row.get("tags") or []),
                )
            )
        return counts

    def _select_kalshi_rows(
        self,
        rows: list[dict],
        *,
        limit: int,
        reference_tokens: Counter,
        reference_entity_tokens: Counter,
    ) -> list[dict]:
        best_by_market: dict[str, dict] = {}
        for row in rows:
            market_id = str(row.get("market_id") or "")
            if not market_id:
                continue
            existing = best_by_market.get(market_id)
            if existing is None or self._kalshi_row_priority(
                row,
                reference_tokens,
                reference_entity_tokens,
            ) > self._kalshi_row_priority(
                existing,
                reference_tokens,
                reference_entity_tokens,
            ):
                best_by_market[market_id] = row

        ranked = sorted(
            best_by_market.values(),
            key=lambda item: self._kalshi_row_priority(
                item,
                reference_tokens,
                reference_entity_tokens,
            ),
            reverse=True,
        )
        overlap_ranked = [
            row
            for row in ranked
            if self._reference_overlap_score(
                row,
                reference_tokens,
                reference_entity_tokens,
            )
            > 0
        ]
        selected: list[dict] = []
        per_event = Counter()
        for row in overlap_ranked:
            event_key = str(row.get("event_slug") or row.get("market_id") or "")
            if per_event[event_key] >= 2:
                continue
            selected.append(row)
            per_event[event_key] += 1
            if len(selected) >= limit:
                return selected

        for row in ranked:
            if row in selected:
                continue
            selected.append(row)
            if len(selected) >= limit:
                break
        return selected

    def _kalshi_row_priority(
        self,
        row: dict,
        reference_tokens: Counter,
        reference_entity_tokens: Counter,
    ) -> float:
        metadata = row.get("metadata") or {}
        overlap_score = self._reference_overlap_score(
            row,
            reference_tokens,
            reference_entity_tokens,
        )
        depth_score = math.log1p(
            max(
                0.0,
                _to_float(metadata.get("volume"))
                + _to_float(metadata.get("open_interest"))
                + (_to_float(metadata.get("liquidity")) * 100.0),
            )
        )
        price_presence = 1.0 if max(
            _to_float(metadata.get("yes_bid")),
            _to_float(metadata.get("yes_ask")),
            _to_float(metadata.get("last_price")),
        ) > 0 else 0.0
        candidate_granularity = 0.75 if str(metadata.get("yes_sub_title") or "").strip() else 0.0
        overlap_boost = 3.0 if overlap_score > 0 else 0.0
        return overlap_score * 4.0 + depth_score + price_presence + candidate_granularity + overlap_boost

    def _reference_overlap_score(
        self,
        row: dict,
        reference_tokens: Counter,
        reference_entity_tokens: Counter,
    ) -> float:
        row_tokens = _theme_tokens(
            str(row.get("title") or ""),
            " ".join(row.get("tags") or []),
        )
        row_entity_tokens = _topic_tokens(
            str(row.get("title") or ""),
            " ".join(row.get("tags") or []),
        )
        theme_score = float(len(row_tokens & set(reference_tokens.keys())))
        entity_score = float(len(row_entity_tokens & set(reference_entity_tokens.keys())))
        return theme_score + entity_score * 8.0

    def _is_kalshi_mapping_candidate(self, market: dict) -> bool:
        ticker = str(market.get("ticker") or market.get("market_ticker") or "").strip().upper()
        event_ticker = str(market.get("event_ticker") or "").strip().upper()
        title = " ".join(
            str(
                part or ""
            ).strip()
            for part in [
                market.get("title"),
                market.get("subtitle"),
                market.get("yes_sub_title"),
                market.get("no_sub_title"),
            ]
            if str(part or "").strip()
        )
        market_type = str(market.get("market_type") or "binary").strip().lower()
        if market_type and market_type != "binary":
            return False
        if market.get("mve_selected_legs") or ticker.startswith("KXMVE") or event_ticker.startswith("KXMVE"):
            return False
        if title.lower().count("yes ") > 1 or title.count(",") >= 3:
            return False
        liquidity = _money_to_float(market.get("liquidity") or market.get("liquidity_dollars"))
        open_interest = _to_float(market.get("open_interest") or market.get("open_interest_fp"))
        yes_bid = _money_to_float(market.get("yes_bid") or market.get("yes_bid_dollars"))
        yes_ask = _money_to_float(market.get("yes_ask") or market.get("yes_ask_dollars"))
        last_price = _money_to_float(market.get("last_price") or market.get("last_price_dollars"))
        if max(liquidity, open_interest, yes_bid, yes_ask, last_price) <= 0:
            return False
        return True

    def _deactivate_stale_kalshi_rows(self) -> int:
        stale_rows = []
        for row in CrossVenueMarketMap.objects.filter(venue="kalshi", is_active=True):
            if self._is_persisted_kalshi_candidate(row):
                continue
            row.is_active = False
            if row.status in {"open", "active", "trading", "initialized"}:
                row.status = "filtered"
            metadata = dict(row.metadata or {})
            metadata["leadlag_filtered_reason"] = "non_candidate"
            row.metadata = metadata
            stale_rows.append(row)
        if stale_rows:
            CrossVenueMarketMap.objects.bulk_update(stale_rows, ["is_active", "status", "metadata"])
        return len(stale_rows)

    def _deactivate_unsynced_rows(self, *, venue: str, synced_market_ids: set[str]) -> int:
        return CrossVenueMarketMap.objects.filter(venue=venue, is_active=True).exclude(
            market_id__in=synced_market_ids
        ).update(is_active=False)

    def _is_persisted_kalshi_candidate(self, row: CrossVenueMarketMap) -> bool:
        metadata = row.metadata or {}
        title = str(row.title or "")
        if str(row.market_id or "").upper().startswith("KXMVE"):
            return False
        if title.lower().count("yes ") > 1 or title.count(",") >= 3:
            return False
        liquidity = _to_float(metadata.get("liquidity"))
        open_interest = _to_float(metadata.get("open_interest"))
        yes_bid = _to_float(metadata.get("yes_bid"))
        yes_ask = _to_float(metadata.get("yes_ask"))
        last_price = _to_float(metadata.get("last_price"))
        return max(liquidity, open_interest, yes_bid, yes_ask, last_price) > 0


class LeadLagPairBuilderService:
    def build(self, *, persist: bool = True) -> dict[str, int]:
        markets = list(CrossVenueMarketMap.objects.filter(is_active=True))
        polymarket_markets = [market for market in markets if market.venue == "polymarket"]
        kalshi_markets = [market for market in markets if market.venue == "kalshi"]
        counts = {"pairs_considered": 0, "created": 0, "updated": 0, "eligible": 0, "deactivated": 0}
        top_candidates: list[dict] = []
        active_pair_ids: set[int] = set()

        for left in polymarket_markets:
            for right in kalshi_markets:
                counts["pairs_considered"] += 1
                leader, follower, payload = self._score_pair(left, right)
                self._remember_candidate(top_candidates, leader, follower, payload)
                if payload["composite_score"] < 0.38:
                    continue
                if not persist:
                    if payload["is_trade_eligible"]:
                        counts["eligible"] += 1
                    continue
                pair, created = LeadLagPair.objects.update_or_create(
                    leader_market=leader,
                    follower_market=follower,
                    pair_type=payload["pair_type"],
                    defaults=payload,
                )
                active_pair_ids.add(pair.id)
                counts["created" if created else "updated"] += 1
                if pair.is_trade_eligible:
                    counts["eligible"] += 1

        if persist:
            counts["deactivated"] = self._deactivate_unsynced_pairs(active_pair_ids)
            LeadLagMonitorService.invalidate_cache()
            from .market_intelligence import BenchmarkSummaryService

            BenchmarkSummaryService.invalidate_cached_summary()
        coverage = self._coverage_summary(
            polymarket_markets=polymarket_markets,
            kalshi_markets=kalshi_markets,
            counts=counts,
            top_candidates=top_candidates,
        )
        return {
            **counts,
            "coverage_status": coverage["status"],
            "coverage_summary": coverage["summary"],
            "coverage_notes": coverage["notes"],
            "shared_topics": coverage["shared_topics"],
            "top_candidates": top_candidates,
        }

    def _score_pair(
        self,
        left: CrossVenueMarketMap,
        right: CrossVenueMarketMap,
    ) -> tuple[CrossVenueMarketMap, CrossVenueMarketMap, dict]:
        left_title_tokens = _theme_tokens(left.title)
        right_title_tokens = _theme_tokens(right.title)
        left_entity_tokens = _topic_tokens(left.title, " ".join(left.tags or []))
        right_entity_tokens = _topic_tokens(right.title, " ".join(right.tags or []))
        shared_entity_tokens = left_entity_tokens & right_entity_tokens
        left_theme_tokens = _theme_tokens(left.title, left.resolution_text, " ".join(left.tags or []))
        right_theme_tokens = _theme_tokens(right.title, right.resolution_text, " ".join(right.tags or []))
        theme_similarity = _jaccard(left_theme_tokens, right_theme_tokens)
        theme_overlap = left_theme_tokens & right_theme_tokens
        entity_equivalence_score = (
            0.52 if len(shared_entity_tokens) >= 2 and (theme_similarity >= 0.2 or len(theme_overlap) >= 3) else 0.0
        )
        left_tokens = _tokenize(left.title, left.resolution_text, " ".join(left.tags or []))
        right_tokens = _tokenize(right.title, right.resolution_text, " ".join(right.tags or []))
        semantic_score = max(
            _jaccard(left_tokens, right_tokens),
            round(theme_similarity * 0.95, 4),
            min(len(shared_entity_tokens) * 0.22, 0.88),
            entity_equivalence_score,
        )
        title_similarity = max(_jaccard(left_title_tokens, right_title_tokens), theme_similarity)
        left_time_tokens = _tokenize(left.resolution_window)
        right_time_tokens = _tokenize(right.resolution_window)
        normalized_left_title = " ".join(sorted(left_title_tokens))
        normalized_right_title = " ".join(sorted(right_title_tokens))
        resolution_score = max(
            _jaccard(left_time_tokens, right_time_tokens),
            semantic_score * 0.65,
            title_similarity * 0.8,
            0.55 if shared_entity_tokens and theme_similarity >= 0.35 else 0.0,
        )

        left_driver = len(left_tokens & MACRO_DRIVER_TOKENS)
        right_driver = len(right_tokens & MACRO_DRIVER_TOKENS)
        left_asset = len(left_tokens & ASSET_RESPONSE_TOKENS)
        right_asset = len(right_tokens & ASSET_RESPONSE_TOKENS)

        pair_type = "narrative_spillover"
        if (
            (semantic_score >= 0.62 and resolution_score >= 0.55)
            or normalized_left_title == normalized_right_title
            or title_similarity >= 0.72
            or (
                len(shared_entity_tokens) >= 2
                and (theme_similarity >= 0.28 or len(theme_overlap) >= 3 or resolution_score >= 0.45)
            )
        ):
            pair_type = "logical_equivalent"
        elif (
            max(left_driver, right_driver)
            and max(left_asset, right_asset)
            and (theme_similarity >= 0.18 or semantic_score >= 0.24)
        ):
            pair_type = "shared_driver"

        if pair_type == "logical_equivalent":
            left_liquidity = _to_float((left.metadata or {}).get("liquidity") or (left.metadata or {}).get("volume"))
            right_liquidity = _to_float((right.metadata or {}).get("open_interest") or (right.metadata or {}).get("volume"))
            if left_liquidity >= right_liquidity:
                leader, follower = left, right
            else:
                leader, follower = right, left
            causal_score = 0.58 + min(abs(left_liquidity - right_liquidity) / 100_000, 0.2)
            if len(shared_entity_tokens) >= 2 and theme_similarity >= 0.35:
                causal_score = max(causal_score, 0.66)
            direction_reason = "Equivalent contracts; the more liquid venue is treated as the leader."
        else:
            left_driver_strength = left_driver * 0.18 + left_asset * 0.05
            right_driver_strength = right_driver * 0.18 + right_asset * 0.05
            if left_driver_strength >= right_driver_strength:
                leader, follower = left, right
                causal_score = min(0.42 + left_driver_strength + right_asset * 0.08, 0.95)
            else:
                leader, follower = right, left
                causal_score = min(0.42 + right_driver_strength + left_asset * 0.08, 0.95)
            direction_reason = self._direction_reason(leader, follower)

        if pair_type == "narrative_spillover" and theme_similarity < 0.15 and len(shared_entity_tokens) < 1:
            causal_score = min(causal_score, 0.35)
            direction_reason = (
                "Rejected as a tradable lead-lag hypothesis because the two markets do not share enough "
                "topic overlap after normalization."
            )

        expected_latency_seconds = 45 if pair_type == "logical_equivalent" else 120
        stability = self._stability_snapshot(
            leader,
            follower,
            expected_latency_seconds=expected_latency_seconds,
        )
        stability_score = stability["score"]
        composite_score = (
            semantic_score * 0.35
            + causal_score * 0.3
            + resolution_score * 0.2
            + stability_score * 0.15
        ) * PAIR_TYPE_WEIGHTS[pair_type]
        is_trade_eligible = (
            composite_score >= 0.58
            and resolution_score >= 0.35
            and causal_score >= 0.5
            and stability["readiness_status"] == "ready"
        )
        return leader, follower, {
            "semantic_score": round(semantic_score, 4),
            "causal_score": round(causal_score, 4),
            "resolution_score": round(resolution_score, 4),
            "stability_score": round(stability_score, 4),
            "composite_score": round(composite_score, 4),
            "expected_latency_seconds": expected_latency_seconds,
            "direction_reason": direction_reason,
            "pair_type": pair_type,
            "is_trade_eligible": is_trade_eligible,
            "metadata": {
                "leader_tokens": sorted(_topic_tokens(leader.title))[:8],
                "follower_tokens": sorted(_topic_tokens(follower.title))[:8],
                "shared_entity_tokens": sorted(shared_entity_tokens)[:6],
                "theme_overlap": sorted(theme_overlap)[:8],
                "stability_samples": stability["aligned_samples"],
                "move_samples": stability["move_samples"],
                "leader_tick_count": stability["leader_tick_count"],
                "follower_tick_count": stability["follower_tick_count"],
                "avg_price_gap": stability["avg_price_gap"],
                "sign_agreement": stability["sign_agreement"],
                "leader_first_ratio": stability["leader_first_ratio"],
                "avg_lead_seconds": stability["avg_lead_seconds"],
                "readiness_status": stability["readiness_status"],
                "readiness_reason": stability["readiness_reason"],
            },
            "is_active": True,
        }

    def _remember_candidate(
        self,
        top_candidates: list[dict],
        leader: CrossVenueMarketMap,
        follower: CrossVenueMarketMap,
        payload: dict,
    ) -> None:
        entry = {
            "id": None,
            "title": f"{leader.title} -> {follower.title}",
            "leader_market": leader.title,
            "follower_market": follower.title,
            "pair_type": payload["pair_type"],
            "composite_score": payload["composite_score"],
            "semantic_score": payload["semantic_score"],
            "stability_score": payload["stability_score"],
            "resolution_score": payload["resolution_score"],
            "causal_score": payload["causal_score"],
            "expected_latency_seconds": payload["expected_latency_seconds"],
            "is_trade_eligible": payload["is_trade_eligible"],
            "direction_reason": payload["direction_reason"],
            "readiness_status": payload["metadata"]["readiness_status"],
            "readiness_reason": payload["metadata"]["readiness_reason"],
            "leader_first_ratio": payload["metadata"]["leader_first_ratio"],
            "avg_lead_seconds": payload["metadata"]["avg_lead_seconds"],
            "move_samples": payload["metadata"]["move_samples"],
        }
        top_candidates.append(entry)
        top_candidates.sort(
            key=lambda item: (
                item["composite_score"],
                item["semantic_score"],
                item["resolution_score"],
            ),
            reverse=True,
        )
        del top_candidates[5:]

    def _coverage_summary(
        self,
        *,
        polymarket_markets: list[CrossVenueMarketMap],
        kalshi_markets: list[CrossVenueMarketMap],
        counts: dict[str, int],
        top_candidates: list[dict],
    ) -> dict:
        shared_topics = self._shared_topics(polymarket_markets, kalshi_markets)
        if not polymarket_markets or not kalshi_markets:
            return {
                "status": "needs_sync",
                "summary": "Sync both venues before claiming any cross-venue lead-lag coverage.",
                "notes": [
                    "One or both venue catalogs are empty, so the monitor cannot screen live cross-venue pairs yet.",
                ],
                "shared_topics": shared_topics,
            }
        if counts["eligible"] > 0:
            return {
                "status": "candidate_pairs_live",
                "summary": "Trade-eligible candidate pairs are available for paper trading.",
                "notes": [
                    "Keep treating the system as paper trading only until the net-of-cost ledger stays positive out of sample.",
                ],
                "shared_topics": shared_topics,
            }
        if counts["created"] or counts["updated"]:
            return {
                "status": "watch_only",
                "summary": "Cross-venue pairs exist, but none currently clear the trade-eligibility bar.",
                "notes": [
                    "Use the current registry as a watchlist and diagnostics layer, not a trading system.",
                ],
                "shared_topics": shared_topics,
            }
        strongest = top_candidates[0] if top_candidates else None
        notes = [
            "Current live Polymarket and Kalshi catalogs do not share enough defensible overlap for seconds-to-minutes paper trading.",
            "This is the expected falsification outcome when the market universe is mismatched; ChaosWing should downgrade to alerts and diagnostics instead of implying alpha.",
        ]
        if strongest:
            notes.append(
                f"Strongest near-miss: {strongest['title']} (score {strongest['composite_score']:.2f}, semantic {strongest['semantic_score']:.2f})."
            )
        return {
            "status": "insufficient_overlap",
            "summary": "No defensible live cross-venue pairs are available in the current synced universe.",
            "notes": notes,
            "shared_topics": shared_topics,
        }

    def _shared_topics(
        self,
        polymarket_markets: list[CrossVenueMarketMap],
        kalshi_markets: list[CrossVenueMarketMap],
    ) -> list[str]:
        polymarket_counter: Counter = Counter()
        kalshi_counter: Counter = Counter()
        for market in polymarket_markets:
            polymarket_counter.update(
                _theme_tokens(market.title, " ".join(market.tags or []))
            )
        for market in kalshi_markets:
            kalshi_counter.update(
                _theme_tokens(market.title, " ".join(market.tags or []))
            )
        shared = []
        for token in polymarket_counter.keys() & kalshi_counter.keys():
            shared.append((token, polymarket_counter[token] + kalshi_counter[token]))
        shared.sort(key=lambda item: item[1], reverse=True)
        return [token for token, _score in shared[:8]]

    def _direction_reason(
        self,
        leader: CrossVenueMarketMap,
        follower: CrossVenueMarketMap,
    ) -> str:
        return (
            f"{leader.title} scores as the likely leader because it looks more like a driver market, "
            f"while {follower.title} looks more like the reacting contract."
        )

    def _historical_stability(
        self,
        leader: CrossVenueMarketMap,
        follower: CrossVenueMarketMap,
    ) -> float:
        return self._stability_snapshot(
            leader,
            follower,
            expected_latency_seconds=120,
        )["score"]

    def _stability_snapshot(
        self,
        leader: CrossVenueMarketMap,
        follower: CrossVenueMarketMap,
        *,
        expected_latency_seconds: int,
    ) -> dict[str, float | int | str]:
        leader_ticks = list(
            MarketEventTick.objects.filter(
                venue=leader.venue,
                market_id=leader.market_id,
                last_price__gt=0,
            )
            .order_by("-exchange_timestamp")[:24]
        )
        follower_ticks = list(
            MarketEventTick.objects.filter(
                venue=follower.venue,
                market_id=follower.market_id,
                last_price__gt=0,
            )
            .order_by("-exchange_timestamp")[:24]
        )
        if len(leader_ticks) < 4 or len(follower_ticks) < 4:
            return {
                "score": 0.0,
                "aligned_samples": 0,
                "move_samples": 0,
                "leader_tick_count": len(leader_ticks),
                "follower_tick_count": len(follower_ticks),
                "avg_price_gap": 0.0,
                "sign_agreement": 0.0,
                "leader_first_ratio": 0.0,
                "avg_lead_seconds": 0.0,
                "readiness_status": "needs_history",
                "readiness_reason": "Collect at least four positive-price ticks on both venues before using stability.",
            }

        leader_ticks = list(reversed(leader_ticks))
        follower_ticks = list(reversed(follower_ticks))
        aligned_pairs = self._align_tick_pairs(
            leader_ticks,
            follower_ticks,
            max_window_seconds=max(expected_latency_seconds * 4, 60),
        )
        if len(aligned_pairs) < 3:
            return {
                "score": 0.0,
                "aligned_samples": len(aligned_pairs),
                "move_samples": 0,
                "leader_tick_count": len(leader_ticks),
                "follower_tick_count": len(follower_ticks),
                "avg_price_gap": 0.0,
                "sign_agreement": 0.0,
                "leader_first_ratio": 0.0,
                "avg_lead_seconds": 0.0,
                "readiness_status": "needs_alignment",
                "readiness_reason": "Both venues have ticks, but not enough time-aligned observations yet.",
            }

        leader_deltas = []
        follower_deltas = []
        price_gaps = []
        lead_times = []
        for prior_pair, pair in zip(aligned_pairs, aligned_pairs[1:]):
            leader_deltas.append(pair[0].last_price - prior_pair[0].last_price)
            follower_deltas.append(pair[1].last_price - prior_pair[1].last_price)
            price_gaps.append(abs(pair[0].last_price - pair[1].last_price))
            lead_times.append((pair[1].exchange_timestamp - pair[0].exchange_timestamp).total_seconds())

        move_pairs = [
            (leader_delta, follower_delta, lead_seconds)
            for leader_delta, follower_delta, lead_seconds in zip(leader_deltas, follower_deltas, lead_times)
            if abs(leader_delta) > 1e-6 and abs(follower_delta) > 1e-6
        ]
        if not move_pairs:
            return {
                "score": 0.0,
                "aligned_samples": len(aligned_pairs),
                "move_samples": 0,
                "leader_tick_count": len(leader_ticks),
                "follower_tick_count": len(follower_ticks),
                "avg_price_gap": round(sum(price_gaps) / len(price_gaps), 4) if price_gaps else 0.0,
                "sign_agreement": 0.0,
                "leader_first_ratio": 0.0,
                "avg_lead_seconds": 0.0,
                "readiness_status": "flat_series",
                "readiness_reason": "Aligned ticks exist, but neither venue has enough movement to estimate stability.",
            }

        sign_agreement = sum(
            1
            for leader_delta, follower_delta, _lead_seconds in move_pairs
            if math.copysign(1, leader_delta) == math.copysign(1, follower_delta)
        ) / len(move_pairs)
        leader_abs = [abs(delta) for delta, _follower_delta, _lead_seconds in move_pairs]
        follower_abs = [abs(delta) for _leader_delta, delta, _lead_seconds in move_pairs]
        leader_avg = sum(leader_abs) / len(leader_abs)
        follower_avg = sum(follower_abs) / len(follower_abs)
        move_ratio = max(
            0.0,
            1.0 - abs(leader_avg - follower_avg) / max(leader_avg, follower_avg, 1e-6),
        )
        leader_first_ratio = sum(
            1
            for _leader_delta, _follower_delta, lead_seconds in move_pairs
            if 0 < lead_seconds <= max(expected_latency_seconds * 2, 30)
        ) / len(move_pairs)
        positive_leads = [
            lead_seconds
            for _leader_delta, _follower_delta, lead_seconds in move_pairs
            if 0 < lead_seconds <= max(expected_latency_seconds * 2, 30)
        ]
        avg_lead_seconds = sum(positive_leads) / len(positive_leads) if positive_leads else 0.0
        avg_gap = sum(price_gaps) / len(price_gaps) if price_gaps else 0.0
        gap_score = max(0.0, 1.0 - avg_gap / 0.2)
        sample_score = min(len(aligned_pairs) / 6.0, 1.0)
        lead_score = min(leader_first_ratio * 1.1, 1.0)
        stability_score = round(
            sign_agreement * 0.35
            + move_ratio * 0.2
            + lead_score * 0.2
            + gap_score * 0.15
            + sample_score * 0.1,
            4,
        )
        if len(aligned_pairs) < 6 or len(move_pairs) < 4:
            readiness_status = "collect_more"
            readiness_reason = (
                "The pair is directionally plausible, but it still needs more synchronized movement history."
            )
        elif leader_first_ratio < 0.5:
            readiness_status = "watch_only"
            readiness_reason = (
                "Both venues move together too often; the leader has not shown enough first-move behavior yet."
            )
        elif sign_agreement < 0.55:
            readiness_status = "watch_only"
            readiness_reason = (
                "Aligned moves exist, but the signs are not consistent enough to trust the directionality."
            )
        else:
            readiness_status = "ready"
            readiness_reason = "Enough aligned ticks exist to evaluate the pair as a lead-lag watch candidate."
        return {
            "score": stability_score,
            "aligned_samples": len(aligned_pairs),
            "move_samples": len(move_pairs),
            "leader_tick_count": len(leader_ticks),
            "follower_tick_count": len(follower_ticks),
            "avg_price_gap": round(avg_gap, 4),
            "sign_agreement": round(sign_agreement, 4),
            "leader_first_ratio": round(leader_first_ratio, 4),
            "avg_lead_seconds": round(avg_lead_seconds, 2),
            "readiness_status": readiness_status,
            "readiness_reason": readiness_reason,
        }

    def _align_tick_pairs(
        self,
        leader_ticks: list[MarketEventTick],
        follower_ticks: list[MarketEventTick],
        *,
        max_window_seconds: int,
    ) -> list[tuple[MarketEventTick, MarketEventTick]]:
        aligned: list[tuple[MarketEventTick, MarketEventTick]] = []
        follower_index = 0
        follower_count = len(follower_ticks)
        for leader_tick in leader_ticks:
            while (
                follower_index + 1 < follower_count
                and follower_ticks[follower_index + 1].exchange_timestamp <= leader_tick.exchange_timestamp
            ):
                follower_index += 1
            candidates = [follower_ticks[follower_index]]
            if follower_index + 1 < follower_count:
                candidates.append(follower_ticks[follower_index + 1])
            best = min(
                candidates,
                key=lambda tick: abs((tick.exchange_timestamp - leader_tick.exchange_timestamp).total_seconds()),
            )
            delta_seconds = abs((best.exchange_timestamp - leader_tick.exchange_timestamp).total_seconds())
            if delta_seconds <= max_window_seconds:
                aligned.append((leader_tick, best))
        deduped: list[tuple[MarketEventTick, MarketEventTick]] = []
        last_key = None
        for leader_tick, follower_tick in aligned:
            key = (leader_tick.id, follower_tick.id)
            if key == last_key:
                continue
            deduped.append((leader_tick, follower_tick))
            last_key = key
        return deduped

    def _deactivate_unsynced_pairs(self, active_pair_ids: set[int]) -> int:
        queryset = LeadLagPair.objects.filter(is_active=True)
        if active_pair_ids:
            queryset = queryset.exclude(id__in=active_pair_ids)
        return queryset.update(is_active=False)


class LeadLagTickCollectionService:
    def __init__(
        self,
        *,
        polymarket_client: GammaPolymarketClient | None = None,
        kalshi_client: KalshiRESTClient | None = None,
    ):
        self.polymarket_client = polymarket_client or GammaPolymarketClient()
        self.kalshi_client = kalshi_client or KalshiRESTClient()

    def collect(
        self,
        *,
        venues: list[str] | None = None,
        market_limit: int = 10,
        iterations: int = 1,
        poll_seconds: int | None = None,
        active_pairs_only: bool = False,
        fixture_path: Path | None = None,
        persist: bool = True,
    ) -> dict:
        if fixture_path:
            ticks = self._load_fixture_ticks(fixture_path)
            return self._persist_ticks(ticks, persist=persist)

        venue_list = [venue for venue in (venues or ["polymarket", "kalshi"]) if venue]
        results = Counter()
        loops = max(int(iterations), 1)
        pause = settings.CHAOSWING_LEADLAG_POLL_SECONDS if poll_seconds is None else max(int(poll_seconds), 0)
        for index in range(loops):
            ticks: list[NormalizedTick] = []
            if "polymarket" in venue_list:
                ticks.extend(
                    self._collect_polymarket_ticks(
                        limit=market_limit,
                        active_pairs_only=active_pairs_only,
                    )
                )
            if "kalshi" in venue_list:
                ticks.extend(
                    self._collect_kalshi_ticks(
                        limit=market_limit,
                        active_pairs_only=active_pairs_only,
                    )
                )
            tick_result = self._persist_ticks(ticks, persist=persist)
            results.update(tick_result)
            if index < loops - 1 and pause:
                time.sleep(pause)
        LeadLagMonitorService.invalidate_cache()
        if persist:
            from .market_intelligence import BenchmarkSummaryService

            BenchmarkSummaryService.invalidate_cached_summary()
        return dict(results)

    def _load_fixture_ticks(self, fixture_path: Path) -> list[NormalizedTick]:
        ticks: list[NormalizedTick] = []
        with fixture_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                ticks.append(
                    NormalizedTick(
                        venue=str(payload.get("venue") or ""),
                        market_id=str(payload.get("market_id") or ""),
                        market_slug=str(payload.get("market_slug") or ""),
                        event_type=str(payload.get("event_type") or "ticker"),
                        status=str(payload.get("status") or ""),
                        exchange_timestamp=_parse_timestamp(payload.get("exchange_timestamp")),
                        received_at=_parse_timestamp(payload.get("received_at")),
                        sequence_id=str(payload.get("sequence_id") or ""),
                        last_price=_to_float(payload.get("last_price")),
                        yes_bid=_to_float(payload.get("yes_bid")),
                        yes_ask=_to_float(payload.get("yes_ask")),
                        no_bid=_to_float(payload.get("no_bid")),
                        no_ask=_to_float(payload.get("no_ask")),
                        bid_size=_to_float(payload.get("bid_size")),
                        ask_size=_to_float(payload.get("ask_size")),
                        trade_size=_to_float(payload.get("trade_size")),
                        volume=_to_float(payload.get("volume")),
                        open_interest=_to_float(payload.get("open_interest")),
                        bids=_normalize_levels(payload.get("bids")),
                        asks=_normalize_levels(payload.get("asks")),
                        payload=payload,
                    )
                )
        return ticks

    def _collect_polymarket_ticks(self, *, limit: int, active_pairs_only: bool = False) -> list[NormalizedTick]:
        ticks: list[NormalizedTick] = []
        markets = self._market_selection(
            venue="polymarket",
            limit=limit,
            active_pairs_only=active_pairs_only,
        )
        for market in markets:
            event_slug = market.event_slug or market.market_slug
            if not event_slug:
                continue
            try:
                event = self.polymarket_client.get_event_by_slug(event_slug)
            except Exception as exc:
                logger.debug("Polymarket event refresh failed for %s: %s", market.market_id, exc)
                continue
            if not event:
                continue
            matched_market = None
            for candidate in event.get("markets", []) or []:
                candidate_id = str(candidate.get("conditionId") or candidate.get("id") or "").strip()
                if candidate_id == market.market_id:
                    matched_market = candidate
                    break
            if not matched_market:
                continue
            outcome_prices = matched_market.get("outcomePrices") or matched_market.get("outcome_prices") or []
            if isinstance(outcome_prices, str):
                try:
                    outcome_prices = json.loads(outcome_prices)
                except json.JSONDecodeError:
                    outcome_prices = []
            yes_price = _clip_probability(_to_float(outcome_prices[0])) if outcome_prices else 0.0
            yes_bid = _clip_probability(_to_float(matched_market.get("bestBid") or yes_price - 0.01))
            yes_ask = _clip_probability(_to_float(matched_market.get("bestAsk") or yes_price + 0.01))
            no_bid = _clip_probability(1.0 - yes_ask)
            no_ask = _clip_probability(1.0 - yes_bid)
            bids = [
                {
                    "price": yes_bid,
                    "size": _to_float(matched_market.get("liquidityNum") or matched_market.get("liquidity")),
                }
            ]
            asks = [
                {
                    "price": yes_ask,
                    "size": _to_float(matched_market.get("liquidityNum") or matched_market.get("liquidity")),
                }
            ]
            ticks.append(
                NormalizedTick(
                    venue="polymarket",
                    market_id=market.market_id,
                    market_slug=market.market_slug,
                    event_type="ticker",
                    status=str(event.get("closed") and "closed" or "open"),
                    exchange_timestamp=_parse_timestamp(
                        matched_market.get("updatedAt") or event.get("updatedAt")
                    ),
                    received_at=_now(),
                    sequence_id=str(matched_market.get("id") or market.market_id),
                    last_price=yes_price,
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    no_bid=no_bid,
                    no_ask=no_ask,
                    bid_size=_to_float(bids[0]["size"]),
                    ask_size=_to_float(asks[0]["size"]),
                    trade_size=0.0,
                    volume=_to_float(matched_market.get("volumeNum") or matched_market.get("volume")),
                    open_interest=_to_float(event.get("openInterest")),
                    bids=bids,
                    asks=asks,
                    payload={"event": event, "market": matched_market},
                )
            )
        return ticks

    def _collect_kalshi_ticks(self, *, limit: int, active_pairs_only: bool = False) -> list[NormalizedTick]:
        ticks: list[NormalizedTick] = []
        markets = self._market_selection(
            venue="kalshi",
            limit=limit,
            active_pairs_only=active_pairs_only,
        )
        for market in markets:
            try:
                payload = self.kalshi_client.get_market(market.market_id)
            except Exception as exc:
                logger.debug("Kalshi market refresh failed for %s: %s", market.market_id, exc)
                continue
            if not payload:
                continue
            try:
                orderbook = self.kalshi_client.get_orderbook(market.market_id, depth=10)
            except Exception:
                orderbook = {}
            raw_yes_bid = _to_float(payload.get("yes_bid"))
            raw_yes_ask = _to_float(payload.get("yes_ask"))
            raw_last_price = _to_float(payload.get("last_price"))
            yes_bid = _clip_probability(raw_yes_bid / 100.0 if raw_yes_bid > 1 else raw_yes_bid)
            yes_ask = _clip_probability(raw_yes_ask / 100.0 if raw_yes_ask > 1 else raw_yes_ask)
            last_price = _clip_probability(raw_last_price / 100.0 if raw_last_price > 1 else raw_last_price)
            if not last_price:
                last_price = _clip_probability((yes_bid + yes_ask) / 2) if yes_bid and yes_ask else yes_bid or yes_ask
            bids = _normalize_levels(orderbook.get("bids") or payload.get("bids"))
            asks = _normalize_levels(orderbook.get("asks") or payload.get("asks"))
            bid_size = _sum_depth(bids) or _to_float(payload.get("yes_bid_volume"))
            ask_size = _sum_depth(asks) or _to_float(payload.get("yes_ask_volume"))
            ticks.append(
                NormalizedTick(
                    venue="kalshi",
                    market_id=market.market_id,
                    market_slug=market.market_slug,
                    event_type="ticker",
                    status=str(payload.get("status") or market.status),
                    exchange_timestamp=_parse_timestamp(
                        payload.get("updated_time")
                        or payload.get("last_updated_time")
                        or payload.get("close_time")
                    ),
                    received_at=_now(),
                    sequence_id=str(payload.get("ticker") or market.market_id),
                    last_price=last_price,
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    no_bid=_clip_probability(1.0 - yes_ask),
                    no_ask=_clip_probability(1.0 - yes_bid),
                    bid_size=bid_size,
                    ask_size=ask_size,
                    trade_size=_to_float(payload.get("last_size")),
                    volume=_to_float(payload.get("volume")),
                    open_interest=_to_float(payload.get("open_interest")),
                    bids=bids,
                    asks=asks,
                    payload={"market": payload, "orderbook": orderbook},
                )
            )
        return ticks

    def _market_selection(
        self,
        *,
        venue: str,
        limit: int,
        active_pairs_only: bool,
    ) -> list[CrossVenueMarketMap]:
        paired_ids: list[int] = []
        for pair in (
            LeadLagPair.objects.filter(is_active=True)
            .select_related("leader_market", "follower_market")
            .order_by("-is_trade_eligible", "-composite_score", "-updated_at")
        ):
            market = pair.leader_market if pair.leader_market.venue == venue else pair.follower_market
            if market and market.id not in paired_ids:
                paired_ids.append(market.id)
            if len(paired_ids) >= limit:
                break

        if active_pairs_only:
            if not paired_ids:
                return []
            ordering = {market_id: index for index, market_id in enumerate(paired_ids)}
            markets = list(
                CrossVenueMarketMap.objects.filter(id__in=paired_ids, is_active=True, venue=venue)
            )
            markets.sort(key=lambda market: ordering.get(market.id, limit))
            return markets[:limit]

        markets = list(
            CrossVenueMarketMap.objects.filter(venue=venue, is_active=True)
        )
        markets.sort(
            key=lambda market: (
                0 if market.id in paired_ids else 1,
                -self._market_priority(market),
                str(market.title or "").lower(),
            )
        )
        return markets[:limit]

    def _market_priority(self, market: CrossVenueMarketMap) -> float:
        metadata = market.metadata or {}
        return (
            _to_float(metadata.get("liquidity"))
            + _to_float(metadata.get("open_interest"))
            + _to_float(metadata.get("volume"))
        )

    def _persist_ticks(self, ticks: list[NormalizedTick], *, persist: bool) -> dict:
        if not persist:
            return {"ticks": len(ticks), "orderbooks": sum(1 for tick in ticks if tick.bids or tick.asks)}

        results = Counter()
        for tick in ticks:
            if not tick.venue or not tick.market_id:
                continue
            market_map = CrossVenueMarketMap.objects.filter(
                venue=tick.venue,
                market_id=tick.market_id,
            ).first()
            duplicate = self._find_duplicate_tick(tick)
            if duplicate is not None:
                results["duplicates_skipped"] += 1
                if (tick.bids or tick.asks) and not duplicate.orderbook_snapshots.exists():
                    OrderBookLevelSnapshot.objects.create(
                        venue=tick.venue,
                        market_map=market_map,
                        tick=duplicate,
                        market_id=tick.market_id,
                        captured_at=tick.exchange_timestamp,
                        best_yes_bid=tick.yes_bid,
                        best_yes_ask=tick.yes_ask,
                        best_no_bid=tick.no_bid,
                        best_no_ask=tick.no_ask,
                        total_bid_depth=_sum_depth(tick.bids) or tick.bid_size,
                        total_ask_depth=_sum_depth(tick.asks) or tick.ask_size,
                        bids=tick.bids,
                        asks=tick.asks,
                        payload=tick.payload,
                    )
                    results["orderbooks"] += 1
                continue
            record = MarketEventTick.objects.create(
                venue=tick.venue,
                market_map=market_map,
                market_id=tick.market_id,
                market_slug=tick.market_slug,
                event_type=tick.event_type,
                status=tick.status,
                exchange_timestamp=tick.exchange_timestamp,
                received_at=tick.received_at,
                sequence_id=tick.sequence_id,
                last_price=tick.last_price,
                yes_bid=tick.yes_bid,
                yes_ask=tick.yes_ask,
                no_bid=tick.no_bid,
                no_ask=tick.no_ask,
                bid_size=tick.bid_size,
                ask_size=tick.ask_size,
                trade_size=tick.trade_size,
                volume=tick.volume,
                open_interest=tick.open_interest,
                payload=tick.payload,
            )
            results["ticks"] += 1
            if tick.bids or tick.asks:
                OrderBookLevelSnapshot.objects.create(
                    venue=tick.venue,
                    market_map=market_map,
                    tick=record,
                    market_id=tick.market_id,
                    captured_at=tick.exchange_timestamp,
                    best_yes_bid=tick.yes_bid,
                    best_yes_ask=tick.yes_ask,
                    best_no_bid=tick.no_bid,
                    best_no_ask=tick.no_ask,
                    total_bid_depth=_sum_depth(tick.bids) or tick.bid_size,
                    total_ask_depth=_sum_depth(tick.asks) or tick.ask_size,
                    bids=tick.bids,
                    asks=tick.asks,
                    payload=tick.payload,
                )
                results["orderbooks"] += 1
        return dict(results)

    def _find_duplicate_tick(self, tick: NormalizedTick) -> MarketEventTick | None:
        queryset = MarketEventTick.objects.filter(
            venue=tick.venue,
            market_id=tick.market_id,
            event_type=tick.event_type,
            exchange_timestamp=tick.exchange_timestamp,
        )
        sequence_id = str(tick.sequence_id or "").strip()
        if sequence_id:
            duplicate = queryset.filter(sequence_id=sequence_id).first()
            if duplicate is not None:
                return duplicate
        return queryset.filter(
            last_price=tick.last_price,
            yes_bid=tick.yes_bid,
            yes_ask=tick.yes_ask,
            bid_size=tick.bid_size,
            ask_size=tick.ask_size,
            volume=tick.volume,
        ).first()


class LeadLagSignalService:
    def scan(
        self,
        *,
        persist: bool = True,
        pair_limit: int | None = None,
    ) -> dict[str, int]:
        results = Counter()
        pairs = LeadLagPair.objects.filter(is_active=True, is_trade_eligible=True).select_related(
            "leader_market",
            "follower_market",
        )
        if pair_limit:
            pairs = pairs[:pair_limit]
        for pair in pairs:
            payload = self._evaluate_pair(pair)
            if payload is None:
                continue
            results["evaluated_pairs"] += 1
            if persist:
                existing = LeadLagSignal.objects.filter(
                    pair=pair,
                    leader_tick_id=payload["leader_tick"].id if payload["leader_tick"] else None,
                    follower_tick_id=payload["follower_tick"].id if payload["follower_tick"] else None,
                ).first()
                if existing:
                    continue
                LeadLagSignal.objects.create(
                    pair=pair,
                    leader_tick=payload["leader_tick"],
                    follower_tick=payload["follower_tick"],
                    status=payload["status"],
                    signal_direction=payload["signal_direction"],
                    leader_price_move=payload["leader_price_move"],
                    follower_gap=payload["follower_gap"],
                    expected_edge=payload["expected_edge"],
                    cost_estimate=payload["cost_estimate"],
                    latency_ms=payload["latency_ms"],
                    liquidity_score=payload["liquidity_score"],
                    rationale=payload["rationale"],
                    no_trade_reason=payload["no_trade_reason"],
                    metadata=payload["metadata"],
                )
            results[payload["status"]] += 1
        if persist:
            LeadLagMonitorService.invalidate_cache()
            from .market_intelligence import BenchmarkSummaryService

            BenchmarkSummaryService.invalidate_cached_summary()
        return dict(results)

    def _evaluate_pair(self, pair: LeadLagPair) -> dict | None:
        leader_ticks = list(
            MarketEventTick.objects.filter(
                venue=pair.leader_market.venue,
                market_id=pair.leader_market.market_id,
                last_price__gt=0,
            )
            .order_by("-exchange_timestamp")[:8]
        )
        follower_ticks = list(
            MarketEventTick.objects.filter(
                venue=pair.follower_market.venue,
                market_id=pair.follower_market.market_id,
                last_price__gt=0,
            )
            .order_by("-exchange_timestamp")[:8]
        )
        if len(leader_ticks) < 3 or len(follower_ticks) < 2:
            return None

        leader_latest = leader_ticks[0]
        leader_prev = leader_ticks[1]
        follower_latest = follower_ticks[0]
        follower_prev = follower_ticks[1]

        leader_move = leader_latest.last_price - leader_prev.last_price
        follower_move = follower_latest.last_price - follower_prev.last_price
        prior_leader_changes = [
            abs(leader_ticks[index].last_price - leader_ticks[index + 1].last_price)
            for index in range(1, len(leader_ticks) - 1)
        ]
        leader_noise = (
            sum(prior_leader_changes) / len(prior_leader_changes)
            if prior_leader_changes
            else abs(leader_prev.last_price - leader_ticks[2].last_price)
            if len(leader_ticks) > 2
            else 0.0
        )
        shock_threshold = max(0.025, leader_noise * 2.2)
        latency_ms = max(
            int((follower_latest.exchange_timestamp - leader_latest.exchange_timestamp).total_seconds() * 1000),
            0,
        )
        book_depth = self._book_depth(follower_latest)
        liquidity_score = min(book_depth / 1000.0, 1.0) if book_depth else 0.0
        spread = self._spread(follower_latest)
        cost_estimate = round(spread + 0.01 + max(0.0, 0.08 - liquidity_score * 0.05), 4)
        follower_gap = leader_move - follower_move

        signal_direction = "buy_yes" if follower_gap > 0 else "buy_no"
        status = "candidate"
        no_trade_reason = ""
        rationale = (
            f"{pair.leader_market.title} moved {leader_move:+.3f} while "
            f"{pair.follower_market.title} moved {follower_move:+.3f}."
        )

        if abs(leader_move) < shock_threshold:
            status = "no_trade"
            no_trade_reason = "leader_move_below_threshold"
        elif abs(follower_move) >= abs(leader_move) * 0.8 and math.copysign(1, follower_move or 1) == math.copysign(1, leader_move or 1):
            status = "no_trade"
            no_trade_reason = "follower_already_repriced"
        elif liquidity_score < 0.08:
            status = "no_trade"
            no_trade_reason = "insufficient_liquidity"
        elif pair.causal_score < 0.5 and pair.pair_type == "narrative_spillover":
            status = "no_trade"
            no_trade_reason = "thematic_link_only"

        expected_edge = round(abs(follower_gap) - cost_estimate - min(latency_ms / 1000.0, 5) * 0.002, 4)
        if expected_edge <= 0 and status == "candidate":
            status = "no_trade"
            no_trade_reason = "edge_below_cost"

        return {
            "leader_tick": leader_latest,
            "follower_tick": follower_latest,
            "status": status,
            "signal_direction": signal_direction,
            "leader_price_move": round(leader_move, 4),
            "follower_gap": round(follower_gap, 4),
            "expected_edge": expected_edge,
            "cost_estimate": cost_estimate,
            "latency_ms": latency_ms,
            "liquidity_score": round(liquidity_score, 4),
            "rationale": rationale,
            "no_trade_reason": no_trade_reason,
            "metadata": {
                "shock_threshold": round(shock_threshold, 4),
                "leader_noise": round(leader_noise, 4),
                "spread": round(spread, 4),
            },
        }

    def _book_depth(self, tick: MarketEventTick) -> float:
        book = OrderBookLevelSnapshot.objects.filter(tick=tick).first()
        if book:
            return book.total_bid_depth + book.total_ask_depth
        return tick.bid_size + tick.ask_size

    def _spread(self, tick: MarketEventTick) -> float:
        if tick.yes_bid and tick.yes_ask and tick.yes_ask >= tick.yes_bid:
            return tick.yes_ask - tick.yes_bid
        return 0.03


class PaperTradingService:
    def run(
        self,
        *,
        persist: bool = True,
        horizon_seconds: int | None = None,
    ) -> dict[str, float]:
        results = Counter()
        horizon = horizon_seconds or settings.CHAOSWING_LEADLAG_TRADE_HORIZON_SECONDS
        candidate_signals = list(
            LeadLagSignal.objects.filter(status="candidate")
            .select_related("pair", "pair__leader_market", "pair__follower_market", "follower_tick")
            .order_by("-created_at")[:50]
        )
        for signal in candidate_signals:
            if signal.paper_trades.exists():
                continue
            entry_price = self._entry_price(signal)
            opened_at = (
                signal.follower_tick.exchange_timestamp
                if signal.follower_tick
                else signal.leader_tick.exchange_timestamp
                if signal.leader_tick
                else signal.created_at
            )
            trade = PaperTrade(
                signal=signal,
                status="open",
                side=signal.signal_direction,
                quantity=1.0,
                entry_price=entry_price,
                fee_paid=round(signal.cost_estimate * 0.35, 4),
                slippage_paid=round(signal.cost_estimate * 0.65, 4),
                opened_at=opened_at,
                metadata={"expected_edge": signal.expected_edge},
            )
            if persist:
                trade.save()
            results["opened_trades"] += 1

        open_trades = list(
            PaperTrade.objects.filter(status="open")
            .select_related(
                "signal",
                "signal__pair",
                "signal__pair__follower_market",
                "signal__follower_tick",
            )
        )
        for trade in open_trades:
            close_result = self._close_trade_if_ready(trade, horizon_seconds=horizon, persist=persist)
            if not close_result:
                continue
            results["closed_trades"] += 1
            results["gross_pnl"] += close_result["gross_pnl"]
            results["net_pnl"] += close_result["net_pnl"]
            results["max_adverse_excursion"] += close_result["max_adverse_excursion"]
        if persist:
            LeadLagMonitorService.invalidate_cache()
            from .market_intelligence import BenchmarkSummaryService

            BenchmarkSummaryService.invalidate_cached_summary()
        return {
            "opened_trades": results.get("opened_trades", 0),
            "closed_trades": results.get("closed_trades", 0),
            "gross_pnl": round(results.get("gross_pnl", 0.0), 4),
            "net_pnl": round(results.get("net_pnl", 0.0), 4),
            "avg_adverse_excursion": round(
                results.get("max_adverse_excursion", 0.0) / max(results.get("closed_trades", 0), 1),
                4,
            ) if results.get("closed_trades", 0) else 0.0,
        }

    def _entry_price(self, signal: LeadLagSignal) -> float:
        tick = signal.follower_tick
        if not tick:
            return 0.0
        if signal.signal_direction == "buy_yes":
            return tick.yes_ask or tick.last_price
        return tick.no_ask or (1.0 - tick.last_price)

    def _close_trade_if_ready(self, trade: PaperTrade, *, horizon_seconds: int, persist: bool) -> dict | None:
        latest_tick = self._latest_follower_tick(trade)
        if latest_tick is None:
            return None
        if latest_tick.exchange_timestamp < trade.opened_at + timedelta(seconds=horizon_seconds):
            return None

        exit_price = latest_tick.yes_bid if trade.side == "buy_yes" else latest_tick.no_bid or (1.0 - latest_tick.last_price)
        gross_pnl = exit_price - trade.entry_price
        net_pnl = gross_pnl - trade.fee_paid - trade.slippage_paid
        adverse = abs((latest_tick.last_price or trade.entry_price) - trade.entry_price)

        if persist:
            trade.status = "closed"
            trade.exit_price = round(exit_price, 4)
            trade.gross_pnl = round(gross_pnl, 4)
            trade.net_pnl = round(net_pnl, 4)
            trade.max_adverse_excursion = round(adverse, 4)
            trade.time_to_exit_seconds = int((latest_tick.exchange_timestamp - trade.opened_at).total_seconds())
            trade.closed_at = latest_tick.exchange_timestamp
            trade.save(
                update_fields=[
                    "status",
                    "exit_price",
                    "gross_pnl",
                    "net_pnl",
                    "max_adverse_excursion",
                    "time_to_exit_seconds",
                    "closed_at",
                    "updated_at",
                ]
            )
        return {
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "max_adverse_excursion": adverse,
        }

    def _latest_follower_tick(self, trade: PaperTrade) -> MarketEventTick | None:
        pair = trade.signal.pair
        return (
            MarketEventTick.objects.filter(
                venue=pair.follower_market.venue,
                market_id=pair.follower_market.market_id,
                exchange_timestamp__gte=trade.opened_at,
            )
            .order_by("-exchange_timestamp")
            .first()
        )


class LeadLagBacktestService:
    def run(self, *, persist: bool = True) -> dict:
        LeadLagPairBuilderService().build(persist=True)
        LeadLagSignalService().scan(persist=True)
        trade_report = PaperTradingService().run(persist=True)

        trades = list(PaperTrade.objects.filter(status="closed").select_related("signal", "signal__pair"))
        signal_count = LeadLagSignal.objects.count()
        candidate_count = LeadLagSignal.objects.filter(status="candidate").count()
        closed_count = len(trades)
        hit_count = sum(1 for trade in trades if trade.net_pnl > 0)
        net_pnl = sum(trade.net_pnl for trade in trades)
        gross_pnl = sum(trade.gross_pnl for trade in trades)
        avg_edge = (
            sum(_to_float(trade.signal.expected_edge) for trade in trades) / closed_count
            if closed_count
            else 0.0
        )
        avg_latency_ms = (
            sum(trade.signal.latency_ms for trade in trades) / closed_count
            if closed_count
            else 0.0
        )
        avg_decay = (
            sum(trade.time_to_exit_seconds for trade in trades) / closed_count
            if closed_count
            else 0.0
        )
        avg_adverse = (
            sum(trade.max_adverse_excursion for trade in trades) / closed_count
            if closed_count
            else 0.0
        )
        net_return = net_pnl / closed_count if closed_count else 0.0
        pair_counter = Counter(trade.signal.pair.pair_type for trade in trades)

        metrics = {
            "signal_count": signal_count,
            "candidate_count": candidate_count,
            "closed_trades": closed_count,
            "gross_pnl": round(gross_pnl, 4),
            "net_pnl": round(net_pnl, 4),
            "hit_rate": round(hit_count / closed_count, 4) if closed_count else 0.0,
            "avg_edge": round(avg_edge, 4),
            "avg_latency_ms": round(avg_latency_ms, 2),
            "avg_time_to_decay_seconds": round(avg_decay, 2),
            "avg_adverse_excursion": round(avg_adverse, 4),
            "slippage_adjusted_return": round(net_return, 4),
            "precision_vs_no_trade": round(candidate_count / signal_count, 4) if signal_count else 0.0,
            "pair_type_breakdown": dict(pair_counter),
        }
        report = {
            "task_type": "leadlag_backtest",
            "title": "Cross-venue lead-lag paper-trading backtest",
            "dataset_version": (
                f"ticks:{MarketEventTick.objects.count()}|pairs:{LeadLagPair.objects.count()}"
            ),
            "metrics": metrics,
            "trade_report": trade_report,
        }
        if persist:
            ExperimentRun.objects.create(
                task_type="leadlag_backtest",
                title="Cross-venue lead-lag paper-trading backtest",
                dataset_version=report["dataset_version"],
                metrics=metrics,
                artifacts={
                    "recent_trade_ids": [trade.id for trade in trades[:20]],
                    "pair_type_breakdown": dict(pair_counter),
                },
                notes=(
                    "Paper-trading backtest over persisted lead-lag signals with cost-adjusted PnL. "
                    "This is a research alert layer, not a claim of production arbitrage."
                ),
            )
            LeadLagMonitorService.invalidate_cache()
            from .market_intelligence import BenchmarkSummaryService

            BenchmarkSummaryService.invalidate_cached_summary()
        return report


class LeadLagMonitorService:
    def build_cached(self, *, force_refresh: bool = False) -> dict:
        if force_refresh:
            self.invalidate_cache()

        cached = cache.get(LEADLAG_SUMMARY_CACHE_KEY)
        if cached is not None:
            return cached
        summary = self.build()
        cache.set(
            LEADLAG_SUMMARY_CACHE_KEY,
            summary,
            timeout=getattr(settings, "CHAOSWING_LEADLAG_CACHE_TTL", 60),
        )
        return summary

    def build(self) -> dict:
        pairs = list(
            LeadLagPair.objects.filter(is_active=True)
            .select_related("leader_market", "follower_market")
            .order_by("-composite_score")[:24]
        )
        live_coverage = LeadLagPairBuilderService().build(persist=False)
        signals = list(
            LeadLagSignal.objects.select_related(
                "pair",
                "pair__leader_market",
                "pair__follower_market",
            )
            .order_by("-created_at")[:20]
        )
        trades = list(
            PaperTrade.objects.select_related(
                "signal",
                "signal__pair",
                "signal__pair__leader_market",
                "signal__pair__follower_market",
            )
            .order_by("-opened_at")[:20]
        )
        experiments = list(
            ExperimentRun.objects.filter(task_type="leadlag_backtest").order_by("-created_at")[:3]
        )
        latest_backtest = experiments[0] if experiments else None
        mapped_markets = CrossVenueMarketMap.objects.count()
        tick_count = MarketEventTick.objects.count()
        open_trade_count = PaperTrade.objects.filter(status="open").count()
        net_pnl = sum(trade.net_pnl for trade in trades if trade.status == "closed")
        gross_pnl = sum(trade.gross_pnl for trade in trades if trade.status == "closed")

        return {
            "summary_cards": [
                {
                    "label": "Mapped markets",
                    "value": mapped_markets,
                    "copy": "Cross-venue market catalog rows that can be paired and scored.",
                },
                {
                    "label": "Persisted live ticks",
                    "value": tick_count,
                    "copy": "Raw venue updates captured for lead-lag research and paper trading.",
                },
                {
                    "label": "Trade-eligible pairs",
                    "value": LeadLagPair.objects.filter(is_active=True, is_trade_eligible=True).count(),
                    "copy": "Pairs that passed both semantic/causal screening and the current heuristic thresholds.",
                },
                {
                    "label": "Coverage status",
                    "value": live_coverage["coverage_status"].replace("_", " "),
                    "copy": live_coverage["coverage_summary"],
                },
                {
                    "label": "Net paper PnL",
                    "value": f"{net_pnl:+.3f}",
                    "copy": "Closed paper-trade PnL after estimated fees and slippage.",
                },
            ],
            "monitor_notes": [
                "Candidate spillover opportunity is not the same as arbitrage; net-of-cost paper trading is the benchmark.",
                "Signals are downgraded to no-trade when the follower already repriced or visible liquidity is too thin.",
                "Current v1 uses heuristic screening and persisted ticks; it should be treated as a research system first.",
                *live_coverage["coverage_notes"],
            ],
            "coverage_status": live_coverage["coverage_status"],
            "coverage_label": live_coverage["coverage_status"].replace("_", " ").title(),
            "coverage_summary": live_coverage["coverage_summary"],
            "coverage_notes": live_coverage["coverage_notes"],
            "shared_topics": live_coverage["shared_topics"],
            "recent_signals": [
                {
                    "id": signal.id,
                    "pair_id": signal.pair_id,
                    "pair_title": _pair_title(signal.pair),
                    "pair_type": signal.pair.pair_type,
                    "status": signal.status,
                    "signal_direction": signal.signal_direction,
                    "leader_price_move": signal.leader_price_move,
                    "follower_gap": signal.follower_gap,
                    "expected_edge": signal.expected_edge,
                    "cost_estimate": signal.cost_estimate,
                    "latency_ms": signal.latency_ms,
                    "liquidity_score": signal.liquidity_score,
                    "rationale": signal.rationale,
                    "no_trade_reason": signal.no_trade_reason,
                    "created_at": signal.created_at.isoformat(),
                }
                for signal in signals
            ],
            "pair_diagnostics": [
                {
                    "id": pair.id,
                    "title": _pair_title(pair),
                    "pair_type": pair.pair_type,
                    "leader_market": pair.leader_market.title,
                    "follower_market": pair.follower_market.title,
                    "semantic_score": pair.semantic_score,
                    "causal_score": pair.causal_score,
                    "resolution_score": pair.resolution_score,
                    "stability_score": pair.stability_score,
                    "composite_score": pair.composite_score,
                    "expected_latency_seconds": pair.expected_latency_seconds,
                    "is_trade_eligible": pair.is_trade_eligible,
                    "direction_reason": pair.direction_reason,
                    "stability_samples": (pair.metadata or {}).get("stability_samples", 0),
                    "move_samples": (pair.metadata or {}).get("move_samples", 0),
                    "leader_first_ratio": (pair.metadata or {}).get("leader_first_ratio", 0.0),
                    "avg_lead_seconds": (pair.metadata or {}).get("avg_lead_seconds", 0.0),
                    "readiness_status": (pair.metadata or {}).get("readiness_status", "needs_history"),
                    "readiness_reason": (pair.metadata or {}).get("readiness_reason", ""),
                }
                for pair in pairs
            ]
            or live_coverage["top_candidates"],
            "top_candidates": live_coverage["top_candidates"],
            "paper_trades": [
                {
                    "id": trade.id,
                    "pair_title": _pair_title(trade.signal.pair),
                    "status": trade.status,
                    "side": trade.side,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "gross_pnl": trade.gross_pnl,
                    "net_pnl": trade.net_pnl,
                    "fee_paid": trade.fee_paid,
                    "slippage_paid": trade.slippage_paid,
                    "time_to_exit_seconds": trade.time_to_exit_seconds,
                    "opened_at": trade.opened_at.isoformat(),
                    "closed_at": trade.closed_at.isoformat() if trade.closed_at else "",
                }
                for trade in trades
            ],
            "latest_backtest": {
                "title": latest_backtest.title,
                "dataset_version": latest_backtest.dataset_version,
                "metrics": latest_backtest.metrics,
                "created_at": latest_backtest.created_at.isoformat(),
            }
            if latest_backtest
            else None,
            "methodology": {
                "commands": [
                    "python manage.py sync_crossvenue_market_map",
                    "python manage.py stream_live_ticks --duration-seconds 60 --iterations 0 --rebuild-pairs-every 1 --scan-signals-every 1 --run-paper-trader --transport hybrid --active-pairs-only",
                    "python manage.py build_leadlag_pairs",
                    "python manage.py run_leadlag_backtest",
                    "python manage.py run_paper_trader",
                ],
                "notes": [
                    "Use cross-venue lead-lag only as a research alert layer until net-of-cost performance is stable.",
                    "The current backtest is heuristic and should be treated as a falsification harness, not proof of deployable alpha.",
                ],
            },
            "totals": {
                "signals": LeadLagSignal.objects.count(),
                "candidate_signals": LeadLagSignal.objects.filter(status="candidate").count(),
                "no_trade_signals": LeadLagSignal.objects.filter(status="no_trade").count(),
                "open_trades": open_trade_count,
                "closed_trades": PaperTrade.objects.filter(status="closed").count(),
                "gross_pnl": round(gross_pnl, 4),
                "net_pnl": round(net_pnl, 4),
            },
        }

    @staticmethod
    def invalidate_cache() -> None:
        cache.delete(LEADLAG_SUMMARY_CACHE_KEY)
