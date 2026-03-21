# Resume Bullets

## Applied AI / Agentic AI

- Built ChaosWing, a prediction-market research and evaluation platform that converts one Polymarket URL into a shareable analyst brief, ranked related markets, causal spillover graph, and persisted experiment artifacts.
- Added persisted agent traces, benchmark exports, and experiment logging to turn graph-generation workflows into measurable AI system behavior instead of unscored UI output.
- Designed public APIs and CLI commands for benchmark reporting, dataset export, snapshot collection, and agent-eval coverage to make the system reusable outside the hosted app.

## Data Science / ML

- Built historical snapshot and label datasets for prediction markets, including `MarketSnapshot` histories, binary resolution labels, related-market ranking examples, and reviewer-aware usefulness judgments.
- Implemented a rolling expanding-window resolution backtest that compares market-implied YES probability against a logistic challenger using Brier score, log loss, calibration error, and accuracy.
- Built a related-market ranking benchmark that compares lexical overlap against a context-aware reranker and evaluates ranked results with Recall@3, NDCG@5, MRR, agreement, and label coverage.
- Added cross-venue lead-lag research exports, screened pair construction, and latency-aware paper-trade evaluation to falsify spillover hypotheses instead of hand-waving correlation claims.
