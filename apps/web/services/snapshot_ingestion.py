from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterator, Protocol
from urllib.parse import urlparse, urlunparse

from django.db import IntegrityError, transaction

from apps.web.models import GraphRun, MarketSnapshot

from .contracts import PolymarketEventSnapshot
from .market_intelligence import BenchmarkSummaryService, WatchlistService
from .polymarket import PolymarketMetadataService, TrendingMarketsService
from .resolution_labeling import ResolutionLabelingService


class _MetadataServiceProtocol(Protocol):
    def hydrate(self, source_url: str) -> PolymarketEventSnapshot: ...


class _TrendingServiceProtocol(Protocol):
    def get_trending(self, limit: int = 6) -> list[dict[str, Any]]: ...


def _parse_snapshot_datetime(value: str) -> datetime:
    if value:
        clean = str(value).strip()
        if clean:
            try:
                return datetime.fromisoformat(clean.replace("Z", "+00:00"))
            except ValueError:
                pass
    return datetime.now(tz=UTC)


def _normalize_source_url(url: str) -> str:
    clean = str(url or "").strip()
    if not clean:
        return ""
    parsed = urlparse(clean)
    normalized = parsed._replace(query="", fragment="")
    return urlunparse(normalized).rstrip("/")


def _event_implied_probability(snapshot: PolymarketEventSnapshot) -> float:
    for market in snapshot.markets:
        if market.outcomes and market.outcome_prices and len(market.outcomes) == len(market.outcome_prices):
            normalized = [str(outcome).strip().lower() for outcome in market.outcomes]
            if "yes" in normalized:
                yes_index = normalized.index("yes")
                try:
                    return float(market.outcome_prices[yes_index])
                except (TypeError, ValueError, IndexError):
                    continue
            try:
                return float(market.outcome_prices[0])
            except (TypeError, ValueError, IndexError):
                continue
    return 0.0


@dataclass(slots=True)
class SnapshotCollectionResult:
    source_count: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    labeled: int = 0
    collector: str = "snapshot_ingestion"
    urls: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "collector": self.collector,
            "source_count": self.source_count,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "labeled": self.labeled,
            "urls": self.urls,
            "errors": self.errors,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
        }


class SnapshotIngestionService:
    def __init__(
        self,
        *,
        metadata_service: _MetadataServiceProtocol | None = None,
        trending_service: _TrendingServiceProtocol | None = None,
        watchlist_service: WatchlistService | None = None,
    ):
        self.metadata_service = metadata_service or PolymarketMetadataService()
        self.trending_service = trending_service or TrendingMarketsService()
        self.watchlist_service = watchlist_service or WatchlistService()
        self.resolution_labeling = ResolutionLabelingService()

    def build_source_urls(
        self,
        *,
        explicit_urls: list[str] | None = None,
        include_watchlists: bool = True,
        recent_run_limit: int = 0,
        trending_limit: int = 0,
    ) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()

        def remember(url: str) -> None:
            clean = _normalize_source_url(url)
            if not clean or clean in seen:
                return
            seen.add(clean)
            urls.append(clean)

        for url in explicit_urls or []:
            remember(url)

        if include_watchlists:
            for url in self.watchlist_service.urls():
                remember(url)

        if recent_run_limit > 0:
            recent_urls = (
                GraphRun.objects.exclude(source_url="")
                .order_by("-created_at")
                .values_list("source_url", flat=True)[:recent_run_limit]
            )
            for url in recent_urls:
                remember(url)

        if trending_limit > 0:
            for market in self.trending_service.get_trending(limit=trending_limit):
                remember(str(market.get("url") or ""))

        return urls

    def collect_urls(
        self,
        urls: list[str],
        *,
        collector: str = "snapshot_ingestion",
    ) -> SnapshotCollectionResult:
        normalized_urls = self._dedupe_urls(urls)
        started_at = datetime.now(tz=UTC)
        started_monotonic = time.monotonic()
        result = SnapshotCollectionResult(
            source_count=len(normalized_urls),
            collector=collector,
            urls=list(normalized_urls),
            started_at=started_at.isoformat(),
        )

        for url in normalized_urls:
            try:
                snapshot = self.metadata_service.hydrate(url)
            except Exception as exc:
                result.failed += 1
                result.errors.append({"url": url, "error": str(exc)})
                continue

            action, labeled = self.persist_snapshot(snapshot, collector=collector)
            if action == "created":
                result.created += 1
            elif action == "updated":
                result.updated += 1
            elif action == "skipped":
                result.skipped += 1
            result.labeled += labeled

        completed_at = datetime.now(tz=UTC)
        result.completed_at = completed_at.isoformat()
        result.duration_seconds = round(time.monotonic() - started_monotonic, 3)

        if result.created or result.updated or result.labeled:
            BenchmarkSummaryService.invalidate_cached_summary()

        return result

    def iter_live_loop(
        self,
        *,
        explicit_urls: list[str] | None = None,
        include_watchlists: bool = True,
        recent_run_limit: int = 0,
        trending_limit: int = 0,
        poll_seconds: int = 300,
        iterations: int = 0,
        collector: str = "live_snapshot_collector",
    ) -> Iterator[SnapshotCollectionResult]:
        iteration = 0
        while iterations <= 0 or iteration < iterations:
            urls = self.build_source_urls(
                explicit_urls=explicit_urls,
                include_watchlists=include_watchlists,
                recent_run_limit=recent_run_limit,
                trending_limit=trending_limit,
            )
            yield self.collect_urls(urls, collector=collector)
            iteration += 1
            if iterations > 0 and iteration >= iterations:
                break
            if poll_seconds > 0:
                time.sleep(poll_seconds)

    def run_live_loop(
        self,
        *,
        explicit_urls: list[str] | None = None,
        include_watchlists: bool = True,
        recent_run_limit: int = 0,
        trending_limit: int = 0,
        poll_seconds: int = 300,
        iterations: int = 1,
        collector: str = "live_snapshot_collector",
    ) -> list[SnapshotCollectionResult]:
        return list(
            self.iter_live_loop(
                explicit_urls=explicit_urls,
                include_watchlists=include_watchlists,
                recent_run_limit=recent_run_limit,
                trending_limit=trending_limit,
                poll_seconds=poll_seconds,
                iterations=iterations,
                collector=collector,
            )
        )

    def persist_snapshot(
        self,
        snapshot: PolymarketEventSnapshot,
        *,
        collector: str,
    ) -> tuple[str, int]:
        snapshot_at = _parse_snapshot_datetime(snapshot.updated_at)
        defaults = self._build_snapshot_defaults(snapshot, snapshot_at=snapshot_at)
        existing = MarketSnapshot.objects.filter(
            event_slug=snapshot.slug,
            snapshot_at=snapshot_at,
        ).first()
        if existing:
            updated = self._update_existing_snapshot(existing, defaults)
            labeled = self._maybe_create_resolution_labels(existing, snapshot, collector=collector)
            return "updated" if updated else "skipped", labeled

        with transaction.atomic():
            record = MarketSnapshot.objects.create(**defaults)
            labeled = self._maybe_create_resolution_labels(record, snapshot, collector=collector)
        return "created", labeled

    def _build_snapshot_defaults(
        self,
        snapshot: PolymarketEventSnapshot,
        *,
        snapshot_at: datetime,
    ) -> dict[str, Any]:
        return {
            "source_url": snapshot.canonical_url or snapshot.source_url,
            "event_slug": snapshot.slug,
            "event_title": snapshot.title,
            "status": snapshot.status,
            "category": snapshot.category,
            "source_kind": snapshot.source_kind,
            "tags": snapshot.tags,
            "outcomes": snapshot.outcomes,
            "implied_probability": _event_implied_probability(snapshot),
            "volume": snapshot.volume,
            "liquidity": snapshot.liquidity,
            "open_interest": snapshot.open_interest,
            "related_market_count": 0,
            "evidence_count": 0,
            "snapshot_at": snapshot_at,
            "payload": snapshot.as_dict(),
        }

    def _update_existing_snapshot(
        self,
        record: MarketSnapshot,
        defaults: dict[str, Any],
    ) -> bool:
        update_fields: list[str] = []
        for field_name, value in defaults.items():
            if field_name == "snapshot_at":
                continue
            if getattr(record, field_name) != value:
                setattr(record, field_name, value)
                update_fields.append(field_name)
        if not update_fields:
            return False
        record.save(update_fields=update_fields)
        return True

    def _maybe_create_resolution_labels(
        self,
        record: MarketSnapshot,
        snapshot: PolymarketEventSnapshot,
        *,
        collector: str,
    ) -> int:
        try:
            return self.resolution_labeling.label_from_terminal_snapshot(
                record=record,
                terminal_snapshot=snapshot,
                source="outcome_prices",
                metadata={"collector": collector},
            )
        except IntegrityError:
            return 0

    def backfill_resolution_labels(
        self,
        *,
        refresh_remote: bool = False,
        limit_events: int = 0,
        collector: str = "label_resolved_markets",
    ) -> dict[str, Any]:
        unlabeled = (
            MarketSnapshot.objects.filter(resolution_label__isnull=True)
            .order_by("-snapshot_at", "-created_at")
        )
        latest_by_slug: dict[str, MarketSnapshot] = {}
        for snapshot in unlabeled:
            if snapshot.event_slug and snapshot.event_slug not in latest_by_slug:
                latest_by_slug[snapshot.event_slug] = snapshot
                if limit_events > 0 and len(latest_by_slug) >= limit_events:
                    break

        report = {
            "event_count": len(latest_by_slug),
            "labels_created": 0,
            "propagated_from_existing": 0,
            "snapshots_created": 0,
            "snapshots_updated": 0,
            "snapshots_skipped": 0,
            "remote_refreshed": 0,
            "unresolved": 0,
            "failed": 0,
            "errors": [],
        }
        for event_slug, snapshot in latest_by_slug.items():
            propagated = self.resolution_labeling.propagate_existing_event_labels(event_slug=event_slug)
            if propagated:
                report["labels_created"] += propagated
                report["propagated_from_existing"] += propagated
                continue

            outcome, probability = self.resolution_labeling.infer_from_snapshot_record(snapshot)
            if snapshot.status == "closed" and outcome and probability >= self.resolution_labeling.decisive_threshold:
                report["labels_created"] += self.resolution_labeling.label_event_family(
                    event_slug=event_slug,
                    resolved_outcome=outcome,
                    resolved_probability=probability,
                    source="outcome_prices",
                    metadata={"collector": collector, "source_snapshot_id": snapshot.id},
                )
                continue

            if not refresh_remote:
                report["unresolved"] += 1
                continue

            try:
                refreshed_snapshot = self.metadata_service.hydrate(snapshot.source_url)
            except Exception as exc:
                report["failed"] += 1
                report["errors"].append({"event_slug": event_slug, "error": str(exc)})
                continue

            action, labeled = self.persist_snapshot(refreshed_snapshot, collector=collector)
            if action == "created":
                report["snapshots_created"] += 1
            elif action == "updated":
                report["snapshots_updated"] += 1
            else:
                report["snapshots_skipped"] += 1
            report["remote_refreshed"] += 1
            if labeled:
                report["labels_created"] += labeled
            else:
                report["unresolved"] += 1

        if (
            report["labels_created"]
            or report["snapshots_created"]
            or report["snapshots_updated"]
        ):
            BenchmarkSummaryService.invalidate_cached_summary()
        return report

    def _dedupe_urls(self, urls: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for url in urls:
            normalized = _normalize_source_url(url)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped
