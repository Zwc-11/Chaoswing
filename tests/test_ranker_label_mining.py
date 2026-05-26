"""End-to-end test for `LabelMiner` against an in-memory fake repository.

This sidesteps the Django ORM so the miner's leakage handling and grading
pipeline can be exercised in isolation. The fake corpus is two correlated
series + one unrelated series; we assert that the miner:

  * drops all observations >= cutoff from each market's series,
  * emits records whose `window_end` is strictly before the cutoff, and
  * assigns the strongest relevance grade to the genuinely correlated pair.
"""
from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from chaoswing.ml._types import MarketDoc, Relevance, TemporalCutoff

from apps.ranker.services.label_mining import LabelMiner, LabelMinerConfig


CUTOFF_TS = datetime(2025, 6, 1, tzinfo=timezone.utc)
CUTOFF = TemporalCutoff(timestamp=CUTOFF_TS, label="test_cutoff")
SERIES_START = CUTOFF_TS - timedelta(hours=24)
GRID = timedelta(minutes=5)


def _build_series(values: list[float], *, start: datetime = SERIES_START) -> tuple[list[datetime], list[float]]:
    timestamps = [start + i * GRID for i in range(len(values))]
    return timestamps, values


def _correlated_pair(n: int) -> tuple[list[float], list[float]]:
    """Two strongly co-moving series: `b` follows `a` with one-step lag + noise."""
    import math

    a = [0.5 + 0.4 * math.sin(i / 6.0) for i in range(n)]
    b = [0.5] + [a[i - 1] + 0.02 * math.sin(i) for i in range(1, n)]
    return a, b


def _unrelated_series(n: int) -> list[float]:
    import math

    return [0.5 + 0.4 * math.cos(i / 11.0 + 1.7) for i in range(n)]


@dataclass(slots=True)
class _MarketFixture:
    doc: MarketDoc
    timestamps: list[datetime]
    values: list[float]


class _FakeSnapshotRepository:
    """Minimal `SnapshotRepository` for tests — pure in-memory."""

    def __init__(self, markets: Iterable[_MarketFixture]):
        self._markets = {m.doc.market_id: m for m in markets}

    def iter_eligible_markets(self, *, cutoff: TemporalCutoff):
        for fixture in self._markets.values():
            yield fixture.doc

    def load_probability_series(self, market_id: str, *, cutoff: TemporalCutoff):
        fixture = self._markets[market_id]
        ts = []
        vals = []
        for t, v in zip(fixture.timestamps, fixture.values, strict=True):
            if t < cutoff.timestamp:
                ts.append(t)
                vals.append(v)
        return ts, vals

    def first_seen(self, market_id: str):
        return self._markets[market_id].timestamps[0]


def _make_fixture(market_id: str, values: list[float], *, family: str = "") -> _MarketFixture:
    ts, vs = _build_series(values)
    doc = MarketDoc(
        market_id=market_id,
        title=market_id.replace("_", " ").title(),
        description="",
        category="test",
        first_seen=ts[0],
        event_family=family or market_id,
    )
    return _MarketFixture(doc=doc, timestamps=ts, values=vs)


class LabelMinerEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        # 300 ticks * 5min = 25 hours of data; the last 1h falls past the cutoff.
        n = 300
        a_vals, b_vals = _correlated_pair(n)
        c_vals = _unrelated_series(n)
        # Push the last 12 ticks past the cutoff to verify they get dropped.
        post_cutoff = CUTOFF_TS + timedelta(minutes=5)
        a_after = a_vals + [0.99] * 12
        b_after = b_vals + [0.99] * 12
        c_after = c_vals + [0.01] * 12
        a_ts = [SERIES_START + i * GRID for i in range(n)] + [
            post_cutoff + i * GRID for i in range(12)
        ]
        b_ts = list(a_ts)
        c_ts = list(a_ts)

        self.markets = [
            _MarketFixture(
                doc=MarketDoc(
                    market_id="market_a",
                    title="Market A",
                    first_seen=a_ts[0],
                    event_family="market_a",
                ),
                timestamps=a_ts,
                values=a_after,
            ),
            _MarketFixture(
                doc=MarketDoc(
                    market_id="market_b",
                    title="Market B",
                    first_seen=b_ts[0],
                    event_family="market_b",
                ),
                timestamps=b_ts,
                values=b_after,
            ),
            _MarketFixture(
                doc=MarketDoc(
                    market_id="market_c",
                    title="Market C",
                    first_seen=c_ts[0],
                    event_family="market_c",
                ),
                timestamps=c_ts,
                values=c_after,
            ),
        ]
        self.repo = _FakeSnapshotRepository(self.markets)

    def test_records_respect_cutoff(self) -> None:
        miner = LabelMiner(
            cutoff=CUTOFF,
            repository=self.repo,
            config=LabelMinerConfig(negative_easy_ratio=0.0),
            run_id="test_run",
        )
        records = miner.mine()
        self.assertGreater(len(records), 0)
        for record in records:
            self.assertIsNotNone(record.window_end)
            self.assertLess(record.window_end, CUTOFF_TS)

    def test_correlated_pair_gets_high_grade(self) -> None:
        miner = LabelMiner(
            cutoff=CUTOFF,
            repository=self.repo,
            config=LabelMinerConfig(negative_easy_ratio=0.0),
            run_id="test_run",
        )
        records = miner.mine()
        pair_ab = [
            r for r in records
            if (r.source_id, r.candidate_id) == ("market_a", "market_b")
        ]
        self.assertEqual(len(pair_ab), 1)
        self.assertGreaterEqual(int(pair_ab[0].relevance), int(Relevance.WEAK))
        self.assertGreater(abs(pair_ab[0].max_xcorr), 0.3)

    def test_unrelated_pair_gets_low_grade(self) -> None:
        miner = LabelMiner(
            cutoff=CUTOFF,
            repository=self.repo,
            config=LabelMinerConfig(negative_easy_ratio=0.0),
            run_id="test_run",
        )
        records = miner.mine()
        pair_ac = [
            r for r in records
            if (r.source_id, r.candidate_id) == ("market_a", "market_c")
        ]
        self.assertEqual(len(pair_ac), 1)
        self.assertLess(int(pair_ac[0].relevance), int(Relevance.STRONG))

    def test_easy_negatives_are_added_when_requested(self) -> None:
        """Easy negatives need *unmeasured* candidates to exist in the corpus.

        With only 3 markets every pair is measured, so we pad the corpus with
        two extra short series. Those don't pass the `min_overlap_steps`
        filter, so they never become measured pairs but they *do* live in the
        corpus dictionary the easy-negative sampler scans.
        """
        # Two padding markets the miner won't measure (too few observations).
        padding_ts = [SERIES_START + i * GRID for i in range(5)]
        padded_markets = list(self.markets) + [
            _MarketFixture(
                doc=MarketDoc(
                    market_id="pad_a",
                    title="Pad A",
                    first_seen=padding_ts[0],
                    event_family="pad_a",
                ),
                timestamps=padding_ts,
                values=[0.5] * 5,
            ),
            _MarketFixture(
                doc=MarketDoc(
                    market_id="pad_b",
                    title="Pad B",
                    first_seen=padding_ts[0],
                    event_family="pad_b",
                ),
                timestamps=padding_ts,
                values=[0.5] * 5,
            ),
        ]
        repo = _FakeSnapshotRepository(padded_markets)
        miner = LabelMiner(
            cutoff=CUTOFF,
            repository=repo,
            config=LabelMinerConfig(negative_easy_ratio=1.0),
            run_id="test_run",
        )
        records = miner.mine()
        easy = [r for r in records if r.negative_kind == "easy"]
        self.assertGreater(len(easy), 0)
        for record in easy:
            self.assertEqual(int(record.relevance), int(Relevance.UNRELATED))
            self.assertIsNotNone(record.window_end)
            self.assertLess(record.window_end, CUTOFF_TS)
