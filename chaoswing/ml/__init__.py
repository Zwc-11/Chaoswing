"""Pure PyTorch / numpy ML core for ChaosWing.

This package must not import Django. It exists so that model, training, and
evaluation code is portable, unit-testable, and callable from management
commands without the Django settings machinery.
"""
from __future__ import annotations

from chaoswing.ml._types import (
    MarketDoc,
    Relevance,
    RelevanceRecord,
    ScoredCandidate,
    SplitName,
    TemporalCutoff,
)
from chaoswing.ml.leakage import LeakageError

__all__ = [
    "LeakageError",
    "MarketDoc",
    "Relevance",
    "RelevanceRecord",
    "ScoredCandidate",
    "SplitName",
    "TemporalCutoff",
]
