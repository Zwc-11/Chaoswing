"""ORM access layer for the reranker pipeline.

Keeping Django queries behind a thin interface means `label_mining.py` can be
unit-tested against an in-memory fake repository without standing up the DB.
This is the only file in `apps/ranker/services/` that imports Django.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from django.db import transaction

from apps.ranker.models import RelevanceLabel
from apps.web.models import MarketSnapshot
from chaoswing.ml._types import MarketDoc, RelevanceRecord, TemporalCutoff


class SnapshotRepository(Protocol):
    """The narrow interface the label miner needs."""

    def iter_eligible_markets(self, *, cutoff: TemporalCutoff) -> Iterable[MarketDoc]: ...

    def load_probability_series(
        self,
        market_id: str,
        *,
        cutoff: TemporalCutoff,
    ) -> tuple[list[datetime], list[float]]: ...

    def first_seen(self, market_id: str) -> datetime | None: ...


@dataclass(slots=True)
class _SnapshotRow:
    market_id: str
    title: str
    description: str
    category: str
    event_family: str
    snapshot_at: datetime
    implied_probability: float


class DjangoSnapshotRepository:
    """`SnapshotRepository` backed by `apps.web.models.MarketSnapshot`.

    The `event_slug` column doubles as the event-family key for now — it is
    the natural dedup key the existing ingestion code already populates. If a
    coarser family ever becomes desirable, add a derived field rather than
    threading it through here.
    """

    def __init__(self, *, min_observations: int = 24):
        self.min_observations = min_observations

    def iter_eligible_markets(self, *, cutoff: TemporalCutoff) -> Iterable[MarketDoc]:
        qs = (
            MarketSnapshot.objects.filter(snapshot_at__lt=cutoff.timestamp)
            .values("event_slug")
            .order_by("event_slug")
            .distinct()
        )
        for row in qs.iterator():
            slug = row["event_slug"]
            if not slug:
                continue
            doc = self._build_doc(slug, cutoff=cutoff)
            if doc is not None:
                yield doc

    def _build_doc(self, market_id: str, *, cutoff: TemporalCutoff) -> MarketDoc | None:
        snaps = (
            MarketSnapshot.objects.filter(event_slug=market_id, snapshot_at__lt=cutoff.timestamp)
            .order_by("snapshot_at")
            .values("event_title", "category", "payload", "snapshot_at")
        )
        rows = list(snaps[: max(self.min_observations, 1)])
        if len(rows) < self.min_observations:
            return None
        first = rows[0]
        description = ""
        if isinstance(first.get("payload"), dict):
            description = str(first["payload"].get("description", ""))
        return MarketDoc(
            market_id=market_id,
            title=first.get("event_title", "") or market_id,
            description=description,
            category=first.get("category", "") or "",
            first_seen=first["snapshot_at"],
            event_family=market_id,
        )

    def load_probability_series(
        self,
        market_id: str,
        *,
        cutoff: TemporalCutoff,
    ) -> tuple[list[datetime], list[float]]:
        rows = (
            MarketSnapshot.objects.filter(event_slug=market_id, snapshot_at__lt=cutoff.timestamp)
            .order_by("snapshot_at")
            .values_list("snapshot_at", "implied_probability")
        )
        timestamps: list[datetime] = []
        values: list[float] = []
        for ts, prob in rows.iterator():
            timestamps.append(ts)
            values.append(float(prob))
        return timestamps, values

    def first_seen(self, market_id: str) -> datetime | None:
        row = (
            MarketSnapshot.objects.filter(event_slug=market_id)
            .order_by("snapshot_at")
            .values_list("snapshot_at", flat=True)
            .first()
        )
        return row

    def load_market_docs(
        self,
        market_ids: set[str] | list[str],
        *,
        cutoff: TemporalCutoff | None = None,
    ) -> dict[str, "MarketDoc"]:
        """Build `{market_id: MarketDoc}` for every requested slug.

        If `cutoff` is provided, only snapshots strictly before the cutoff are
        considered; otherwise the earliest snapshot per market is used (handy
        for the benchmark, where we need text for *every* market in the
        labeled candidate lists regardless of when they first appeared).

        Slugs without any snapshot are silently dropped from the returned map;
        callers should treat the missing key as "skip this row."
        """
        if not market_ids:
            return {}
        qs = MarketSnapshot.objects.filter(event_slug__in=list(market_ids))
        if cutoff is not None:
            qs = qs.filter(snapshot_at__lt=cutoff.timestamp)
        rows = (
            qs.order_by("event_slug", "snapshot_at")
            .values("event_slug", "event_title", "category", "payload", "snapshot_at")
        )
        docs: dict[str, MarketDoc] = {}
        for row in rows.iterator():
            slug = row["event_slug"]
            if slug in docs:
                continue  # earliest wins (queryset is ordered)
            description = ""
            payload = row.get("payload")
            if isinstance(payload, dict):
                description = str(payload.get("description", ""))
            docs[slug] = MarketDoc(
                market_id=slug,
                title=row.get("event_title", "") or slug,
                description=description,
                category=row.get("category", "") or "",
                first_seen=row["snapshot_at"],
                event_family=slug,
            )
        return docs


class LabelRepository:
    """Bulk-create `RelevanceLabel` rows for a mining run."""

    def __init__(self, *, mining_run_id: str, cutoff: TemporalCutoff):
        self.mining_run_id = mining_run_id
        self.cutoff = cutoff

    @transaction.atomic
    def replace_for_run(self, records: list[RelevanceRecord]) -> int:
        """Delete any existing rows for this run, then bulk-insert `records`.

        Returns the number of rows written. Replace semantics keep the
        pipeline idempotent: re-running the miner with the same `run_id`
        overwrites the previous output instead of duplicating it.
        """
        RelevanceLabel.objects.filter(mining_run_id=self.mining_run_id).delete()
        objs = [self._record_to_model(r) for r in records]
        RelevanceLabel.objects.bulk_create(objs, batch_size=1000, ignore_conflicts=False)
        return len(objs)

    def _record_to_model(self, record: RelevanceRecord) -> RelevanceLabel:
        return RelevanceLabel(
            source_market_id=record.source_id,
            candidate_market_id=record.candidate_id,
            event_family=record.event_family,
            relevance=int(record.relevance),
            negative_kind=record.negative_kind or "",
            max_xcorr=record.max_xcorr,
            best_lag_seconds=record.best_lag_seconds,
            granger_p=record.granger_p,
            shock_co_fraction=record.shock_co_fraction,
            source_first_seen=record.source_first_seen,
            candidate_first_seen=record.candidate_first_seen,
            window_start=record.window_start,
            window_end=record.window_end,
            mining_cutoff=self.cutoff.timestamp,
            mining_run_id=self.mining_run_id,
            metadata=record.extra or {},
        )
