from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings

from apps.web.models import GraphRun, MarketSnapshot, ResolutionLabel
from apps.web.services.contracts import PolymarketEventSnapshot, PolymarketMarket
from apps.web.services.snapshot_ingestion import SnapshotCollectionResult, SnapshotIngestionService


class StubWatchlistService:
    def __init__(self, urls: list[str]):
        self._urls = urls

    def urls(self) -> list[str]:
        return list(self._urls)


class StubTrendingService:
    def __init__(self, urls: list[str]):
        self._urls = urls

    def get_trending(self, limit: int = 6) -> list[dict[str, str]]:
        return [{"url": url} for url in self._urls[:limit]]


class StubMetadataService:
    def __init__(self, snapshots_by_url: dict[str, list[PolymarketEventSnapshot] | PolymarketEventSnapshot]):
        self.snapshots_by_url = {
            key: value if isinstance(value, list) else [value]
            for key, value in snapshots_by_url.items()
        }

    def hydrate(self, source_url: str) -> PolymarketEventSnapshot:
        queue = self.snapshots_by_url[source_url]
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]


def build_snapshot(
    *,
    slug: str,
    title: str,
    updated_at: str,
    status: str = "open",
    source_kind: str = "gamma-api",
    yes_probability: float = 0.61,
) -> PolymarketEventSnapshot:
    url = f"https://polymarket.com/event/{slug}"
    return PolymarketEventSnapshot(
        source_url=url,
        canonical_url=url,
        event_id=slug,
        slug=slug,
        title=title,
        description=f"{title} description",
        resolution_source="",
        image_url="",
        icon_url="",
        status=status,
        category="Macro",
        tags=["Fed", "Rates"],
        tag_ids=["1", "2"],
        outcomes=["Yes", "No"],
        updated_at=updated_at,
        volume=12_500.0,
        liquidity=6_200.0,
        open_interest=3_100.0,
        markets=[
            PolymarketMarket(
                id=f"{slug}-market",
                slug=slug,
                question=title,
                description=f"{title} contract",
                resolution_source="",
                image_url="",
                icon_url="",
                category="Macro",
                outcomes=["Yes", "No"],
                outcome_prices=[yes_probability, round(1.0 - yes_probability, 3)],
                volume=12_500.0,
                liquidity=6_200.0,
                updated_at=updated_at,
            )
        ],
        source_kind=source_kind,
        subtitle="Macro | 1 market",
    )


@override_settings(
    CHAOSWING_ENABLE_REMOTE_FETCH=False,
    CHAOSWING_ENABLE_LLM=False,
    CHAOSWING_RATE_LIMIT_ENABLED=False,
)
class SnapshotIngestionServiceTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_build_source_urls_expands_and_dedupes_sources(self):
        GraphRun.objects.create(
            source_url="https://polymarket.com/event/recent-run/",
            event_slug="recent-run",
            event_title="Recent run",
        )

        service = SnapshotIngestionService(
            metadata_service=StubMetadataService({}),
            watchlist_service=StubWatchlistService(
                [
                    "https://polymarket.com/event/watchlist-market?ref=featured",
                    "https://polymarket.com/event/explicit-market",
                ]
            ),
            trending_service=StubTrendingService(
                [
                    "https://polymarket.com/event/trending-market#focus",
                    "https://polymarket.com/event/recent-run",
                ]
            ),
        )

        urls = service.build_source_urls(
            explicit_urls=["https://polymarket.com/event/explicit-market#top"],
            include_watchlists=True,
            recent_run_limit=1,
            trending_limit=2,
        )

        self.assertEqual(
            urls,
            [
                "https://polymarket.com/event/explicit-market",
                "https://polymarket.com/event/watchlist-market",
                "https://polymarket.com/event/recent-run",
                "https://polymarket.com/event/trending-market",
            ],
        )

    def test_collect_urls_creates_snapshot_and_resolution_label(self):
        snapshot = build_snapshot(
            slug="fed-decision-in-march-885",
            title="Fed decision in March?",
            updated_at="2026-03-11T12:00:00Z",
            status="closed",
            yes_probability=0.995,
        )
        service = SnapshotIngestionService(
            metadata_service=StubMetadataService({snapshot.canonical_url: snapshot}),
            watchlist_service=StubWatchlistService([]),
            trending_service=StubTrendingService([]),
        )

        result = service.collect_urls(
            [
                f"{snapshot.canonical_url}#focus",
                f"{snapshot.canonical_url}?ref=test",
            ],
            collector="unit_test",
        )

        self.assertEqual(result.source_count, 1)
        self.assertEqual(result.created, 1)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.labeled, 1)
        self.assertEqual(MarketSnapshot.objects.count(), 1)
        self.assertEqual(ResolutionLabel.objects.count(), 1)

        stored_snapshot = MarketSnapshot.objects.get()
        self.assertEqual(stored_snapshot.source_url, snapshot.canonical_url)
        self.assertAlmostEqual(stored_snapshot.implied_probability, 0.995)
        self.assertEqual(stored_snapshot.source_kind, "gamma-api")

    def test_collect_urls_updates_existing_snapshot_and_backfills_resolution_label(self):
        snapshot = build_snapshot(
            slug="existing-market",
            title="Existing market updated",
            updated_at="2026-03-11T14:00:00Z",
            status="closed",
            source_kind="gamma-api",
            yes_probability=0.997,
        )
        existing = MarketSnapshot.objects.create(
            source_url=snapshot.canonical_url,
            event_slug=snapshot.slug,
            event_title="Existing market stale",
            status="closed",
            category="Macro",
            source_kind="fallback",
            tags=["Fallback"],
            outcomes=["Yes", "No"],
            implied_probability=0.0,
            volume=0.0,
            liquidity=0.0,
            open_interest=0.0,
            related_market_count=0,
            evidence_count=0,
            snapshot_at=datetime(2026, 3, 11, 14, 0, tzinfo=UTC),
            payload={"source_kind": "fallback"},
        )

        service = SnapshotIngestionService(
            metadata_service=StubMetadataService({snapshot.canonical_url: snapshot}),
            watchlist_service=StubWatchlistService([]),
            trending_service=StubTrendingService([]),
        )

        result = service.collect_urls([snapshot.canonical_url], collector="unit_test")

        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(result.labeled, 1)
        self.assertEqual(MarketSnapshot.objects.count(), 1)
        self.assertEqual(ResolutionLabel.objects.count(), 1)

        existing.refresh_from_db()
        self.assertEqual(existing.event_title, snapshot.title)
        self.assertEqual(existing.source_kind, "gamma-api")
        self.assertAlmostEqual(existing.implied_probability, 0.997)

        second_result = service.collect_urls([snapshot.canonical_url], collector="unit_test")
        self.assertEqual(second_result.created, 0)
        self.assertEqual(second_result.updated, 0)
        self.assertEqual(second_result.skipped, 1)
        self.assertEqual(second_result.labeled, 0)

    def test_collect_urls_propagates_terminal_label_across_event_history(self):
        earlier_snapshot = MarketSnapshot.objects.create(
            source_url="https://polymarket.com/event/fed-terminal-market",
            event_slug="fed-terminal-market",
            event_title="Fed terminal market",
            status="open",
            category="Macro",
            source_kind="gamma-api",
            tags=["Fed"],
            outcomes=["Yes", "No"],
            implied_probability=0.61,
            volume=1000.0,
            liquidity=800.0,
            open_interest=400.0,
            related_market_count=0,
            evidence_count=0,
            snapshot_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            payload={
                "markets": [
                    {
                        "outcomes": ["Yes", "No"],
                        "outcome_prices": [0.61, 0.39],
                    }
                ]
            },
        )
        terminal_snapshot = build_snapshot(
            slug="fed-terminal-market",
            title="Fed terminal market",
            updated_at="2026-03-11T14:00:00Z",
            status="closed",
            yes_probability=0.997,
        )
        service = SnapshotIngestionService(
            metadata_service=StubMetadataService({terminal_snapshot.canonical_url: terminal_snapshot}),
            watchlist_service=StubWatchlistService([]),
            trending_service=StubTrendingService([]),
        )

        result = service.collect_urls([terminal_snapshot.canonical_url], collector="unit_test")

        self.assertEqual(result.created, 1)
        self.assertEqual(result.labeled, 2)
        self.assertEqual(MarketSnapshot.objects.filter(event_slug="fed-terminal-market").count(), 2)
        self.assertEqual(ResolutionLabel.objects.filter(event_slug="fed-terminal-market").count(), 2)
        earlier_snapshot.refresh_from_db()
        self.assertEqual(earlier_snapshot.resolution_label.resolved_outcome, "Yes")
        self.assertEqual(earlier_snapshot.resolution_label.source, "outcome_prices")

    def test_backfill_resolution_labels_refreshes_remote_and_labels_event_family(self):
        open_snapshot = MarketSnapshot.objects.create(
            source_url="https://polymarket.com/event/backfill-market",
            event_slug="backfill-market",
            event_title="Backfill market",
            status="open",
            category="Macro",
            source_kind="gamma-api",
            tags=["Macro"],
            outcomes=["Yes", "No"],
            implied_probability=0.42,
            volume=1200.0,
            liquidity=900.0,
            open_interest=500.0,
            related_market_count=0,
            evidence_count=0,
            snapshot_at=datetime(2026, 3, 2, 12, 0, tzinfo=UTC),
            payload={
                "markets": [
                    {
                        "outcomes": ["Yes", "No"],
                        "outcome_prices": [0.42, 0.58],
                    }
                ]
            },
        )
        closed_snapshot = build_snapshot(
            slug="backfill-market",
            title="Backfill market",
            updated_at="2026-03-12T12:00:00Z",
            status="closed",
            yes_probability=0.999,
        )
        service = SnapshotIngestionService(
            metadata_service=StubMetadataService({open_snapshot.source_url: closed_snapshot}),
            watchlist_service=StubWatchlistService([]),
            trending_service=StubTrendingService([]),
        )

        report = service.backfill_resolution_labels(refresh_remote=True, collector="unit_test")

        self.assertEqual(report["event_count"], 1)
        self.assertEqual(report["remote_refreshed"], 1)
        self.assertEqual(report["snapshots_created"], 1)
        self.assertEqual(report["labels_created"], 2)
        self.assertEqual(ResolutionLabel.objects.filter(event_slug="backfill-market").count(), 2)
        self.assertEqual(MarketSnapshot.objects.filter(event_slug="backfill-market", status="closed").count(), 1)

    def test_label_resolved_markets_command_supports_remote_refresh(self):
        MarketSnapshot.objects.create(
            source_url="https://polymarket.com/event/command-backfill-market",
            event_slug="command-backfill-market",
            event_title="Command backfill market",
            status="open",
            category="Macro",
            source_kind="gamma-api",
            tags=["Macro"],
            outcomes=["Yes", "No"],
            implied_probability=0.37,
            volume=1000.0,
            liquidity=750.0,
            open_interest=410.0,
            related_market_count=0,
            evidence_count=0,
            snapshot_at=datetime(2026, 3, 3, 12, 0, tzinfo=UTC),
            payload={
                "markets": [
                    {
                        "outcomes": ["Yes", "No"],
                        "outcome_prices": [0.37, 0.63],
                    }
                ]
            },
        )
        stdout = io.StringIO()
        refreshed_snapshot = build_snapshot(
            slug="command-backfill-market",
            title="Command backfill market",
            updated_at="2026-03-13T00:00:00Z",
            status="closed",
            yes_probability=0.999,
        )

        with patch(
            "apps.web.services.snapshot_ingestion.PolymarketMetadataService.hydrate",
            return_value=refreshed_snapshot,
        ):
            call_command("label_resolved_markets", "--refresh-remote", stdout=stdout)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["remote_refreshed"], 1)
        self.assertEqual(payload["labels_created"], 2)
        self.assertEqual(ResolutionLabel.objects.filter(event_slug="command-backfill-market").count(), 2)

    def test_live_snapshot_collector_command_streams_iteration_summary(self):
        stdout = io.StringIO()
        fake_result = SnapshotCollectionResult(
            source_count=3,
            created=2,
            updated=1,
            skipped=0,
            failed=0,
            labeled=1,
            collector="run_live_snapshot_collector",
            urls=["https://polymarket.com/event/fed-decision-in-march-885"],
            started_at="2026-03-11T12:00:00+00:00",
            completed_at="2026-03-11T12:00:01+00:00",
            duration_seconds=1.0,
        )

        with patch(
            "apps.web.management.commands.run_live_snapshot_collector.SnapshotIngestionService.iter_live_loop",
            return_value=iter([fake_result]),
        ) as mocked_iter:
            call_command(
                "run_live_snapshot_collector",
                "--iterations",
                "1",
                "--poll-seconds",
                "0",
                stdout=stdout,
            )

        payload = json.loads(stdout.getvalue().strip())
        self.assertEqual(payload["iteration"], 1)
        self.assertEqual(payload["created"], 2)
        self.assertEqual(payload["updated"], 1)
        self.assertTrue(payload["source_mode"]["include_watchlists"])
        mocked_iter.assert_called_once()
