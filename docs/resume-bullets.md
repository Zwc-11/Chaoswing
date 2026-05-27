# Resume Bullets

## ChaosWing: Self-Supervised Neural Reranker for Prediction-Market Events

**Stack:** Python, PyTorch, SentenceTransformers, FAISS, transformers, DuckDB, MLflow, Django.

- Built a two-stage neural retrieval system that embeds prediction-market questions with a bi-encoder, retrieves top-100 candidates, and reranks them with a fine-tuned cross-encoder transformer.
- Mined self-supervised 0–3 relevance labels from historical implied-probability series using lead-lag co-movement, learning economic event relationships without human labeling.
- Benchmarked against BM25, embedding cosine similarity, RankGPT-style listwise reranking, and commercial reranking APIs using NDCG@5, MRR, Recall@100, and p95 latency.
- Used strict temporal train/test splits to prevent event leakage, then ran a forecasting probe measuring whether top-k related markets improved Brier score, log loss, and calibration versus single-market baselines.

### One-liner on leakage (interview-ready)

The part I was most careful about was leakage. I used temporal train/test splits, because random splits would make the project look better than it really is — the same event spawns many correlated sub-markets, so a random split leaks near-twins across train and test. I split by event family on a time cutoff, mined labels only from data before the cutoff, and the forecasting probe only uses information available before each forecast timestamp.

## Earlier ChaosWing work (analyst interface + research tracks)

These bullets describe the platform layer that preceded the neural reranker rebuild. They remain in the repo but are not the project headline.

- Built ChaosWing, a prediction-market research platform that converts one Polymarket URL into a shareable analyst brief, ranked related markets, causal spillover graph, and persisted experiment artifacts.
- Implemented a rolling expanding-window resolution backtest comparing market-implied YES probability against a logistic challenger using Brier score, log loss, calibration error, and accuracy.
- Built a related-market ranking benchmark comparing lexical overlap against a context-aware reranker, evaluated with Recall@3, NDCG@5, MRR, and reviewer-aware usefulness labels.
- Added cross-venue Polymarket × Kalshi lead-lag research exports, screened pair construction, and latency-aware paper-trade evaluation to falsify spillover hypotheses rather than hand-wave correlation.
- Designed public APIs and CLI commands for benchmark reporting, dataset export, snapshot collection, and agent-eval coverage so the system is reusable outside the hosted app.
