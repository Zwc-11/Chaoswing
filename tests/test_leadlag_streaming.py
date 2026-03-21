from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.test.utils import override_settings

from apps.web.models import CrossVenueMarketMap
from apps.web.services.leadlag_streaming import KalshiWebSocketAuthSigner, LeadLagStreamingCollectionService


def create_stream_market(*, market_id: str, title: str) -> CrossVenueMarketMap:
    return CrossVenueMarketMap.objects.create(
        venue="polymarket",
        market_id=market_id,
        market_slug=market_id.lower(),
        event_slug=market_id.lower(),
        title=title,
        url=f"https://example.com/{market_id}",
        category="Politics",
        status="open",
        tags=["Politics", "Election"],
        resolution_text="Binary market",
        resolution_window="2026-11-05",
        metadata={
            "clob_token_ids": [f"{market_id}-yes", f"{market_id}-no"],
            "outcomes": ["Yes", "No"],
            "volume": 100000,
            "liquidity": 90000,
        },
    )


def create_kalshi_stream_market(*, market_id: str, title: str) -> CrossVenueMarketMap:
    return CrossVenueMarketMap.objects.create(
        venue="kalshi",
        market_id=market_id,
        market_slug=market_id.lower(),
        event_slug=market_id.lower(),
        title=title,
        url=f"https://example.com/{market_id}",
        category="Macro",
        status="open",
        tags=["Fed"],
        resolution_text="Binary market",
        resolution_window="2026-03-20",
        metadata={"volume": 50000, "open_interest": 45000},
    )


@override_settings(
    CHAOSWING_ENABLE_REMOTE_FETCH=False,
    CHAOSWING_ENABLE_LLM=False,
    CHAOSWING_RATE_LIMIT_ENABLED=False,
)
class LeadLagStreamingTests(TestCase):
    def test_polymarket_asset_lookup_maps_yes_and_no_tokens(self):
        market = create_stream_market(market_id="walz-2028", title="Tim Walz 2028")
        service = LeadLagStreamingCollectionService()

        lookup = service._polymarket_asset_lookup([market])

        self.assertEqual(lookup["walz-2028-yes"][1], "yes")
        self.assertEqual(lookup["walz-2028-no"][1], "no")

    def test_polymarket_book_snapshot_on_yes_asset_becomes_normalized_tick(self):
        market = create_stream_market(market_id="fed-march", title="Fed in March")
        service = LeadLagStreamingCollectionService()
        lookup = service._polymarket_asset_lookup([market])

        ticks = service._normalize_polymarket_payload(
            {
                "event_type": "book",
                "asset_id": "fed-march-yes",
                "timestamp": 1710000000000,
                "bids": [{"price": 0.51, "size": 1200}],
                "asks": [{"price": 0.53, "size": 1100}],
            },
            asset_lookup=lookup,
            states={},
        )

        self.assertEqual(len(ticks), 1)
        tick = ticks[0]
        self.assertEqual(tick.market_id, "fed-march")
        self.assertAlmostEqual(tick.yes_bid, 0.51)
        self.assertAlmostEqual(tick.yes_ask, 0.53)
        self.assertAlmostEqual(tick.bid_size, 1200.0)
        self.assertAlmostEqual(tick.ask_size, 1100.0)
        self.assertEqual(tick.exchange_timestamp, datetime.fromtimestamp(1710000000, tz=UTC))

    def test_polymarket_no_asset_snapshot_converts_back_to_yes_side(self):
        market = create_stream_market(market_id="oil-june", title="Oil June")
        service = LeadLagStreamingCollectionService()
        lookup = service._polymarket_asset_lookup([market])

        ticks = service._normalize_polymarket_payload(
            {
                "event_type": "book",
                "asset_id": "oil-june-no",
                "timestamp": 1710000000000,
                "bids": [{"price": 0.47, "size": 800}],
                "asks": [{"price": 0.49, "size": 700}],
            },
            asset_lookup=lookup,
            states={},
        )

        self.assertEqual(len(ticks), 1)
        tick = ticks[0]
        self.assertAlmostEqual(tick.yes_bid, 0.51)
        self.assertAlmostEqual(tick.yes_ask, 0.53)
        self.assertAlmostEqual(tick.no_bid, 0.47)
        self.assertAlmostEqual(tick.no_ask, 0.49)
        self.assertTrue(tick.bids)
        self.assertTrue(tick.asks)

    def test_polymarket_resolution_event_marks_status_and_terminal_price(self):
        market = create_stream_market(market_id="election", title="Election")
        service = LeadLagStreamingCollectionService()
        lookup = service._polymarket_asset_lookup([market])
        states = {}

        ticks = service._normalize_polymarket_payload(
            {
                "event_type": "market_resolved",
                "market": "election",
                "winning_asset_id": "election-yes",
                "timestamp": 1710000060000,
            },
            asset_lookup=lookup,
            states=states,
        )

        self.assertEqual(len(ticks), 1)
        tick = ticks[0]
        self.assertEqual(tick.status, "resolved")
        self.assertAlmostEqual(tick.last_price, 1.0)
        self.assertAlmostEqual(tick.yes_bid, 1.0)
        self.assertAlmostEqual(tick.no_bid, 0.0)

    def test_kalshi_ticker_message_normalizes_to_tick(self):
        market = create_kalshi_stream_market(market_id="FED-2026-CUT", title="Fed cut")
        service = LeadLagStreamingCollectionService()

        ticks = service._normalize_kalshi_payload(
            {
                "type": "ticker",
                "msg": {
                    "market_ticker": "FED-2026-CUT",
                    "yes_bid_dollars": "0.44",
                    "yes_ask_dollars": "0.47",
                    "price_dollars": "0.46",
                    "yes_bid_volume": "120",
                    "yes_ask_volume": "140",
                    "volume_fp": "4000.0",
                    "open_interest_fp": "900.0",
                    "time": "2026-03-12T10:00:00Z",
                    "sid": 7,
                },
            },
            market_lookup={"FED-2026-CUT": market},
        )

        self.assertEqual(len(ticks), 1)
        tick = ticks[0]
        self.assertEqual(tick.market_id, "FED-2026-CUT")
        self.assertAlmostEqual(tick.yes_bid, 0.44)
        self.assertAlmostEqual(tick.yes_ask, 0.47)
        self.assertAlmostEqual(tick.last_price, 0.46)
        self.assertAlmostEqual(tick.no_bid, 0.53)
        self.assertAlmostEqual(tick.no_ask, 0.56)


class LeadLagStreamingMessageTests(SimpleTestCase):
    def test_decode_message_handles_json_and_pong(self):
        service = LeadLagStreamingCollectionService()

        self.assertEqual(service._decode_message("PONG"), {})
        self.assertEqual(service._decode_message(b'{"event_type":"book"}'), {"event_type": "book"})

    def test_kalshi_signer_without_config_returns_empty_headers(self):
        signer = KalshiWebSocketAuthSigner(access_key_id="", private_key_path=Path("missing.pem"))

        self.assertFalse(signer.is_configured())
        self.assertEqual(signer.build_headers(ws_url="wss://api.elections.kalshi.com/trade-api/ws/v2"), {})

    def test_supervised_stream_runs_refresh_hooks_on_schedule(self):
        service = LeadLagStreamingCollectionService()

        with (
            patch.object(service, "stream", side_effect=[{"ticks": 2}, {"ticks": 3}]) as stream_mock,
            patch.object(
                service,
                "_refresh_research_state",
                side_effect=[{"pairs": {"updated": 1}}, {"signals": {"candidate": 1}}],
            ) as refresh_mock,
            patch("apps.web.services.leadlag_streaming.time.sleep") as sleep_mock,
        ):
            reports = list(
                service.iter_supervised_stream(
                    iterations=2,
                    reconnect_seconds=1,
                    rebuild_pairs_every=1,
                    scan_signals_every=1,
                )
            )

        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0]["status"], "ok")
        self.assertEqual(reports[0]["stream"]["ticks"], 2)
        self.assertEqual(reports[1]["refresh"]["signals"]["candidate"], 1)
        self.assertEqual(stream_mock.call_count, 2)
        self.assertEqual(refresh_mock.call_count, 2)
        sleep_mock.assert_called_once_with(1)
