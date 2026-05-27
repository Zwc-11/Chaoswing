# ChaosWing — Self-Supervised Neural Reranker for Prediction-Market Events

ChaosWing is a two-stage neural retrieval pipeline for prediction-market events.
A **bi-encoder** retrieves the top-100 candidate markets for a source query; a
**fine-tuned cross-encoder** reranks them. Relevance labels are mined
automatically from historical implied-probability lead-lag co-movement —
**no human labeling required.** Every benchmark number lives on a strict
temporal train/test split deduplicated by event family to prevent near-twin
leakage.

**Live site:** https://chaoswing.onrender.com

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![PyTorch](https://img.shields.io/badge/PyTorch-2.x-EE4C2C?style=flat-square&logo=pytorch&logoColor=white)
![Django](https://img.shields.io/badge/Django-5.x-092E20?style=flat-square&logo=django&logoColor=white)
![Tests](https://img.shields.io/badge/tests-162%20passing-brightgreen?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)

---

## Why this is interesting

Most prediction-market rerankers rely on title similarity or hand-curated
labels. ChaosWing asks a different question: **can a market's historical
probability series tell us which other markets are economically related?**

The answer is yes, at three levels:
- **Cross-correlation**: shared price moves at a detectable lag
- **Granger causality**: one series helps predict the other (p < 0.01)
- **Shock co-fraction**: large moves in one co-occur with large moves in the other

Those signals combine into a 0–3 graded relevance label — mined
from the data, not written by hand. The cross-encoder then learns from those
labels, and a downstream **forecasting probe** tests whether the reranker's
top-k neighbors actually improve Brier score relative to a single-market
logistic baseline.

---

## Pipeline

```
   MarketSnapshot          lead-lag co-movement              stage 1                  stage 2
   probability    ─►   (cross-correlation, Granger,    ─►   bi-encoder       ─►   fine-tuned
   histories             shared-shock fraction)             retrieves top-100      cross-encoder
                                  │                                                      │
                                  ▼                                                      ▼
                          RelevanceLabel rows                                   ranked top-k
                         (graded 0-3, dedup'd by                                related markets
                          event_family)
                                  │
                                  ▼
                          temporal train/val/test
                          (strictly pre-cutoff data)
```

---

## Methods compared

Every method is scored on the **same temporal test split**. Reported via
`python manage.py run_rerank_benchmark --split <name>`.

| Method                            | Role                             | Metrics                                           |
|-----------------------------------|----------------------------------|---------------------------------------------------|
| **Fine-tuned cross-encoder**      | **Headline**                     | NDCG@5, NDCG@10, MRR, Recall@5/100, p95 latency  |
| Bi-encoder cosine (stage 1)       | Retrieval-only baseline          | Recall@100 ceiling, p95 latency                   |
| RankGPT-style listwise            | Generative LLM baseline          | NDCG@5, MRR, latency                              |
| Cohere Rerank                     | Commercial reference (eval only) | NDCG@5, MRR                                       |
| BM25                              | Lexical floor                    | NDCG@5, MRR                                       |
| Lexical overlap                   | Legacy heuristic                 | NDCG@5, MRR                                       |

Cohere is reported but is never the headline result — the headline is the
fine-tuned cross-encoder trained on self-mined labels.

---

## Leakage discipline

The most careful part of ChaosWing is preventing leakage. Random splits
would inflate results — a single event ("Fed March decision") spawns many
correlated sub-markets, so a random split leaks near-twins across train and
test.

ChaosWing's discipline:

- **Temporal splits, never random.** Train / val / test boundaries are
  cutoff timestamps; markets are bucketed by `first_seen`.
- **De-duplicated by `event_family`.** A market and its near-twin are pinned
  to the same split.
- **Strict cutoff routing.** Every label-mining, reranking, and forecasting
  call takes a `TemporalCutoff` value object. The chokepoint helpers in
  [`chaoswing/ml/leakage.py`](chaoswing/ml/leakage.py) raise `LeakageError`
  if any feature or label touches data at or after its cutoff.
- **Per-row tests.** [`tests/test_ranker_leakage.py`](tests/test_ranker_leakage.py)
  and friends fail if any emitted record's `window_end` is on or after its
  mining cutoff.

---

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/Zwc-11/Chaoswing.git
cd Chaoswing
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e .[ml]
```

The `[ml]` extra pulls in PyTorch (CUDA build), sentence-transformers,
transformers, faiss-cpu, rank-bm25, statsmodels, and cohere. Other extras:

```bash
pip install -e .[research]   # jupyter + pandas + matplotlib + seaborn
pip install -e .[mlops]      # mlflow
pip install -e .[dev]        # ruff + django-stubs + pyright
```

### 2. Configure

Copy `.env.example` to `.env` and fill in:

```env
DJANGO_SECRET_KEY=replace-with-a-long-random-secret
DJANGO_DEBUG=1
CHAOSWING_MLFLOW_TRACKING_URI=sqlite:///mlflow.db
CHAOSWING_MLFLOW_EXPERIMENT=ChaosWing
```

For the RankGPT / Cohere baselines (optional):

```env
CHAOSWING_ENABLE_LLM=1
CHAOSWING_ANTHROPIC_API_KEY=your-key
CHAOSWING_ANTHROPIC_MODEL=claude-haiku-4-5
CHAOSWING_COHERE_API_KEY=your-key   # optional, eval-only
```

### 3. Migrate and run

```bash
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000/.

---

## Run the full pipeline

After install and migrate, these six commands run the complete build order:

```bash
# 1. Mine relevance labels from lead-lag co-movement
python manage.py mine_relevance_labels --cutoff 2025-06-01T00:00:00Z

# 2. Build temporal splits deduplicated by event_family
python manage.py build_temporal_splits --name chaoswing-2025 \
    --train-cutoff 2025-03-01T00:00:00Z --val-cutoff 2025-06-01T00:00:00Z

# 3. Build the bi-encoder index and report Recall@100 on the val slice
python manage.py build_biencoder_index \
    --cutoff 2025-03-01T00:00:00Z --eval-split chaoswing-2025 --eval-on val

# 4. Fine-tune the cross-encoder on the train slice (early-stops on val NDCG@5)
python manage.py train_cross_encoder --split chaoswing-2025

# 5. Benchmark every registered method on the test slice
python manage.py run_rerank_benchmark --split chaoswing-2025 \
    --biencoder-index models/biencoder/<run>.meta.json \
    --cross-encoder models/cross_encoder/<run>

# 6. Run the forecasting probe
python manage.py run_forecasting_probe --split chaoswing-2025 \
    --method cross-encoder-finetuned \
    --cross-encoder models/cross_encoder/<run>
```

Run only cheap stateless baselines (no artifacts needed):
```bash
python manage.py run_rerank_benchmark --split chaoswing-2025 --only bm25 lexical-overlap
```

---

## Architecture

The ML pipeline is a separate Django app backed by a pure-PyTorch core that
does not import Django:

```text
apps/ranker/
├── models.py                       RelevanceLabel, RankingExample, TemporalSplit, RerankerRun
├── services/
│   ├── _registry.py                Reranker Protocol + @register decorator
│   ├── _repository.py              Django ORM access (snapshots + labels)
│   ├── label_mining.py             Module 1 — lead-lag → 0-3 grade
│   ├── biencoder.py                Module 3 — SentenceTransformers + FAISS (numpy fallback)
│   ├── cross_encoder.py            Module 4 — fine-tuned headline reranker
│   ├── listwise_llm.py             Module 5 — RankGPT (Anthropic-backed)
│   ├── baselines.py                Module 6 — BM25 / lexical / Cohere
│   ├── forecasting.py              Module 7 — forecasting probe
│   └── metrics.py                  MLflow-aware metric wrappers
└── management/commands/            mine_relevance_labels, build_temporal_splits,
                                    build_biencoder_index, train_cross_encoder,
                                    run_rerank_benchmark, run_forecasting_probe

chaoswing/ml/                       (NO Django imports — pure torch/numpy)
├── _types.py                       TemporalCutoff, Relevance, MarketDoc, ScoredCandidate
├── leakage.py                      The leakage chokepoint helpers
├── timeseries.py                   align series, cross-correlation, Granger, shocks
├── grading.py                      co-movement → 0-3 rubric
├── splits.py                       temporal split assignment, deduped by event_family
├── data.py                         torch Dataset classes
├── losses.py                       listwise softmax + pairwise RankNet
├── train.py                        cross-encoder training loop
├── eval.py                         NDCG@k, MRR, Recall@k (pure functions)
└── forecast.py                     Brier / log loss / calibration / neighbor features
```

**Design pattern:** a `Reranker` Protocol plus a decorator-based registry
(`@register("name")`) lets `run_rerank_benchmark` discover every method
without a central dispatch table. Adding a new method is one class plus one
decorator.

---

## Configuration

ChaosWing reads runtime settings from environment variables via
[`chaoswing/config.py`](chaoswing/config.py):

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | `django-insecure-chaoswing-local` | Django secret key |
| `DJANGO_DEBUG` | `1` | Development mode |
| `CHAOSWING_MLFLOW_TRACKING_URI` | `sqlite:///mlflow.db` | Local MLflow store |
| `CHAOSWING_MLFLOW_EXPERIMENT` | `ChaosWing` | Default experiment name |
| `CHAOSWING_ANTHROPIC_API_KEY` | empty | Used by RankGPT and the legacy graph reviewer |
| `CHAOSWING_ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Anthropic model identifier |
| `CHAOSWING_COHERE_API_KEY` | empty | Cohere rerank baseline (eval only) |
| `CHAOSWING_HTTP_TIMEOUT_SECONDS` | `8` | Outbound HTTP timeout |
| `CHAOSWING_LOG_LEVEL` | `INFO` | Application log level |

Training assumes a **local GPU** (bf16 if Ampere+, fp16 otherwise, fp32 CPU fallback).

---

## Testing

```bash
python manage.py check
python manage.py test
python -m compileall chaoswing apps tests
```

The ranker test suite enforces three invariants across 162 tests:

- **Leakage** ([`tests/test_ranker_leakage.py`](tests/test_ranker_leakage.py),
  [`tests/test_ranker_training_leakage.py`](tests/test_ranker_training_leakage.py))
  — no label, training row, or rerank candidate can be at or after its cutoff.
- **Method-level chokepoints** ([`tests/test_ranker_biencoder.py`](tests/test_ranker_biencoder.py),
  [`tests/test_ranker_cross_encoder.py`](tests/test_ranker_cross_encoder.py),
  [`tests/test_ranker_baselines.py`](tests/test_ranker_baselines.py),
  [`tests/test_ranker_listwise_llm.py`](tests/test_ranker_listwise_llm.py))
  — every reranker rejects a post-cutoff candidate before any model call.
- **Math** ([`tests/test_ranker_losses.py`](tests/test_ranker_losses.py),
  [`tests/test_ranker_forecast_math.py`](tests/test_ranker_forecast_math.py))
  — listwise / pairwise losses and Brier / log loss / calibration on hand-computed inputs.

The optional end-to-end training smoke test is gated by
`CHAOSWING_RUN_TRAIN_SMOKE=1` (downloads `prajjwal1/bert-tiny` from HuggingFace
on first run).

---

## Background: earlier ChaosWing surfaces

ChaosWing started as a graph-based analyst interface for prediction-market
research. Those surfaces remain in the repo and on the live site but are not
the project headline:

- **Graph workspace** (`/app/`) — Cytoscape-rendered spillover graphs around a
  Polymarket event URL.
- **Briefs** (`/briefs/<uuid>/`) — shareable analyst briefs with strongest-path
  summaries, ranked adjacent markets, and evidence.
- **Watchlists** (`/watchlists/`) — curated macro / commodity / politics
  watchlists for repeat sessions.
- **Cross-venue lead-lag** (`/lead-lag/`) — Polymarket × Kalshi pair
  construction, live tick collection, and a paper-trade ledger. The same
  lead-lag math powers the reranker's label factory.
- **Resolution forecasting** — expanding-window logistic backtest, reused by
  the Module 7 forecasting probe.
- **Agent traces + benchmarks** — `/benchmarks/` and `/developers/api/`
  expose older evaluation tracks (related-market ranking, reviewer-aware
  usefulness, agent trust, graph quality).

---

## Documentation

- [docs/architecture.md](docs/architecture.md)
- [docs/frontend-architecture.md](docs/frontend-architecture.md)
- [docs/api-contracts.md](docs/api-contracts.md)
- [docs/design-principles.md](docs/design-principles.md)
- [docs/benchmark-methodology.md](docs/benchmark-methodology.md)
- [Ranker.md](Ranker.md) — leakage discipline + module-specific rules
- [notebooks/README.md](notebooks/README.md)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

ChaosWing is released under the MIT License. See [LICENSE](LICENSE).
