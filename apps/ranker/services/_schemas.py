"""JSONL serialization helpers for the reranker pipeline.

`chaoswing.ml._types.RelevanceRecord` defines the in-memory shape and
`to_jsonl()`. This module is the inverse: parse a JSONL line back into the
typed record so the splitter and trainer can consume the file produced by the
miner without re-deriving the schema.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import IO, Iterator

from chaoswing.ml._types import Relevance, RelevanceRecord


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def relevance_record_from_jsonl(payload: dict) -> RelevanceRecord:
    return RelevanceRecord(
        source_id=str(payload["source_id"]),
        candidate_id=str(payload["candidate_id"]),
        relevance=Relevance(int(payload["relevance"])),
        max_xcorr=float(payload.get("max_xcorr", 0.0)),
        best_lag_seconds=int(payload.get("best_lag_seconds", 0)),
        granger_p=payload.get("granger_p"),
        shock_co_fraction=float(payload.get("shock_co_fraction", 0.0)),
        source_first_seen=_parse_dt(payload.get("source_first_seen")),
        candidate_first_seen=_parse_dt(payload.get("candidate_first_seen")),
        window_start=_parse_dt(payload.get("window_start")),
        window_end=_parse_dt(payload.get("window_end")),
        event_family=str(payload.get("event_family", "")),
        negative_kind=payload.get("negative_kind", "") or "",
        extra=dict(payload.get("extra") or {}),
    )


def iter_relevance_records(handle: IO[str]) -> Iterator[RelevanceRecord]:
    """Yield typed records from an open JSONL file handle."""
    for line in handle:
        line = line.strip()
        if not line:
            continue
        yield relevance_record_from_jsonl(json.loads(line))


def write_relevance_records(records, handle: IO[str]) -> int:
    """Write records to `handle` as one JSON object per line. Returns count written."""
    count = 0
    for record in records:
        handle.write(json.dumps(record.to_jsonl(), separators=(",", ":")))
        handle.write("\n")
        count += 1
    return count
