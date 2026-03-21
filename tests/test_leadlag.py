from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from apps.web.models import (
    CrossVenueMarketMap,
    ExperimentRun,
    LeadLagPair,
    LeadLagSignal,
    MarketEventTick,
    OrderBookLevelSnapshot,
    PaperTrade,
)
from apps.web.services.leadlag import (
    CrossVenueMarketMapService,
    LeadLagBacktestService,
    LeadLagMonitorService,
    LeadLagPairBuilderService,
    LeadLagSignalService,
    LeadLagTickCollectionService,
    PaperTradingService,
)


def create_market(
    *,
    venue: str,
    market_id: str,
    title: str,
    resolution_window: str = "2026-03-18",
    resolution_text: str = "",
    category: str = "Macro",
    tags: list[str] | None = None,
) -> CrossVenueMarketMap:
    return CrossVenueMarketMap.objects.create(
        venue=venue,
        market_id=market_id,
        market_slug=market_id.lower().replace(" ", "-"),
        event_slug=market_id.lower().replace(" ", "-"),
        title=title,
        url=f"https://example.com/{venue}/{market_id}",
        category=category,
        status="open",
        tags=tags or [],
        resolution_text=resolution_text,
        resolution_window=resolution_window,
        metadata={"volume": 120000, "liquidity": 80000, "open_interest": 60000},
    )


def create_tick(
    *,
    market: CrossVenueMarketMap,
    price: float,
    timestamp: datetime,
    yes_bid: float | None = None,
    yes_ask: float | None = None,
    bid_size: float = 800.0,
    ask_size: float = 800.0,
) -> MarketEventTick:
    yes_bid = price - 0.01 if yes_bid is None else yes_bid
    yes_ask = price + 0.01 if yes_ask is None else yes_ask
    tick = MarketEventTick.objects.create(
        venue=market.venue,
        market_map=market,
        market_id=market.market_id,
        market_slug=market.market_slug,
        event_type="ticker",
        status="open",
        exchange_timestamp=timestamp,
        received_at=timestamp,
        sequence_id=f"{market.market_id}-{int(timestamp.timestamp())}",
        last_price=price,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=1.0 - yes_ask,
        no_ask=1.0 - yes_bid,
        bid_size=bid_size,
        ask_size=ask_size,
        volume=1000.0,
        open_interest=500.0,
        payload={},
    )
    OrderBookLevelSnapshot.objects.create(
        venue=market.venue,
        market_map=market,
        tick=tick,
        market_id=market.market_id,
        captured_at=timestamp,
        best_yes_bid=yes_bid,
        best_yes_ask=yes_ask,
        best_no_bid=1.0 - yes_ask,
        best_no_ask=1.0 - yes_bid,
        total_bid_depth=bid_size,
        total_ask_depth=ask_size,
        bids=[{"price": yes_bid, "size": bid_size}],
        asks=[{"price": yes_ask, "size": ask_size}],
        payload={},
    )
    return tick


@override_settings(
    CHAOSWING_ENABLE_REMOTE_FETCH=False,
    CHAOSWING_ENABLE_LLM=False,
    CHAOSWING_RATE_LIMIT_ENABLED=False,
)
class LeadLagResearchTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_kalshi_sync_uses_event_feed_and_deactivates_legacy_bundle_rows(self):
        class StubPolymarketClient:
            def list_events(self, params):  # pragma: no cover - shape only
                return []

        class StubKalshiClient:
            def list_events(self, *, limit=100, status="open", with_nested_markets=True):
                del limit, status, with_nested_markets
                return [
                    {
                        "event_ticker": "KXNEWPOPE-70",
                        "title": "Who will the next Pope be?",
                        "category": "World",
                        "series_ticker": "KXNEWPOPE",
                        "markets": [
                            {
                                "ticker": "KXNEWPOPE-70-PPAR",
                                "event_ticker": "KXNEWPOPE-70",
                                "title": "Who will the next Pope be?",
                                "yes_sub_title": "Pietro Parolin",
                                "status": "active",
                                "market_type": "binary",
                                "yes_bid_dollars": "0.0600",
                                "yes_ask_dollars": "0.0900",
                                "last_price_dollars": "0.0600",
                                "open_interest_fp": "4570.00",
                                "volume_fp": "12744.00",
                                "rules_primary": "If Pietro Parolin becomes the next Pope, this market resolves to Yes.",
                            },
                            {
                                "ticker": "KXMVEFAKE-1",
                                "event_ticker": "KXMVEFAKE",
                                "title": "yes A,yes B,yes C,yes D",
                                "status": "active",
                                "market_type": "binary",
                                "mve_selected_legs": [{"ticker": "A"}],
                            },
                        ],
                    },
                    {
                        "event_ticker": "KXOAIANTH",
                        "title": "Will OpenAI or Anthropic IPO first?",
                        "category": "Financials",
                        "series_ticker": "KXOAIANTH",
                        "markets": [
                            {
                                "ticker": "KXOAIANTH-ANTH",
                                "event_ticker": "KXOAIANTH",
                                "title": "Will OpenAI or Anthropic IPO first?",
                                "yes_sub_title": "Anthropic",
                                "status": "active",
                                "market_type": "binary",
                                "yes_bid_dollars": "0.2200",
                                "yes_ask_dollars": "0.2600",
                                "last_price_dollars": "0.2400",
                                "open_interest_fp": "1250.00",
                                "volume_fp": "5800.00",
                                "rules_primary": "If Anthropic IPOs before OpenAI, this market resolves to Yes.",
                            }
                        ],
                    },
                ]

            def list_markets(self, *, limit=100, status="open", mve_filter=None):  # pragma: no cover - fallback only
                del limit, status, mve_filter
                return []

        legacy = create_market(
            venue="kalshi",
            market_id="KXMVECROSSCATEGORY-OLD",
            title="yes A,yes B,yes C,yes D",
        )
        legacy.metadata = {
            "liquidity": 0.0,
            "open_interest": 0.0,
            "yes_bid": 0.0,
            "yes_ask": 0.0,
            "last_price": 0.0,
        }
        legacy.save(update_fields=["metadata"])

        report = CrossVenueMarketMapService(
            polymarket_client=StubPolymarketClient(),
            kalshi_client=StubKalshiClient(),
        ).sync(limit_per_venue=2, persist=True)

        self.assertEqual(report["kalshi"], 2)
        self.assertGreaterEqual(report["deactivated"], 1)
        titles = list(
            CrossVenueMarketMap.objects.filter(venue="kalshi", is_active=True).values_list("title", flat=True)
        )
        self.assertIn("Who will the next Pope be? - Pietro Parolin", titles)
        self.assertIn("Will OpenAI or Anthropic IPO first?", titles)
        legacy.refresh_from_db()
        self.assertFalse(legacy.is_active)

    def test_pair_builder_prefers_equivalent_market_and_rejects_spurious_pairs(self):
        leader = create_market(
            venue="polymarket",
            market_id="fed-march",
            title="Fed decision in March",
            resolution_text="Will the Fed cut rates in March?",
            tags=["Fed", "Rates"],
        )
        follower = create_market(
            venue="kalshi",
            market_id="FED-26MAR-CUT",
            title="Fed decision in March",
            resolution_text="Will the Federal Reserve cut rates in March?",
            tags=["Fed", "Rates"],
        )
        create_market(
            venue="kalshi",
            market_id="BTC-120K",
            title="Bitcoin above 120k by June",
            resolution_text="Crypto price market",
            tags=["Bitcoin"],
        )
        base = datetime(2026, 3, 11, 10, 0, tzinfo=UTC)
        for index, (leader_price, follower_price) in enumerate(
            [
                (0.39, 0.37),
                (0.44, 0.42),
                (0.52, 0.49),
                (0.58, 0.55),
                (0.63, 0.6),
                (0.67, 0.64),
            ]
        ):
            create_tick(
                market=leader,
                price=leader_price,
                timestamp=base + timedelta(seconds=index * 60),
            )
            create_tick(
                market=follower,
                price=follower_price,
                timestamp=base + timedelta(seconds=index * 60 + 15),
            )

        report = LeadLagPairBuilderService().build(persist=True)

        self.assertEqual(report["created"], 1)
        self.assertEqual(LeadLagPair.objects.count(), 1)
        pair = LeadLagPair.objects.get()
        self.assertEqual(pair.pair_type, "logical_equivalent")
        self.assertTrue(pair.is_trade_eligible)
        self.assertIn("liquid venue", pair.direction_reason.lower())

    def test_pair_builder_detects_candidate_equivalence_from_shared_entity_and_theme(self):
        create_market(
            venue="polymarket",
            market_id="walz-2028",
            title="Presidential Election Winner 2028: Will Tim Walz win the 2028 US Presidential Election?",
            resolution_text="2028 presidential winner market.",
            tags=["Politics", "Election", "Tim Walz"],
        )
        create_market(
            venue="kalshi",
            market_id="PRES-2028-WALZ",
            title="Who will win the next presidential election? - Tim Walz",
            resolution_text="If Tim Walz wins the next presidential election, this market resolves to Yes.",
            tags=["Politics", "Election", "Tim Walz"],
        )

        report = LeadLagPairBuilderService().build(persist=True)

        self.assertEqual(report["created"], 1)
        pair = LeadLagPair.objects.get()
        self.assertEqual(pair.pair_type, "logical_equivalent")
        self.assertGreaterEqual(pair.semantic_score, 0.5)
        self.assertGreaterEqual(pair.resolution_score, 0.55)
        self.assertFalse(pair.is_trade_eligible)
        self.assertIn("tim", (pair.metadata or {}).get("shared_entity_tokens", []))

    def test_pair_builder_rejects_false_shared_driver_without_theme_overlap(self):
        create_market(
            venue="polymarket",
            market_id="barron-pardon",
            title="Will Barron Trump receive a presidential pardon before Jan 21, 2029?",
            resolution_text="Pardon market.",
            tags=["Politics", "Trump"],
        )
        create_market(
            venue="kalshi",
            market_id="BTC-150K",
            title="What price will Bitcoin hit in March? - Will Bitcoin reach $150,000 in March?",
            resolution_text="Bitcoin price market.",
            tags=["Bitcoin", "Crypto"],
        )

        report = LeadLagPairBuilderService().build(persist=True)

        self.assertEqual(report["created"], 0)
        self.assertEqual(LeadLagPair.objects.count(), 0)

    def test_pair_builder_deactivates_stale_pairs_outside_current_universe(self):
        left = create_market(
            venue="polymarket",
            market_id="legacy-left",
            title="Legacy left market",
            tags=["Legacy"],
        )
        right = create_market(
            venue="kalshi",
            market_id="legacy-right",
            title="Legacy right market",
            tags=["Legacy"],
        )
        stale_pair = LeadLagPair.objects.create(
            pair_type="narrative_spillover",
            leader_market=left,
            follower_market=right,
            semantic_score=0.4,
            causal_score=0.4,
            resolution_score=0.4,
            stability_score=0.0,
            composite_score=0.4,
            expected_latency_seconds=120,
            direction_reason="Legacy pair.",
            is_trade_eligible=False,
            is_active=True,
        )
        left.is_active = False
        right.is_active = False
        left.save(update_fields=["is_active"])
        right.save(update_fields=["is_active"])

        create_market(
            venue="polymarket",
            market_id="walz-2028",
            title="Presidential Election Winner 2028: Will Tim Walz win the 2028 US Presidential Election?",
            resolution_text="2028 presidential winner market.",
            tags=["Politics", "Election", "Tim Walz"],
        )
        create_market(
            venue="kalshi",
            market_id="PRES-2028-WALZ",
            title="Who will win the next presidential election? - Tim Walz",
            resolution_text="If Tim Walz wins the next presidential election, this market resolves to Yes.",
            tags=["Politics", "Election", "Tim Walz"],
        )

        report = LeadLagPairBuilderService().build(persist=True)

        self.assertEqual(report["created"], 1)
        self.assertEqual(report["deactivated"], 1)
        stale_pair.refresh_from_db()
        self.assertFalse(stale_pair.is_active)

    def test_pair_builder_stability_snapshot_reports_ready_when_leader_moves_first(self):
        leader = create_market(
            venue="polymarket",
            market_id="walz-leader",
            title="Tim Walz 2028",
            tags=["Politics", "Election", "Tim Walz"],
        )
        follower = create_market(
            venue="kalshi",
            market_id="WALZ-FOLLOW",
            title="Tim Walz next presidential election",
            tags=["Politics", "Election", "Tim Walz"],
        )
        base = datetime(2026, 3, 12, 9, 0, tzinfo=UTC)
        for index, (leader_price, follower_price) in enumerate(
            [
                (0.32, 0.31),
                (0.38, 0.35),
                (0.44, 0.41),
                (0.5, 0.47),
                (0.56, 0.53),
                (0.61, 0.58),
            ]
        ):
            create_tick(
                market=leader,
                price=leader_price,
                timestamp=base + timedelta(seconds=index * 60),
            )
            create_tick(
                market=follower,
                price=follower_price,
                timestamp=base + timedelta(seconds=index * 60 + 20),
            )

        snapshot = LeadLagPairBuilderService()._stability_snapshot(
            leader,
            follower,
            expected_latency_seconds=45,
        )

        self.assertEqual(snapshot["readiness_status"], "ready")
        self.assertGreaterEqual(snapshot["leader_first_ratio"], 0.8)
        self.assertGreaterEqual(snapshot["move_samples"], 4)

    def test_signal_service_marks_already_repriced_follower_as_no_trade(self):
        leader = create_market(
            venue="polymarket",
            market_id="fed-driver",
            title="Fed cut in March",
            tags=["Fed", "Rates"],
        )
        follower = create_market(
            venue="kalshi",
            market_id="FED-REACT",
            title="Fed cut in March reaction",
            tags=["Fed", "Rates"],
        )
        pair = LeadLagPair.objects.create(
            pair_type="logical_equivalent",
            leader_market=leader,
            follower_market=follower,
            semantic_score=0.9,
            causal_score=0.8,
            resolution_score=0.8,
            stability_score=0.7,
            composite_score=0.82,
            expected_latency_seconds=45,
            direction_reason="Equivalent contracts.",
            is_trade_eligible=True,
        )
        base = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)
        create_tick(market=leader, price=0.42, timestamp=base - timedelta(seconds=20))
        create_tick(market=leader, price=0.44, timestamp=base - timedelta(seconds=10))
        create_tick(market=leader, price=0.58, timestamp=base)
        create_tick(market=follower, price=0.40, timestamp=base - timedelta(seconds=20))
        create_tick(market=follower, price=0.45, timestamp=base - timedelta(seconds=10))
        create_tick(market=follower, price=0.57, timestamp=base + timedelta(milliseconds=800))

        report = LeadLagSignalService().scan(persist=True)

        self.assertEqual(report["no_trade"], 1)
        signal = LeadLagSignal.objects.get(pair=pair)
        self.assertEqual(signal.status, "no_trade")
        self.assertEqual(signal.no_trade_reason, "follower_already_repriced")

    def test_backtest_and_paper_trader_persist_positive_trade(self):
        leader = create_market(
            venue="polymarket",
            market_id="oil-driver",
            title="Crude oil above 80",
            resolution_text="Oil shock market",
            tags=["Oil", "Crude"],
        )
        follower = create_market(
            venue="kalshi",
            market_id="INFL-REACT",
            title="Inflation above expectations",
            resolution_text="Inflation reaction market",
            tags=["Inflation", "Macro"],
        )
        LeadLagPair.objects.create(
            pair_type="shared_driver",
            leader_market=leader,
            follower_market=follower,
            semantic_score=0.65,
            causal_score=0.82,
            resolution_score=0.58,
            stability_score=0.6,
            composite_score=0.69,
            expected_latency_seconds=120,
            direction_reason="Oil shock market leads the inflation reaction market.",
            is_trade_eligible=True,
        )
        base = datetime(2026, 3, 11, 13, 0, tzinfo=UTC)
        create_tick(market=leader, price=0.32, timestamp=base - timedelta(seconds=30))
        create_tick(market=leader, price=0.35, timestamp=base - timedelta(seconds=20))
        create_tick(market=leader, price=0.52, timestamp=base)
        create_tick(market=follower, price=0.34, timestamp=base - timedelta(seconds=30))
        create_tick(market=follower, price=0.35, timestamp=base - timedelta(seconds=20))
        create_tick(market=follower, price=0.36, timestamp=base + timedelta(seconds=1))

        signal_report = LeadLagSignalService().scan(persist=True)
        self.assertEqual(signal_report["candidate"], 1)

        create_tick(market=follower, price=0.48, timestamp=base + timedelta(seconds=240))
        trade_report = PaperTradingService().run(persist=True, horizon_seconds=180)
        self.assertEqual(trade_report["opened_trades"], 1)
        self.assertEqual(trade_report["closed_trades"], 1)
        trade = PaperTrade.objects.get()
        self.assertGreater(trade.net_pnl, 0.0)

        backtest = LeadLagBacktestService().run(persist=True)
        self.assertEqual(backtest["task_type"], "leadlag_backtest")
        self.assertTrue(ExperimentRun.objects.filter(task_type="leadlag_backtest").exists())

    def test_monitor_page_and_api_render(self):
        leader = create_market(venue="polymarket", market_id="fed-ui", title="Fed decision in March", tags=["Fed"])
        follower = create_market(venue="kalshi", market_id="FED-UI", title="Fed decision in March", tags=["Fed"])
        pair = LeadLagPair.objects.create(
            pair_type="logical_equivalent",
            leader_market=leader,
            follower_market=follower,
            semantic_score=0.9,
            causal_score=0.8,
            resolution_score=0.8,
            stability_score=0.7,
            composite_score=0.82,
            expected_latency_seconds=45,
            direction_reason="Equivalent contracts.",
            is_trade_eligible=True,
        )
        tick = create_tick(
            market=leader,
            price=0.5,
            timestamp=datetime(2026, 3, 11, 14, 0, tzinfo=UTC),
        )
        follower_tick = create_tick(
            market=follower,
            price=0.45,
            timestamp=datetime(2026, 3, 11, 14, 0, 1, tzinfo=UTC),
        )
        signal = LeadLagSignal.objects.create(
            pair=pair,
            leader_tick=tick,
            follower_tick=follower_tick,
            status="candidate",
            signal_direction="buy_yes",
            leader_price_move=0.1,
            follower_gap=0.08,
            expected_edge=0.03,
            cost_estimate=0.02,
            latency_ms=1000,
            liquidity_score=0.8,
            rationale="Leader moved first.",
        )
        PaperTrade.objects.create(
            signal=signal,
            status="closed",
            side="buy_yes",
            entry_price=0.46,
            exit_price=0.5,
            gross_pnl=0.04,
            net_pnl=0.02,
            fee_paid=0.01,
            slippage_paid=0.01,
            opened_at=datetime(2026, 3, 11, 14, 0, 1, tzinfo=UTC),
            closed_at=datetime(2026, 3, 11, 14, 3, 1, tzinfo=UTC),
        )

        page = self.client.get(reverse("web:leadlag_monitor"))
        summary = self.client.get(reverse("web:leadlag_summary"))
        pairs = self.client.get(reverse("web:leadlag_pairs"))
        pair_detail = self.client.get(reverse("web:leadlag_pair_detail", args=[pair.id]))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Lead-Lag Monitor")
        self.assertContains(page, "Candidate spillover opportunities")
        self.assertEqual(summary.status_code, 200)
        self.assertIn("summary_cards", summary.json())
        self.assertEqual(pairs.status_code, 200)
        self.assertEqual(pairs.json()["count"], 1)
        self.assertEqual(pair_detail.status_code, 200)
        self.assertEqual(pair_detail.json()["id"], pair.id)
        self.assertEqual(pair_detail.json()["signals"][0]["status"], "candidate")
        self.assertIn("readiness", pair_detail.json())

    def test_collect_live_ticks_command_ingests_fixture_ticks(self):
        market = create_market(
            venue="polymarket",
            market_id="fixture-market",
            title="Fixture market",
        )
        del market
        with TemporaryDirectory() as tmpdir:
            fixture_path = Path(tmpdir) / "ticks.jsonl"
            fixture_path.write_text(
                json.dumps(
                    {
                        "venue": "polymarket",
                        "market_id": "fixture-market",
                        "market_slug": "fixture-market",
                        "event_type": "ticker",
                        "status": "open",
                        "exchange_timestamp": "2026-03-11T15:00:00Z",
                        "received_at": "2026-03-11T15:00:01Z",
                        "last_price": 0.51,
                        "yes_bid": 0.5,
                        "yes_ask": 0.52,
                        "bid_size": 900,
                        "ask_size": 870,
                        "bids": [{"price": 0.5, "size": 900}],
                        "asks": [{"price": 0.52, "size": 870}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = io.StringIO()
            call_command("collect_live_ticks", "--fixture-path", str(fixture_path), stdout=stdout)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["ticks"], 1)
        self.assertEqual(MarketEventTick.objects.count(), 1)
        self.assertEqual(OrderBookLevelSnapshot.objects.count(), 1)

    def test_collect_live_ticks_skips_duplicate_fixture_ticks(self):
        create_market(
            venue="polymarket",
            market_id="fixture-dup",
            title="Fixture duplicate market",
        )
        payload = {
            "venue": "polymarket",
            "market_id": "fixture-dup",
            "market_slug": "fixture-dup",
            "event_type": "ticker",
            "status": "open",
            "exchange_timestamp": "2026-03-11T15:00:00Z",
            "received_at": "2026-03-11T15:00:01Z",
            "sequence_id": "dup-seq-1",
            "last_price": 0.51,
            "yes_bid": 0.5,
            "yes_ask": 0.52,
            "bid_size": 900,
            "ask_size": 870,
            "bids": [{"price": 0.5, "size": 900}],
            "asks": [{"price": 0.52, "size": 870}],
        }
        with TemporaryDirectory() as tmpdir:
            fixture_path = Path(tmpdir) / "ticks.jsonl"
            fixture_path.write_text(
                json.dumps(payload) + "\n" + json.dumps(payload) + "\n",
                encoding="utf-8",
            )
            report = LeadLagTickCollectionService().collect(
                fixture_path=fixture_path,
                persist=True,
            )

        self.assertEqual(report["ticks"], 1)
        self.assertEqual(report["duplicates_skipped"], 1)
        self.assertEqual(MarketEventTick.objects.count(), 1)
        self.assertEqual(OrderBookLevelSnapshot.objects.count(), 1)

    def test_tick_collection_prioritizes_active_pairs_when_requested(self):
        paired_market = create_market(
            venue="polymarket",
            market_id="paired-market",
            title="Paired market",
            tags=["Fed"],
        )
        follower_market = create_market(
            venue="kalshi",
            market_id="paired-follow",
            title="Paired follow market",
            tags=["Fed"],
        )
        unpaired_market = create_market(
            venue="polymarket",
            market_id="unpaired-market",
            title="Unpaired market",
            tags=["Oil"],
        )
        LeadLagPair.objects.create(
            pair_type="logical_equivalent",
            leader_market=paired_market,
            follower_market=follower_market,
            semantic_score=0.8,
            causal_score=0.75,
            resolution_score=0.7,
            stability_score=0.4,
            composite_score=0.62,
            expected_latency_seconds=45,
            direction_reason="Equivalent contracts.",
            metadata={"readiness_status": "collect_more"},
            is_trade_eligible=False,
        )

        markets = LeadLagTickCollectionService()._market_selection(
            venue="polymarket",
            limit=2,
            active_pairs_only=True,
        )

        self.assertEqual([market.market_id for market in markets], ["paired-market"])
        self.assertNotIn(unpaired_market.market_id, [market.market_id for market in markets])

    def test_monitor_summary_cache_builds(self):
        summary = LeadLagMonitorService().build_cached(force_refresh=True)
        self.assertIn("summary_cards", summary)
        self.assertIn("totals", summary)
        self.assertIn("coverage_status", summary)
        self.assertIn("coverage_summary", summary)
