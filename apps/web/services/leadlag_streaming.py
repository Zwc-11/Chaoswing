from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from django.conf import settings

from apps.web.models import CrossVenueMarketMap

from .leadlag import (
    LeadLagPairBuilderService,
    LeadLagSignalService,
    LeadLagTickCollectionService,
    NormalizedTick,
    PaperTradingService,
    _clip_probability,
    _normalize_levels,
    _now,
    _to_float,
)


logger = logging.getLogger("apps.web.services.leadlag_streaming")

try:
    import websockets
except ImportError:  # pragma: no cover - optional dependency at import time
    websockets = None

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
except ImportError:  # pragma: no cover - optional dependency at import time
    hashes = None
    serialization = None
    padding = None


def _parse_stream_timestamp(value) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    numeric = _to_float(value)
    if numeric > 10_000_000_000:
        return datetime.fromtimestamp(numeric / 1000.0, tz=UTC)
    if numeric > 1_000_000_000:
        return datetime.fromtimestamp(numeric, tz=UTC)
    text = str(value or "").strip()
    if not text:
        return _now()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return _now()


def _iter_messages(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _sorted_bids(levels: list[dict[str, float]]) -> list[dict[str, float]]:
    return sorted(levels, key=lambda item: item["price"], reverse=True)


def _sorted_asks(levels: list[dict[str, float]]) -> list[dict[str, float]]:
    return sorted(levels, key=lambda item: item["price"])


def _convert_no_levels_to_yes_bids(levels: list[dict[str, float]]) -> list[dict[str, float]]:
    converted = [
        {"price": _clip_probability(1.0 - _to_float(level.get("price"))), "size": _to_float(level.get("size"))}
        for level in levels
    ]
    return _sorted_bids([level for level in converted if level["price"] > 0 or level["size"] > 0])


def _convert_no_levels_to_yes_asks(levels: list[dict[str, float]]) -> list[dict[str, float]]:
    converted = [
        {"price": _clip_probability(1.0 - _to_float(level.get("price"))), "size": _to_float(level.get("size"))}
        for level in levels
    ]
    return _sorted_asks([level for level in converted if level["price"] > 0 or level["size"] > 0])


class KalshiWebSocketAuthSigner:
    def __init__(
        self,
        *,
        access_key_id: str | None = None,
        private_key_path: Path | None = None,
    ):
        self.access_key_id = str(access_key_id or settings.CHAOSWING_KALSHI_ACCESS_KEY_ID or "").strip()
        self.private_key_path = Path(private_key_path or settings.CHAOSWING_KALSHI_PRIVATE_KEY_PATH)

    def is_configured(self) -> bool:
        return bool(self.access_key_id and self.private_key_path and self.private_key_path.exists())

    def is_available(self) -> bool:
        return self.is_configured() and all(module is not None for module in (hashes, serialization, padding))

    def build_headers(self, *, ws_url: str) -> dict[str, str]:
        if not self.is_available():
            return {}
        path = urlparse(ws_url).path or "/trade-api/ws/v2"
        timestamp_ms = str(int(_now().timestamp() * 1000))
        message = f"{timestamp_ms}GET{path}".encode("utf-8")
        private_key = serialization.load_pem_private_key(
            self.private_key_path.read_bytes(),
            password=None,
        )
        signature = private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.access_key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }


@dataclass(slots=True)
class _PolymarketMarketState:
    market: CrossVenueMarketMap
    exchange_timestamp: datetime = field(default_factory=_now)
    status: str = "open"
    yes_bid: float = 0.0
    yes_ask: float = 0.0
    no_bid: float = 0.0
    no_ask: float = 0.0
    yes_bid_size: float = 0.0
    yes_ask_size: float = 0.0
    no_bid_size: float = 0.0
    no_ask_size: float = 0.0
    yes_last_price: float = 0.0
    no_last_price: float = 0.0
    trade_size: float = 0.0
    yes_bids: list[dict[str, float]] = field(default_factory=list)
    yes_asks: list[dict[str, float]] = field(default_factory=list)
    no_bids: list[dict[str, float]] = field(default_factory=list)
    no_asks: list[dict[str, float]] = field(default_factory=list)

    def set_side_snapshot(
        self,
        *,
        side: str,
        bids: list[dict[str, float]],
        asks: list[dict[str, float]],
        timestamp: datetime,
        last_price: float = 0.0,
        trade_size: float = 0.0,
    ) -> None:
        self.exchange_timestamp = timestamp
        self.trade_size = max(trade_size, self.trade_size)
        if side == "yes":
            self.yes_bids = _sorted_bids(bids)
            self.yes_asks = _sorted_asks(asks)
            self.yes_bid = self.yes_bids[0]["price"] if self.yes_bids else self.yes_bid
            self.yes_ask = self.yes_asks[0]["price"] if self.yes_asks else self.yes_ask
            self.yes_bid_size = self.yes_bids[0]["size"] if self.yes_bids else self.yes_bid_size
            self.yes_ask_size = self.yes_asks[0]["size"] if self.yes_asks else self.yes_ask_size
            if last_price > 0:
                self.yes_last_price = _clip_probability(last_price)
        else:
            self.no_bids = _sorted_bids(bids)
            self.no_asks = _sorted_asks(asks)
            self.no_bid = self.no_bids[0]["price"] if self.no_bids else self.no_bid
            self.no_ask = self.no_asks[0]["price"] if self.no_asks else self.no_ask
            self.no_bid_size = self.no_bids[0]["size"] if self.no_bids else self.no_bid_size
            self.no_ask_size = self.no_asks[0]["size"] if self.no_asks else self.no_ask_size
            if last_price > 0:
                self.no_last_price = _clip_probability(last_price)

    def update_side_top(
        self,
        *,
        side: str,
        best_bid: float,
        best_ask: float,
        timestamp: datetime,
        bid_size: float = 0.0,
        ask_size: float = 0.0,
        last_price: float = 0.0,
        trade_size: float = 0.0,
    ) -> None:
        self.exchange_timestamp = timestamp
        if trade_size > 0:
            self.trade_size = trade_size
        if side == "yes":
            if best_bid > 0:
                self.yes_bid = _clip_probability(best_bid)
            if best_ask > 0:
                self.yes_ask = _clip_probability(best_ask)
            if bid_size > 0:
                self.yes_bid_size = bid_size
            if ask_size > 0:
                self.yes_ask_size = ask_size
            if last_price > 0:
                self.yes_last_price = _clip_probability(last_price)
        else:
            if best_bid > 0:
                self.no_bid = _clip_probability(best_bid)
            if best_ask > 0:
                self.no_ask = _clip_probability(best_ask)
            if bid_size > 0:
                self.no_bid_size = bid_size
            if ask_size > 0:
                self.no_ask_size = ask_size
            if last_price > 0:
                self.no_last_price = _clip_probability(last_price)

    def mark_resolved(self, *, winning_side: str, timestamp: datetime) -> None:
        self.exchange_timestamp = timestamp
        self.status = "resolved"
        if winning_side == "yes":
            self.yes_last_price = 1.0
            self.no_last_price = 0.0
            self.yes_bid = 1.0
            self.yes_ask = 1.0
            self.no_bid = 0.0
            self.no_ask = 0.0
        else:
            self.yes_last_price = 0.0
            self.no_last_price = 1.0
            self.yes_bid = 0.0
            self.yes_ask = 0.0
            self.no_bid = 1.0
            self.no_ask = 1.0

    def to_tick(self, *, payload: dict[str, Any], event_type: str, sequence_id: str) -> NormalizedTick:
        yes_bid = self.yes_bid
        if not yes_bid and self.no_ask:
            yes_bid = _clip_probability(1.0 - self.no_ask)
        yes_ask = self.yes_ask
        if not yes_ask and self.no_bid:
            yes_ask = _clip_probability(1.0 - self.no_bid)
        no_bid = self.no_bid
        if not no_bid and yes_ask:
            no_bid = _clip_probability(1.0 - yes_ask)
        no_ask = self.no_ask
        if not no_ask and yes_bid:
            no_ask = _clip_probability(1.0 - yes_bid)
        yes_bids = self.yes_bids or _convert_no_levels_to_yes_bids(self.no_asks)
        yes_asks = self.yes_asks or _convert_no_levels_to_yes_asks(self.no_bids)
        bid_size = self.yes_bid_size or self.no_ask_size
        ask_size = self.yes_ask_size or self.no_bid_size
        last_price = self.yes_last_price
        if not last_price and self.no_last_price > 0:
            last_price = _clip_probability(1.0 - self.no_last_price)
        if not last_price and yes_bid and yes_ask:
            last_price = _clip_probability((yes_bid + yes_ask) / 2.0)
        metadata = self.market.metadata or {}
        return NormalizedTick(
            venue="polymarket",
            market_id=self.market.market_id,
            market_slug=self.market.market_slug,
            event_type=event_type,
            status=self.status,
            exchange_timestamp=self.exchange_timestamp,
            received_at=_now(),
            sequence_id=sequence_id,
            last_price=last_price,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            bid_size=bid_size,
            ask_size=ask_size,
            trade_size=self.trade_size,
            volume=_to_float(metadata.get("volume")),
            open_interest=_to_float(metadata.get("liquidity") or metadata.get("open_interest")),
            bids=yes_bids,
            asks=yes_asks,
            payload=payload,
        )


class LeadLagStreamingCollectionService:
    def __init__(
        self,
        *,
        tick_service: LeadLagTickCollectionService | None = None,
        kalshi_signer: KalshiWebSocketAuthSigner | None = None,
    ):
        self.tick_service = tick_service or LeadLagTickCollectionService()
        self.kalshi_signer = kalshi_signer or KalshiWebSocketAuthSigner()

    def stream(
        self,
        *,
        venues: list[str] | None = None,
        market_limit: int = 10,
        duration_seconds: int = 30,
        active_pairs_only: bool = True,
        transport: str = "hybrid",
        poll_seconds: int | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        return asyncio.run(
            self._stream(
                venues=venues,
                market_limit=market_limit,
                duration_seconds=duration_seconds,
                active_pairs_only=active_pairs_only,
                transport=transport,
                poll_seconds=poll_seconds,
                persist=persist,
            )
        )

    def iter_supervised_stream(
        self,
        *,
        venues: list[str] | None = None,
        market_limit: int = 10,
        duration_seconds: int = 30,
        active_pairs_only: bool = True,
        transport: str = "hybrid",
        poll_seconds: int | None = None,
        persist: bool = True,
        iterations: int = 1,
        reconnect_seconds: int = 5,
        rebuild_pairs_every: int = 0,
        scan_signals_every: int = 0,
        run_paper_trader: bool = False,
        pair_limit: int | None = None,
        horizon_seconds: int | None = None,
    ):
        cycle = 0
        while iterations <= 0 or cycle < iterations:
            cycle += 1
            try:
                stream_report = self.stream(
                    venues=venues,
                    market_limit=market_limit,
                    duration_seconds=duration_seconds,
                    active_pairs_only=active_pairs_only,
                    transport=transport,
                    poll_seconds=poll_seconds,
                    persist=persist,
                )
                refresh_report = self._refresh_research_state(
                    cycle=cycle,
                    persist=persist,
                    rebuild_pairs_every=rebuild_pairs_every,
                    scan_signals_every=scan_signals_every,
                    run_paper_trader=run_paper_trader,
                    pair_limit=pair_limit,
                    horizon_seconds=horizon_seconds,
                )
                yield {
                    "cycle": cycle,
                    "status": "ok",
                    "stream": stream_report,
                    "refresh": refresh_report,
                }
            except Exception as exc:
                logger.warning("Lead-lag streaming cycle %s failed: %s", cycle, exc)
                yield {
                    "cycle": cycle,
                    "status": "error",
                    "error": str(exc),
                    "stream": {},
                    "refresh": {},
                }
            if iterations > 0 and cycle >= iterations:
                break
            if reconnect_seconds > 0:
                time.sleep(reconnect_seconds)

    def _refresh_research_state(
        self,
        *,
        cycle: int,
        persist: bool,
        rebuild_pairs_every: int,
        scan_signals_every: int,
        run_paper_trader: bool,
        pair_limit: int | None,
        horizon_seconds: int | None,
    ) -> dict[str, Any]:
        if not persist:
            return {}
        refresh: dict[str, Any] = {}
        if rebuild_pairs_every > 0 and cycle % rebuild_pairs_every == 0:
            refresh["pairs"] = LeadLagPairBuilderService().build(persist=True)
        if scan_signals_every > 0 and cycle % scan_signals_every == 0:
            refresh["signals"] = LeadLagSignalService().scan(
                persist=True,
                pair_limit=pair_limit,
            )
            if run_paper_trader:
                refresh["paper_trades"] = PaperTradingService().run(
                    persist=True,
                    horizon_seconds=horizon_seconds,
                )
        return refresh

    async def _stream(
        self,
        *,
        venues: list[str] | None,
        market_limit: int,
        duration_seconds: int,
        active_pairs_only: bool,
        transport: str,
        poll_seconds: int | None,
        persist: bool,
    ) -> dict[str, Any]:
        venue_list = [venue for venue in (venues or ["polymarket", "kalshi"]) if venue]
        deadline = asyncio.get_running_loop().time() + max(int(duration_seconds), 1)
        results: Counter = Counter()
        venue_modes: dict[str, str] = {}
        tasks: list[asyncio.Task[dict[str, Any]]] = []

        if "polymarket" in venue_list:
            if transport in {"hybrid", "websocket"}:
                venue_modes["polymarket"] = "websocket"
                tasks.append(
                    asyncio.create_task(
                        self._stream_polymarket(
                            deadline=deadline,
                            market_limit=market_limit,
                            active_pairs_only=active_pairs_only,
                            persist=persist,
                        )
                    )
                )
            else:
                venue_modes["polymarket"] = "poll"
                tasks.append(
                    asyncio.create_task(
                        self._poll_loop(
                            venue="polymarket",
                            deadline=deadline,
                            market_limit=market_limit,
                            active_pairs_only=active_pairs_only,
                            persist=persist,
                            poll_seconds=poll_seconds,
                        )
                    )
                )

        if "kalshi" in venue_list:
            if transport == "poll":
                venue_modes["kalshi"] = "poll"
                tasks.append(
                    asyncio.create_task(
                        self._poll_loop(
                            venue="kalshi",
                            deadline=deadline,
                            market_limit=market_limit,
                            active_pairs_only=active_pairs_only,
                            persist=persist,
                            poll_seconds=poll_seconds,
                        )
                    )
                )
            elif self.kalshi_signer.is_available():
                venue_modes["kalshi"] = "websocket"
                tasks.append(
                    asyncio.create_task(
                        self._stream_kalshi(
                            deadline=deadline,
                            market_limit=market_limit,
                            active_pairs_only=active_pairs_only,
                            persist=persist,
                            poll_seconds=poll_seconds,
                            allow_poll_fallback=(transport == "hybrid"),
                        )
                    )
                )
            else:
                venue_modes["kalshi"] = "poll_fallback"
                tasks.append(
                    asyncio.create_task(
                        self._poll_loop(
                            venue="kalshi",
                            deadline=deadline,
                            market_limit=market_limit,
                            active_pairs_only=active_pairs_only,
                            persist=persist,
                            poll_seconds=poll_seconds,
                        )
                    )
                )
                results["kalshi_ws_unavailable"] += 1

        for report in await asyncio.gather(*tasks):
            results.update(report)

        results["duration_seconds"] = max(int(duration_seconds), 1)
        return {
            **dict(results),
            "transport": transport,
            "venue_modes": venue_modes,
            "kalshi_ws_auth_configured": self.kalshi_signer.is_available(),
        }

    async def _poll_loop(
        self,
        *,
        venue: str,
        deadline: float,
        market_limit: int,
        active_pairs_only: bool,
        persist: bool,
        poll_seconds: int | None,
    ) -> dict[str, Any]:
        pause = settings.CHAOSWING_LEADLAG_POLL_SECONDS if poll_seconds is None else max(int(poll_seconds), 0)
        results: Counter = Counter()
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            if venue == "polymarket":
                ticks = await asyncio.to_thread(
                    self.tick_service._collect_polymarket_ticks,
                    limit=market_limit,
                    active_pairs_only=active_pairs_only,
                )
            else:
                ticks = await asyncio.to_thread(
                    self.tick_service._collect_kalshi_ticks,
                    limit=market_limit,
                    active_pairs_only=active_pairs_only,
                )
            batch = await asyncio.to_thread(self.tick_service._persist_ticks, ticks, persist=persist)
            results.update(batch)
            results[f"{venue}_poll_cycles"] += 1
            if pause <= 0:
                break
            await asyncio.sleep(min(pause, max(remaining, 0)))
        return dict(results)

    async def _stream_polymarket(
        self,
        *,
        deadline: float,
        market_limit: int,
        active_pairs_only: bool,
        persist: bool,
    ) -> dict[str, Any]:
        results: Counter = Counter()
        markets = await asyncio.to_thread(
            self.tick_service._market_selection,
            venue="polymarket",
            limit=market_limit,
            active_pairs_only=active_pairs_only,
        )
        if not markets:
            results["polymarket_markets_selected"] = 0
            return dict(results)
        if websockets is None:
            logger.warning("websockets is not installed; falling back to poll collection for Polymarket.")
            return await self._poll_loop(
                venue="polymarket",
                deadline=deadline,
                market_limit=market_limit,
                active_pairs_only=active_pairs_only,
                persist=persist,
                poll_seconds=1,
            )

        asset_lookup = self._polymarket_asset_lookup(markets)
        if not asset_lookup:
            results["polymarket_markets_selected"] = len(markets)
            results["polymarket_asset_subscriptions"] = 0
            return dict(results)

        url = settings.CHAOSWING_POLYMARKET_WS_URL
        subscribe_payload = {
            "assets_ids": sorted(asset_lookup.keys()),
            "type": "market",
        }
        if "clob.polymarket.com" in url:
            subscribe_payload["auth"] = None

        states: dict[str, _PolymarketMarketState] = {}
        results["polymarket_markets_selected"] = len(markets)
        results["polymarket_asset_subscriptions"] = len(asset_lookup)
        timeout_seconds = max(2.0, min(settings.CHAOSWING_HTTP_TIMEOUT_SECONDS, 15.0))

        try:
            async with websockets.connect(  # type: ignore[attr-defined]
                url,
                open_timeout=timeout_seconds,
                close_timeout=timeout_seconds,
                ping_interval=20,
                ping_timeout=timeout_seconds,
                max_size=None,
            ) as socket:
                await socket.send(json.dumps(subscribe_payload))
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        raw_message = await asyncio.wait_for(socket.recv(), timeout=min(remaining, 5.0))
                    except TimeoutError:
                        continue
                    decoded = self._decode_message(raw_message)
                    ticks = self._normalize_polymarket_payload(
                        decoded,
                        asset_lookup=asset_lookup,
                        states=states,
                    )
                    if not ticks:
                        continue
                    batch = await asyncio.to_thread(self.tick_service._persist_ticks, ticks, persist=persist)
                    results.update(batch)
                    results["polymarket_ws_messages"] += len(ticks)
        except Exception as exc:
            logger.warning("Polymarket websocket stream failed: %s", exc)
            results["polymarket_ws_failures"] += 1
            results["polymarket_ws_fallback_to_poll"] += 1
            fallback = await self._poll_loop(
                venue="polymarket",
                deadline=deadline,
                market_limit=market_limit,
                active_pairs_only=active_pairs_only,
                persist=persist,
                poll_seconds=1,
            )
            results.update(fallback)
        return dict(results)

    async def _stream_kalshi(
        self,
        *,
        deadline: float,
        market_limit: int,
        active_pairs_only: bool,
        persist: bool,
        poll_seconds: int | None,
        allow_poll_fallback: bool,
    ) -> dict[str, Any]:
        results: Counter = Counter()
        markets = await asyncio.to_thread(
            self.tick_service._market_selection,
            venue="kalshi",
            limit=market_limit,
            active_pairs_only=active_pairs_only,
        )
        if not markets:
            results["kalshi_markets_selected"] = 0
            return dict(results)
        if websockets is None:
            logger.warning("websockets is not installed; falling back to poll collection for Kalshi.")
            if allow_poll_fallback:
                return await self._poll_loop(
                    venue="kalshi",
                    deadline=deadline,
                    market_limit=market_limit,
                    active_pairs_only=active_pairs_only,
                    persist=persist,
                    poll_seconds=poll_seconds,
                )
            results["kalshi_ws_failures"] += 1
            return dict(results)

        headers = self.kalshi_signer.build_headers(ws_url=settings.CHAOSWING_KALSHI_WS_URL)
        subscribe_payload = {
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker"],
                "market_tickers": [market.market_id for market in markets],
            },
        }
        market_lookup = {market.market_id: market for market in markets}
        results["kalshi_markets_selected"] = len(markets)
        timeout_seconds = max(2.0, min(settings.CHAOSWING_HTTP_TIMEOUT_SECONDS, 15.0))

        try:
            async with websockets.connect(  # type: ignore[attr-defined]
                settings.CHAOSWING_KALSHI_WS_URL,
                additional_headers=headers or None,
                open_timeout=timeout_seconds,
                close_timeout=timeout_seconds,
                ping_interval=20,
                ping_timeout=timeout_seconds,
                max_size=None,
            ) as socket:
                await socket.send(json.dumps(subscribe_payload))
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        raw_message = await asyncio.wait_for(socket.recv(), timeout=min(remaining, 5.0))
                    except TimeoutError:
                        continue
                    decoded = self._decode_message(raw_message)
                    ticks = self._normalize_kalshi_payload(decoded, market_lookup=market_lookup)
                    if not ticks:
                        continue
                    batch = await asyncio.to_thread(self.tick_service._persist_ticks, ticks, persist=persist)
                    results.update(batch)
                    results["kalshi_ws_messages"] += len(ticks)
        except Exception as exc:
            logger.warning("Kalshi websocket stream failed: %s", exc)
            results["kalshi_ws_failures"] += 1
            if allow_poll_fallback:
                results["kalshi_ws_fallback_to_poll"] += 1
                fallback = await self._poll_loop(
                    venue="kalshi",
                    deadline=deadline,
                    market_limit=market_limit,
                    active_pairs_only=active_pairs_only,
                    persist=persist,
                    poll_seconds=poll_seconds,
                )
                results.update(fallback)
        return dict(results)

    def _decode_message(self, raw_message: Any) -> Any:
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8", errors="ignore")
        if isinstance(raw_message, str):
            text = raw_message.strip()
            if not text or text.upper() == "PONG":
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {}
        return raw_message

    def _polymarket_asset_lookup(
        self,
        markets: list[CrossVenueMarketMap],
    ) -> dict[str, tuple[CrossVenueMarketMap, str]]:
        lookup: dict[str, tuple[CrossVenueMarketMap, str]] = {}
        for market in markets:
            metadata = market.metadata or {}
            token_ids = metadata.get("clob_token_ids") or []
            outcomes = [str(item).strip().lower() for item in metadata.get("outcomes") or []]
            if not isinstance(token_ids, list) or len(token_ids) < 2:
                continue
            yes_index = 0
            no_index = 1
            for index, outcome in enumerate(outcomes[: len(token_ids)]):
                if outcome.startswith("yes"):
                    yes_index = index
                elif outcome.startswith("no"):
                    no_index = index
            yes_token = str(token_ids[yes_index]).strip()
            no_token = str(token_ids[no_index]).strip()
            if yes_token:
                lookup[yes_token] = (market, "yes")
            if no_token:
                lookup[no_token] = (market, "no")
        return lookup

    def _normalize_polymarket_payload(
        self,
        payload: Any,
        *,
        asset_lookup: dict[str, tuple[CrossVenueMarketMap, str]],
        states: dict[str, _PolymarketMarketState],
    ) -> list[NormalizedTick]:
        ticks: list[NormalizedTick] = []
        for message in _iter_messages(payload):
            event_type = str(message.get("event_type") or message.get("type") or "").strip().lower()
            if event_type == "price_change":
                for index, change in enumerate(message.get("price_changes") or []):
                    normalized = self._apply_polymarket_event(
                        change,
                        event_type=event_type,
                        parent_message=message,
                        asset_lookup=asset_lookup,
                        states=states,
                        sequence_suffix=str(index),
                    )
                    if normalized is not None:
                        ticks.append(normalized)
                continue
            normalized = self._apply_polymarket_event(
                message,
                event_type=event_type,
                parent_message=message,
                asset_lookup=asset_lookup,
                states=states,
                sequence_suffix="0",
            )
            if normalized is not None:
                ticks.append(normalized)
        return ticks

    def _normalize_kalshi_payload(
        self,
        payload: Any,
        *,
        market_lookup: dict[str, CrossVenueMarketMap],
    ) -> list[NormalizedTick]:
        ticks: list[NormalizedTick] = []
        for message in _iter_messages(payload):
            message_type = str(message.get("type") or message.get("channel") or "").strip().lower()
            if message_type != "ticker":
                continue
            inner = message.get("msg") if isinstance(message.get("msg"), dict) else message
            tick = self._normalize_kalshi_ticker(inner, market_lookup=market_lookup)
            if tick is not None:
                ticks.append(tick)
        return ticks

    def _normalize_kalshi_ticker(
        self,
        payload: dict[str, Any],
        *,
        market_lookup: dict[str, CrossVenueMarketMap],
    ) -> NormalizedTick | None:
        market_ticker = str(
            payload.get("market_ticker")
            or payload.get("ticker")
            or payload.get("marketTicker")
            or ""
        ).strip()
        if not market_ticker or market_ticker not in market_lookup:
            return None

        market = market_lookup[market_ticker]
        yes_bid = _clip_probability(_to_float(payload.get("yes_bid_dollars") or payload.get("yes_bid")))
        yes_ask = _clip_probability(_to_float(payload.get("yes_ask_dollars") or payload.get("yes_ask")))
        last_price = _clip_probability(
            _to_float(
                payload.get("price_dollars")
                or payload.get("last_price_dollars")
                or payload.get("last_price")
            )
        )
        if not last_price and yes_bid and yes_ask:
            last_price = _clip_probability((yes_bid + yes_ask) / 2.0)
        bid_size = _to_float(payload.get("yes_bid_volume"))
        ask_size = _to_float(payload.get("yes_ask_volume"))
        bids = [{"price": yes_bid, "size": bid_size}] if yes_bid else []
        asks = [{"price": yes_ask, "size": ask_size}] if yes_ask else []
        return NormalizedTick(
            venue="kalshi",
            market_id=market.market_id,
            market_slug=market.market_slug,
            event_type="ticker",
            status=str(payload.get("status") or market.status),
            exchange_timestamp=_parse_stream_timestamp(payload.get("ts") or payload.get("time")),
            received_at=_now(),
            sequence_id=str(payload.get("seq") or payload.get("sid") or f"{market.market_id}:ticker"),
            last_price=last_price,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=_clip_probability(1.0 - yes_ask) if yes_ask else 0.0,
            no_ask=_clip_probability(1.0 - yes_bid) if yes_bid else 0.0,
            bid_size=bid_size,
            ask_size=ask_size,
            trade_size=_to_float(payload.get("last_size")),
            volume=_to_float(payload.get("volume") or payload.get("volume_fp")),
            open_interest=_to_float(payload.get("open_interest") or payload.get("open_interest_fp")),
            bids=bids,
            asks=asks,
            payload=payload,
        )

    def _apply_polymarket_event(
        self,
        event: dict[str, Any],
        *,
        event_type: str,
        parent_message: dict[str, Any],
        asset_lookup: dict[str, tuple[CrossVenueMarketMap, str]],
        states: dict[str, _PolymarketMarketState],
        sequence_suffix: str,
    ) -> NormalizedTick | None:
        asset_id = str(
            event.get("asset_id")
            or event.get("assetId")
            or parent_message.get("asset_id")
            or parent_message.get("assetId")
            or ""
        ).strip()
        if event_type == "market_resolved":
            market_id = str(event.get("market") or parent_message.get("market") or "").strip()
            for candidate_market, side in asset_lookup.values():
                if candidate_market.market_id != market_id:
                    continue
                state = states.setdefault(candidate_market.market_id, _PolymarketMarketState(market=candidate_market))
                winning_asset_id = str(event.get("winning_asset_id") or parent_message.get("winning_asset_id") or "").strip()
                winning_side = "yes" if asset_id and winning_asset_id == asset_id else side
                state.mark_resolved(
                    winning_side=winning_side,
                    timestamp=_parse_stream_timestamp(
                        event.get("timestamp") or parent_message.get("timestamp")
                    ),
                )
                return state.to_tick(
                    payload=parent_message,
                    event_type=event_type,
                    sequence_id=f"{candidate_market.market_id}:{event_type}:{sequence_suffix}",
                )
            return None
        if not asset_id or asset_id not in asset_lookup:
            return None

        market, side = asset_lookup[asset_id]
        state = states.setdefault(market.market_id, _PolymarketMarketState(market=market))
        timestamp = _parse_stream_timestamp(event.get("timestamp") or parent_message.get("timestamp"))
        event_sequence = str(event.get("hash") or parent_message.get("hash") or f"{asset_id}:{timestamp.timestamp()}:{sequence_suffix}")
        status = str(parent_message.get("status") or event.get("status") or "").strip().lower()
        if status:
            state.status = status

        if event_type == "book":
            bids = _normalize_levels(event.get("bids"))
            asks = _normalize_levels(event.get("asks"))
            state.set_side_snapshot(
                side=side,
                bids=bids,
                asks=asks,
                timestamp=timestamp,
                last_price=_to_float(event.get("price")),
            )
        elif event_type == "best_bid_ask":
            state.update_side_top(
                side=side,
                best_bid=_to_float(event.get("best_bid")),
                best_ask=_to_float(event.get("best_ask")),
                bid_size=_to_float(event.get("bid_size")),
                ask_size=_to_float(event.get("ask_size")),
                last_price=_to_float(event.get("price")),
                timestamp=timestamp,
            )
        elif event_type == "last_trade_price":
            price = _to_float(event.get("price"))
            state.update_side_top(
                side=side,
                best_bid=0.0,
                best_ask=0.0,
                last_price=price,
                trade_size=_to_float(event.get("size")),
                timestamp=timestamp,
            )
        elif event_type == "price_change":
            state.update_side_top(
                side=side,
                best_bid=_to_float(event.get("best_bid")),
                best_ask=_to_float(event.get("best_ask")),
                bid_size=_to_float(event.get("size")),
                ask_size=_to_float(event.get("size")),
                last_price=_to_float(event.get("price")),
                trade_size=_to_float(event.get("size")),
                timestamp=timestamp,
            )
        else:
            return None

        return state.to_tick(
            payload=parent_message,
            event_type=event_type or "ticker",
            sequence_id=event_sequence,
        )
