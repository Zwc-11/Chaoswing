from __future__ import annotations

"""Machine learning hooks and prediction service stub for ChaosWing.

This module provides:
1. Data collection hooks that log graph runs into structured training data
2. A prediction confidence scorer stub for future model integration
3. Feature extraction utilities for graph payloads

When you're ready to add ML:
- Implement PredictionModel with your trained model (scikit-learn, PyTorch, etc.)
- Wire GraphRunDataCollector into the workflow to accumulate labeled examples
- Use FeatureExtractor to build feature vectors from graph payloads
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("apps.web.services.ml_hooks")


# ---------------------------------------------------------------------------
# Feature extraction from graph payloads
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class GraphFeatures:
    """Numeric feature vector extracted from a graph payload for ML scoring."""

    node_count: int = 0
    edge_count: int = 0
    related_market_count: int = 0
    evidence_count: int = 0
    hypothesis_count: int = 0
    entity_count: int = 0
    avg_node_confidence: float = 0.0
    avg_edge_confidence: float = 0.0
    min_edge_confidence: float = 0.0
    max_edge_confidence: float = 0.0
    graph_density: float = 0.0
    event_volume: float = 0.0
    event_liquidity: float = 0.0
    related_market_volume_ratio: float = 0.0
    has_llm_expansion: bool = False
    edge_type_diversity: int = 0

    def as_vector(self) -> list[float]:
        return [
            float(self.node_count),
            float(self.edge_count),
            float(self.related_market_count),
            float(self.evidence_count),
            float(self.hypothesis_count),
            float(self.entity_count),
            self.avg_node_confidence,
            self.avg_edge_confidence,
            self.min_edge_confidence,
            self.max_edge_confidence,
            self.graph_density,
            self.event_volume,
            self.event_liquidity,
            self.related_market_volume_ratio,
            1.0 if self.has_llm_expansion else 0.0,
            float(self.edge_type_diversity),
        ]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class FeatureExtractor:
    """Extracts ML features from ChaosWing graph payloads."""

    def extract(self, payload: dict[str, Any]) -> GraphFeatures:
        nodes = payload.get("graph", {}).get("nodes", [])
        edges = payload.get("graph", {}).get("edges", [])
        event = payload.get("event", {})
        run = payload.get("run", {})

        node_confidences = [self._parse_float(n.get("confidence")) for n in nodes]
        edge_confidences = [self._parse_float(e.get("confidence")) for e in edges]
        edge_types = {e.get("type") for e in edges if e.get("type")}

        type_counts = {}
        for node in nodes:
            t = node.get("type", "")
            type_counts[t] = type_counts.get(t, 0) + 1

        n = len(nodes)
        e = len(edges)
        max_edges = n * (n - 1) if n > 1 else 1

        event_volume = self._parse_float(event.get("volume"))
        related_volumes = [
            self._parse_float(node.get("volume"))
            for node in nodes
            if node.get("type") == "RelatedMarket"
        ]
        total_related_volume = sum(related_volumes)

        return GraphFeatures(
            node_count=n,
            edge_count=e,
            related_market_count=type_counts.get("RelatedMarket", 0),
            evidence_count=type_counts.get("Evidence", 0),
            hypothesis_count=type_counts.get("Hypothesis", 0),
            entity_count=type_counts.get("Entity", 0),
            avg_node_confidence=sum(node_confidences) / len(node_confidences) if node_confidences else 0.0,
            avg_edge_confidence=sum(edge_confidences) / len(edge_confidences) if edge_confidences else 0.0,
            min_edge_confidence=min(edge_confidences) if edge_confidences else 0.0,
            max_edge_confidence=max(edge_confidences) if edge_confidences else 0.0,
            graph_density=e / max_edges if max_edges else 0.0,
            event_volume=event_volume,
            event_liquidity=self._parse_float(event.get("liquidity")),
            related_market_volume_ratio=total_related_volume / event_volume if event_volume else 0.0,
            has_llm_expansion=run.get("mode") == "agent-enriched",
            edge_type_diversity=len(edge_types),
        )

    def _parse_float(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


# ---------------------------------------------------------------------------
# Data collection for future model training
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TrainingExample:
    run_id: str
    features: GraphFeatures
    quality_score: float
    mode: str
    timestamp: str
    labels: dict[str, Any] = field(default_factory=dict)


class GraphRunDataCollector:
    """Collects structured training data from graph runs.

    Data is saved to a JSONL file for offline analysis and model training.
    """

    def __init__(self, output_dir: Path | None = None):
        self.output_dir = output_dir or Path("ml_data")
        self.feature_extractor = FeatureExtractor()

    def collect(self, payload: dict[str, Any]) -> TrainingExample:
        run = payload.get("run", {})
        review = run.get("review", {})

        features = self.feature_extractor.extract(payload)
        quality_score = self._parse_float(review.get("quality_score", 0.0))

        example = TrainingExample(
            run_id=str(run.get("id") or ""),
            features=features,
            quality_score=quality_score,
            mode=str(run.get("mode") or ""),
            timestamp=datetime.now(tz=UTC).isoformat(),
            labels={
                "approved": review.get("approved", False),
                "issue_count": len(review.get("issues", [])),
            },
        )

        self._persist(example)
        return example

    def _persist(self, example: TrainingExample) -> None:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path = self.output_dir / "training_data.jsonl"
            record = {
                "run_id": example.run_id,
                "features": example.features.as_dict(),
                "feature_vector": example.features.as_vector(),
                "quality_score": example.quality_score,
                "mode": example.mode,
                "timestamp": example.timestamp,
                "labels": example.labels,
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.warning("Failed to persist ML training example", exc_info=True)

    def _parse_float(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


# ---------------------------------------------------------------------------
# Prediction model stub — replace with your trained model
# ---------------------------------------------------------------------------

class PredictionModel:
    """Stub prediction model for graph quality scoring.

    Replace this with a trained scikit-learn / PyTorch model when ready.
    The interface is intentionally simple: features in, score out.
    """

    def __init__(self, model_path: Path | None = None):
        self.model = None
        self.model_path = model_path
        if model_path and model_path.exists():
            self._load_model(model_path)

    def predict_quality(self, features: GraphFeatures) -> float:
        """Predict the quality score for a graph based on its features.

        Returns a score between 0.0 and 1.0.
        """
        if self.model is not None:
            return self._model_predict(features)
        return self._heuristic_score(features)

    def _heuristic_score(self, features: GraphFeatures) -> float:
        """Rule-based scoring until a trained model is available."""
        score = 0.5

        if features.node_count >= 8:
            score += 0.1
        elif features.node_count >= 12:
            score += 0.15

        if features.related_market_count >= 2:
            score += 0.08
        if features.evidence_count >= 2:
            score += 0.07
        if features.hypothesis_count >= 1:
            score += 0.05

        if features.avg_edge_confidence > 0.7:
            score += 0.05
        if features.edge_type_diversity >= 4:
            score += 0.05
        if features.graph_density > 0.1:
            score += 0.03
        if features.has_llm_expansion:
            score += 0.08

        return round(min(score, 0.95), 2)

    def _load_model(self, path: Path) -> None:
        """Load a serialized model. Override for your framework."""
        try:
            import pickle
            with open(path, "rb") as f:
                self.model = pickle.load(f)  # noqa: S301
            logger.info("Loaded ML model from %s", path)
        except Exception:
            logger.warning("Could not load ML model from %s", path, exc_info=True)
            self.model = None

    def _model_predict(self, features: GraphFeatures) -> float:
        """Run the loaded model. Override for your framework."""
        try:
            vector = [features.as_vector()]
            prediction = self.model.predict(vector)
            return float(prediction[0])
        except Exception:
            logger.warning("Model prediction failed, using heuristic", exc_info=True)
            return self._heuristic_score(features)
