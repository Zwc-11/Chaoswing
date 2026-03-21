from __future__ import annotations

from typing import Any

from apps.web.models import MarketSnapshot, ResolutionLabel

from .contracts import PolymarketEventSnapshot


def infer_terminal_resolution(snapshot: PolymarketEventSnapshot | dict[str, Any]) -> tuple[str, float]:
    markets: list[Any]
    if isinstance(snapshot, PolymarketEventSnapshot):
        markets = snapshot.markets
    else:
        markets = snapshot.get("markets") or snapshot.get("snapshot", {}).get("markets") or []

    best_outcome = ""
    best_probability = 0.0
    for market in markets:
        if isinstance(market, dict):
            outcomes = market.get("outcomes") or []
            outcome_prices = market.get("outcome_prices") or market.get("outcomePrices") or []
        else:
            outcomes = getattr(market, "outcomes", []) or []
            outcome_prices = getattr(market, "outcome_prices", []) or []
        if len(outcomes) != len(outcome_prices):
            continue
        for outcome, probability in zip(outcomes, outcome_prices, strict=False):
            try:
                probability_value = float(probability)
            except (TypeError, ValueError):
                continue
            if probability_value > best_probability:
                best_outcome = str(outcome)
                best_probability = probability_value
    return best_outcome, best_probability


class ResolutionLabelingService:
    decisive_threshold = 0.99

    def label_event_family(
        self,
        *,
        event_slug: str,
        resolved_outcome: str,
        resolved_probability: float,
        source: str,
        metadata: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> int:
        if not event_slug or not resolved_outcome or resolved_probability < self.decisive_threshold:
            return 0

        snapshots = MarketSnapshot.objects.filter(event_slug=event_slug).order_by("snapshot_at", "created_at")
        created = 0
        for snapshot in snapshots:
            defaults = {
                "event_slug": event_slug,
                "resolved_outcome": resolved_outcome,
                "resolved_probability": resolved_probability,
                "source": source,
                "metadata": metadata or {},
            }
            if overwrite:
                ResolutionLabel.objects.update_or_create(
                    market_snapshot=snapshot,
                    defaults=defaults,
                )
                created += 1
                continue
            if hasattr(snapshot, "resolution_label") and snapshot.resolution_label is not None:
                continue
            ResolutionLabel.objects.create(
                market_snapshot=snapshot,
                **defaults,
            )
            created += 1
        return created

    def label_from_terminal_snapshot(
        self,
        *,
        record: MarketSnapshot,
        terminal_snapshot: PolymarketEventSnapshot,
        source: str,
        metadata: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> int:
        if terminal_snapshot.status != "closed":
            return 0
        outcome, probability = infer_terminal_resolution(terminal_snapshot)
        merged_metadata = {
            "terminal_status": terminal_snapshot.status,
            "terminal_source_kind": terminal_snapshot.source_kind,
            **(metadata or {}),
        }
        return self.label_event_family(
            event_slug=record.event_slug or terminal_snapshot.slug,
            resolved_outcome=outcome,
            resolved_probability=probability,
            source=source,
            metadata=merged_metadata,
            overwrite=overwrite,
        )

    def propagate_existing_event_labels(self, *, event_slug: str) -> int:
        label = (
            ResolutionLabel.objects.filter(event_slug=event_slug)
            .order_by("-created_at")
            .first()
        )
        if not label:
            return 0
        metadata = dict(label.metadata or {})
        metadata.setdefault("propagated_from_existing_label", True)
        return self.label_event_family(
            event_slug=event_slug,
            resolved_outcome=label.resolved_outcome,
            resolved_probability=label.resolved_probability,
            source=label.source,
            metadata=metadata,
            overwrite=False,
        )

    def infer_from_snapshot_record(self, snapshot: MarketSnapshot) -> tuple[str, float]:
        payload = snapshot.payload if isinstance(snapshot.payload, dict) else {}
        return infer_terminal_resolution(payload)
