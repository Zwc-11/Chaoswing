"""Module 1 — the label miner.

For every candidate `(source, candidate)` pair, pull both probability series
strictly before the cutoff, align them on a uniform grid, measure
co-movement (peak cross-correlation, optional Granger, shared-shock fraction),
and emit a 0-3 graded relevance record.

The miner is a thin pipeline:

    iter_pair_candidates → load_series_pair → align → measure → grade → record

The pieces are kept as small methods so each step is testable in isolation
and so the file stays well under the 1000-line ceiling.

Hard-negative sampling: the build plan calls for hard negatives drawn from
the bi-encoder top-100 (so stage 1 and stage 2 are coupled). The miner takes
a `candidate_provider` callable so that wiring lands with Module 3 without
changing this file. The default provider yields every market pair, which is
fine for bootstrap runs on a small corpus.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

import numpy as np

from chaoswing.ml._types import MarketDoc, Relevance, RelevanceRecord, TemporalCutoff, _ensure_utc
from chaoswing.ml.grading import GradingThresholds, default_thresholds, grade_co_movement
from chaoswing.ml.leakage import (
    assert_record_respects_cutoff,
    filter_array_before_cutoff,
)
from chaoswing.ml.timeseries import (
    align_series,
    cross_correlation,
    granger_causality_pvalue,
    peak_lag,
    shock_co_fraction,
    to_numpy_series,
)

from apps.ranker.services._repository import (
    DjangoSnapshotRepository,
    LabelRepository,
    SnapshotRepository,
)
from apps.ranker.services._schemas import write_relevance_records


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LabelMinerConfig:
    """Knobs for one mining run.

    `grid_freq` is the resample step the time-series alignment uses; smaller
    means more grid steps but more memory. `max_lag_steps` is the lag window
    explored on either side, in grid steps. `granger_max_lag` is the lag span
    for the Granger test; usually keep this <= `max_lag_steps`.

    `negative_easy_ratio` controls how many easy negatives we sample per
    positive when there is no `candidate_provider` returning hard negatives.
    """

    grid_freq: timedelta = timedelta(minutes=5)
    max_lag_steps: int = 12  # 12 * 5min = 1 hour either way
    granger_max_lag: int = 4
    shock_z_threshold: float = 2.0
    min_overlap_steps: int = 24
    thresholds: GradingThresholds = field(default_factory=default_thresholds)
    negative_easy_ratio: float = 1.0
    mlflow_run_name: str | None = None


CandidateProvider = Callable[[MarketDoc, Iterable[MarketDoc]], Iterator[tuple[MarketDoc, str]]]
"""A function that yields `(candidate, negative_kind)` for one source market.

`negative_kind` is one of "positive", "hard", "easy", or "" (let the grader
decide). This lets future modules (e.g. the bi-encoder in Module 3) supply
hard negatives drawn from its top-100 without changing the miner.
"""


def all_pairs_provider(source: MarketDoc, corpus: Iterable[MarketDoc]) -> Iterator[tuple[MarketDoc, str]]:
    """Default provider: every other market is a candidate, kind unset."""
    for candidate in corpus:
        if candidate.market_id == source.market_id:
            continue
        yield candidate, ""


@dataclass(slots=True)
class _Measurement:
    """Internal: the four numbers that feed the grader."""

    peak_xcorr: float
    best_lag_steps: int
    granger_p: float | None
    shock_fraction: float
    window_start: datetime | None
    window_end: datetime | None


class LabelMiner:
    """Orchestrates Module 1: mined labels for a single cutoff."""

    def __init__(
        self,
        *,
        cutoff: TemporalCutoff,
        repository: SnapshotRepository | None = None,
        config: LabelMinerConfig | None = None,
        run_id: str | None = None,
    ):
        self.cutoff = cutoff
        self.repository = repository or DjangoSnapshotRepository()
        self.config = config or LabelMinerConfig()
        self.run_id = run_id or uuid.uuid4().hex[:12]

    # ----- public API -----------------------------------------------------

    def mine(
        self,
        *,
        candidate_provider: CandidateProvider | None = None,
    ) -> list[RelevanceRecord]:
        """Run the full pipeline and return the materialized records.

        Records are validated against the cutoff before being returned. The
        caller (the management command) is responsible for writing them to
        JSONL and the DB.
        """
        provider = candidate_provider or all_pairs_provider
        corpus = list(self.repository.iter_eligible_markets(cutoff=self.cutoff))
        logger.info(
            "label_miner: cutoff=%s corpus_size=%d", self.cutoff.timestamp, len(corpus)
        )
        out: list[RelevanceRecord] = []
        for source in corpus:
            for record in self._mine_source(source, corpus, provider):
                assert_record_respects_cutoff(record, self.cutoff)
                out.append(record)
        out.extend(self._sample_easy_negatives(out, corpus))
        logger.info("label_miner: produced %d records (run_id=%s)", len(out), self.run_id)
        return out

    def write_jsonl(self, records: Sequence[RelevanceRecord], path: Path) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            return write_relevance_records(records, handle)

    def persist(self, records: Sequence[RelevanceRecord]) -> int:
        repo = LabelRepository(mining_run_id=self.run_id, cutoff=self.cutoff)
        return repo.replace_for_run(list(records))

    # ----- pipeline steps -------------------------------------------------

    def _mine_source(
        self,
        source: MarketDoc,
        corpus: Sequence[MarketDoc],
        provider: CandidateProvider,
    ) -> Iterator[RelevanceRecord]:
        source_series = self._load_series(source.market_id)
        if source_series[0].size < self.config.min_overlap_steps:
            return
        for candidate, kind in provider(source, corpus):
            candidate_series = self._load_series(candidate.market_id)
            if candidate_series[0].size < self.config.min_overlap_steps:
                continue
            measurement = self._measure(source_series, candidate_series)
            if measurement is None:
                continue
            relevance = grade_co_movement(
                peak_xcorr=measurement.peak_xcorr,
                best_lag_steps=measurement.best_lag_steps,
                granger_p=measurement.granger_p,
                shock_fraction=measurement.shock_fraction,
                thresholds=self.config.thresholds,
            )
            yield self._make_record(
                source, candidate, measurement, relevance,
                self._resolve_kind(relevance, kind),
            )

    def _load_series(self, market_id: str) -> tuple[np.ndarray, np.ndarray]:
        timestamps, values = self.repository.load_probability_series(market_id, cutoff=self.cutoff)
        ts, vals = to_numpy_series(timestamps, values)
        # Belt-and-suspenders: enforce the cutoff once more on the array side.
        return filter_array_before_cutoff(ts, vals, self.cutoff)

    def _measure(
        self,
        source_series: tuple[np.ndarray, np.ndarray],
        candidate_series: tuple[np.ndarray, np.ndarray],
    ) -> _Measurement | None:
        grid, src_v, cand_v = align_series(
            source_series,
            candidate_series,
            freq=self.config.grid_freq,
            cutoff=self.cutoff,
        )
        if grid.size < self.config.min_overlap_steps:
            return None
        lags, xcorr = cross_correlation(src_v, cand_v, max_lag=self.config.max_lag_steps)
        best_lag_grid, peak = peak_lag(lags, xcorr)
        granger_p = granger_causality_pvalue(src_v, cand_v, max_lag=self.config.granger_max_lag)
        shocks = shock_co_fraction(src_v, cand_v, z_threshold=self.config.shock_z_threshold)
        window_start = grid[0].astype("M8[us]").astype("O")
        window_end = grid[-1].astype("M8[us]").astype("O")
        return _Measurement(
            peak_xcorr=peak,
            best_lag_steps=best_lag_grid,
            granger_p=granger_p,
            shock_fraction=shocks,
            window_start=_ensure_utc(window_start) if isinstance(window_start, datetime) else None,
            window_end=_ensure_utc(window_end) if isinstance(window_end, datetime) else None,
        )

    def _make_record(
        self,
        source: MarketDoc,
        candidate: MarketDoc,
        m: _Measurement,
        relevance: Relevance,
        kind: str,
    ) -> RelevanceRecord:
        lag_seconds = int(m.best_lag_steps * self.config.grid_freq.total_seconds())
        return RelevanceRecord(
            source_id=source.market_id,
            candidate_id=candidate.market_id,
            relevance=relevance,
            max_xcorr=float(m.peak_xcorr),
            best_lag_seconds=lag_seconds,
            granger_p=m.granger_p,
            shock_co_fraction=float(m.shock_fraction),
            source_first_seen=source.first_seen,
            candidate_first_seen=candidate.first_seen,
            window_start=m.window_start,
            window_end=m.window_end,
            event_family=source.event_family or source.market_id,
            negative_kind=kind,
        )

    def _default_kind(self, relevance: Relevance) -> str:
        if relevance >= Relevance.RELATED:
            return "positive"
        if relevance == Relevance.WEAK:
            return "hard"
        return ""

    def _resolve_kind(self, relevance: Relevance, provider_hint: str) -> str:
        """Combine the grader's verdict with a provider hint.

        The grader has final say on positives: a bi-encoder-retrieved candidate
        that scores >= RELATED is a positive, not a "hard" negative. For
        sub-positive relevance the provider hint wins (so we can mark
        bi-encoder neighbors that didn't co-move as `"hard"` rather than the
        default unspecified `""`).
        """
        default = self._default_kind(relevance)
        if default == "positive":
            return "positive"
        return provider_hint or default

    # ----- easy negatives -------------------------------------------------

    def _sample_easy_negatives(
        self,
        positives: Sequence[RelevanceRecord],
        corpus: Sequence[MarketDoc],
    ) -> list[RelevanceRecord]:
        """For each source, add a small number of randomly chosen easy negatives.

        "Easy" means random distant pairs that the pipeline didn't already
        score. We don't run the time-series measurement for these — they
        balance the training set with confidently-unrelated pairs.
        """
        if self.config.negative_easy_ratio <= 0 or not positives:
            return []
        ratio = float(self.config.negative_easy_ratio)
        by_source: dict[str, list[RelevanceRecord]] = {}
        for record in positives:
            by_source.setdefault(record.source_id, []).append(record)
        corpus_by_id = {doc.market_id: doc for doc in corpus}
        rng = np.random.default_rng(seed=1729)
        easy: list[RelevanceRecord] = []
        for source_id, source_records in by_source.items():
            existing = {r.candidate_id for r in source_records}
            existing.add(source_id)
            candidates = [doc for doc_id, doc in corpus_by_id.items() if doc_id not in existing]
            if not candidates:
                continue
            n_positive = sum(1 for r in source_records if r.relevance >= Relevance.RELATED)
            n_easy = int(max(1, round(n_positive * ratio)))
            if n_easy <= 0:
                continue
            picks = rng.choice(len(candidates), size=min(n_easy, len(candidates)), replace=False)
            source_doc = corpus_by_id.get(source_id)
            for pick in picks:
                cand = candidates[int(pick)]
                easy.append(
                    RelevanceRecord(
                        source_id=source_id,
                        candidate_id=cand.market_id,
                        relevance=Relevance.UNRELATED,
                        source_first_seen=source_doc.first_seen if source_doc else None,
                        candidate_first_seen=cand.first_seen,
                        window_start=None,
                        window_end=self._safe_window_end_before_cutoff(),
                        event_family=(source_doc.event_family if source_doc else source_id) or source_id,
                        negative_kind="easy",
                    )
                )
        return easy

    def _safe_window_end_before_cutoff(self) -> datetime:
        """A window_end timestamp guaranteed to be strictly before cutoff.

        Easy negatives don't compute a real window, so we still need to mark
        them as cutoff-respecting. One microsecond before cutoff is sufficient
        and obviously synthetic.
        """
        return self.cutoff.timestamp - timedelta(microseconds=1)
