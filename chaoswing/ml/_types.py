"""Shared value types for the pure ML core.

These types are the language the ranker, the trainer, and the evaluator all
speak. Keep them small, frozen where possible, and free of Django imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Literal


SplitName = Literal["train", "val", "test"]


class Relevance(IntEnum):
    """Graded relevance label mined from lead-lag co-movement.

    The rubric is defined in `chaoswing.ml.grading`; this enum fixes the
    integer codes that flow through datasets, losses, and metrics.
    """

    UNRELATED = 0
    WEAK = 1
    RELATED = 2
    STRONG = 3


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class TemporalCutoff:
    """A timestamp that bounds what data a label or feature may use.

    Every leakage-sensitive operation (label mining, split assignment,
    forecasting probe) takes a `TemporalCutoff`. Helpers here are the
    chokepoint: bypassing the cutoff means bypassing this type, which is
    grep-able and reviewable.
    """

    timestamp: datetime
    label: str = "cutoff"

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _ensure_utc(self.timestamp))

    def __lt__(self, other: "TemporalCutoff") -> bool:
        return self.timestamp < other.timestamp


@dataclass(frozen=True, slots=True)
class MarketDoc:
    """A market as the reranker sees it: id, surface text, and timestamps.

    `first_seen` is the earliest observation timestamp the corpus has for this
    market. The temporal split logic uses it to keep "future" markets out of
    training; rerankers should generally not look at it directly.
    """

    market_id: str
    title: str
    description: str = ""
    category: str = ""
    first_seen: datetime | None = None
    event_family: str = ""

    @property
    def text(self) -> str:
        if self.description:
            return f"{self.title}\n{self.description}"
        return self.title


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """One reranker output row: a candidate market id with a score and rank."""

    market_id: str
    score: float
    rank: int


@dataclass(slots=True)
class RelevanceRecord:
    """One row of the mined label set (mirrors ml_data/relevance_labels.jsonl).

    `window_end` is the latest timestamp whose data contributed to the label.
    It MUST be strictly before the temporal cutoff this record was mined
    under; the leakage tests rely on this invariant.
    """

    source_id: str
    candidate_id: str
    relevance: Relevance
    max_xcorr: float = 0.0
    best_lag_seconds: int = 0
    granger_p: float | None = None
    shock_co_fraction: float = 0.0
    source_first_seen: datetime | None = None
    candidate_first_seen: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    event_family: str = ""
    negative_kind: Literal["positive", "hard", "easy", ""] = ""
    extra: dict = field(default_factory=dict)

    def to_jsonl(self) -> dict:
        def _iso(d: datetime | None) -> str | None:
            return d.isoformat() if d else None

        return {
            "source_id": self.source_id,
            "candidate_id": self.candidate_id,
            "relevance": int(self.relevance),
            "max_xcorr": self.max_xcorr,
            "best_lag_seconds": self.best_lag_seconds,
            "granger_p": self.granger_p,
            "shock_co_fraction": self.shock_co_fraction,
            "source_first_seen": _iso(self.source_first_seen),
            "candidate_first_seen": _iso(self.candidate_first_seen),
            "window_start": _iso(self.window_start),
            "window_end": _iso(self.window_end),
            "event_family": self.event_family,
            "negative_kind": self.negative_kind,
            "extra": self.extra,
        }
