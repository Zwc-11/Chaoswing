"""Temporal split assignment, deduplicated by event family.

Module 2 of the rebuild plan. The single sentence to repeat everywhere:

    "I used temporal splits instead of random splits, because random splits
    would leak event clusters across train and test."

Pure functions over python dataclasses; the management command handles I/O.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from chaoswing.ml._types import RelevanceRecord, SplitName, TemporalCutoff, _ensure_utc
from chaoswing.ml.leakage import LeakageError


@dataclass(frozen=True, slots=True)
class TemporalSplitConfig:
    """The two cutoffs that define train / val / test.

    Rules:
      * train = source first_seen strictly before `train_cutoff`.
      * val   = source first_seen in [train_cutoff, val_cutoff).
      * test  = source first_seen >= `val_cutoff`.
    Event families that span a cutoff are pinned to the **earliest** member's
    split so a family never appears on both sides of a boundary — that's the
    dedup invariant.
    """

    train_cutoff: TemporalCutoff
    val_cutoff: TemporalCutoff

    def __post_init__(self) -> None:
        if not self.train_cutoff < self.val_cutoff:
            raise ValueError(
                f"train_cutoff ({self.train_cutoff.timestamp.isoformat()}) must be "
                f"strictly before val_cutoff ({self.val_cutoff.timestamp.isoformat()})"
            )


@dataclass(slots=True)
class SplitAssignment:
    family_to_split: dict[str, SplitName]
    record_to_split: dict[tuple[str, str], SplitName]
    counts: dict[SplitName, int]


def _family_first_seen(records: Sequence[RelevanceRecord]) -> dict[str, datetime]:
    earliest: dict[str, datetime] = {}
    for record in records:
        if not record.event_family or record.source_first_seen is None:
            continue
        first = _ensure_utc(record.source_first_seen)
        existing = earliest.get(record.event_family)
        if existing is None or first < existing:
            earliest[record.event_family] = first
    return earliest


def _assign_family(first_seen: datetime, config: TemporalSplitConfig) -> SplitName:
    if first_seen < config.train_cutoff.timestamp:
        return "train"
    if first_seen < config.val_cutoff.timestamp:
        return "val"
    return "test"


def compute_splits(
    records: Iterable[RelevanceRecord],
    config: TemporalSplitConfig,
) -> SplitAssignment:
    """Assign each record to train/val/test by its `event_family`'s first_seen.

    Raises `LeakageError` if any record's `window_end` is on or after the
    `train_cutoff` while the record lands in train (defensive — the label
    miner should already have caught this).
    """
    records_list = list(records)
    family_first = _family_first_seen(records_list)
    family_to_split: dict[str, SplitName] = {
        family: _assign_family(first, config) for family, first in family_first.items()
    }

    record_to_split: dict[tuple[str, str], SplitName] = {}
    counts: dict[SplitName, int] = defaultdict(int)
    for record in records_list:
        if not record.event_family or record.event_family not in family_to_split:
            continue
        split = family_to_split[record.event_family]
        if split == "train" and record.window_end is not None:
            if _ensure_utc(record.window_end) >= config.train_cutoff.timestamp:
                raise LeakageError(
                    f"record source={record.source_id} candidate={record.candidate_id} "
                    f"assigned to train but window_end is at or after train_cutoff"
                )
        record_to_split[(record.source_id, record.candidate_id)] = split
        counts[split] += 1
    for split_name in ("train", "val", "test"):
        counts.setdefault(split_name, 0)  # type: ignore[arg-type]
    return SplitAssignment(
        family_to_split=family_to_split,
        record_to_split=record_to_split,
        counts=dict(counts),
    )


def group_records_by_split(
    records: Iterable[RelevanceRecord],
    assignment: SplitAssignment,
) -> dict[SplitName, list[RelevanceRecord]]:
    """Bucket records by the assignment produced by `compute_splits`."""
    buckets: dict[SplitName, list[RelevanceRecord]] = {"train": [], "val": [], "test": []}
    for record in records:
        key = (record.source_id, record.candidate_id)
        split = assignment.record_to_split.get(key)
        if split is None:
            continue
        buckets[split].append(record)
    return buckets
