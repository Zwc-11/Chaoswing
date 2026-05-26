# ChaosWing Rebuild Plan — Self-Supervised Neural Reranker

**Target:** A self-supervised transformer reranker that learns event-market relatedness from historical implied-probability co-movement, evaluated with leakage-safe ranking metrics and a forecasting probe.

**Tuned to your choices:** refactor in place (keep Django), train on a local GPU, fine-tune the cross-encoder for the strongest resume signal.

---

## 0. The reframe

The whole rebuild rests on one move: **your lead-lag engine stops being an arbitrage tool and becomes a label factory.**

Today `apps/web` runs lead-lag co-movement to chase cross-venue arbitrage (Polymarket vs Kalshi, paper trades). You already have the hard part — code that measures whether two probability series move together with a lead. Repoint it: a pair with statistically significant lead-lag co-movement becomes a *graded relevance label*. Those labels train a neural reranker. The reranker is scored with ranking metrics under temporal splits. A forecasting probe checks whether the markets it surfaces actually improve Brier/log loss.

Nothing in the headline is human-labeled or lexical anymore. That is the entire point.

---

## 1. What you keep, repoint, and demote

### Reuse nearly as-is
- `apps/web/services/polymarket.py` — ingestion + `MarketSnapshot` probability series. This is your corpus and your co-movement input.
- Cross-venue lead-lag math (correlation / Granger / shock co-movement) — becomes the label miner core.
- Resolution backtest (Brier / log loss / calibration, expanding-window logistic) — becomes the forecasting probe almost verbatim.
- MLflow wiring, `ml_data/*.jsonl` export pattern, NDCG@5/MRR metric code, p95 latency from agent traces.

### Repoint (change purpose, keep plumbing)
- Related-market ranking: the lexical-overlap baseline and context-aware heuristic stay **as baselines only**. The neural pipeline becomes the headline ranker.

### Demote out of the headline (keep in repo, drop from README top + resume)
Briefs, watchlists, agent-trust benchmark, graph-quality scorer, cross-venue **paper trading**, CatBoost (→ baseline only). Per your plan doc: no kitchen sink, no fake "predict the future" transformer, Cohere lives *inside evaluation* and never in the headline.

---

## 2. New structure (refactor in place)

Add one Django app for the orchestration + a pure-torch package so model code has no Django import (cleaner, testable, portable).

```
chaoswing/
├── apps/
│   ├── web/                         # existing; demoted surfaces live here untouched
│   └── ranker/                      # NEW Django app — the ML core
│       ├── models.py                # RelevanceLabel, RankingExample, TemporalSplit, RerankerRun
│       ├── migrations/
│       ├── services/
│       │   ├── label_mining.py      # lead-lag → graded 0–3 labels  (wraps existing lead-lag math)
│       │   ├── biencoder.py         # SentenceTransformers retrieval (stage 1)
│       │   ├── cross_encoder.py     # fine-tuned reranker inference (stage 2)
│       │   ├── listwise_llm.py      # RankGPT-style baseline (reuses anthropic_agent)
│       │   ├── baselines.py         # BM25, cosine, lexical, Cohere Rerank
│       │   └── metrics.py           # ndcg@k, mrr, recall@k, latency helpers
│       └── management/commands/
│           ├── mine_relevance_labels.py
│           ├── build_temporal_splits.py
│           ├── build_biencoder_index.py
│           ├── train_cross_encoder.py
│           ├── run_rerank_benchmark.py
│           └── run_forecasting_probe.py
├── chaoswing/
│   └── ml/                          # NEW pure-python/torch package (NO django imports)
│       ├── data.py                  # Dataset classes + temporal split logic
│       ├── losses.py                # pairwise / listwise ranking losses
│       ├── train.py                 # the torch training loop (called by mgmt cmd)
│       └── eval.py                  # metric computation on raw arrays
├── models/                          # saved checkpoints — gitignore this
└── ml_data/                         # existing + new jsonl exports
```

Everything is a `manage.py` command, matching your existing style. New deps go behind a `[ml]` extra in `pyproject.toml`:

```
torch  (CUDA build for your GPU)
sentence-transformers
transformers
faiss-cpu            # cpu index is plenty at this corpus size
rank-bm25
statsmodels          # likely already present for lead-lag (Granger)
cohere               # optional, eval-only
```

---

## 3. Module 1 — Label miner (the self-supervision)

**File:** `apps/ranker/services/label_mining.py`
**Command:** `python manage.py mine_relevance_labels`

For every candidate `(source_market, candidate_market)` pair, pull both implied-probability series from `MarketSnapshot`, align on a common time grid, and grade co-movement **0–3**.

### Grading rubric (concrete)
| Label | Meaning | Trigger |
|---|---|---|
| 3 | Strong related | Significant lead-lag: peak cross-correlation above threshold at non-zero lag **and** Granger p < 0.01 (or survives a shock co-movement test) |
| 2 | Related | Significant contemporaneous correlation, weaker/shorter lag evidence |
| 1 | Weak | Correlation present but not significant, or only co-moves during shared shocks |
| 0 | Unrelated | No detectable co-movement (negative sample) |

Tune the exact thresholds on a held-out slice and **write them down** — being able to defend "why 0.01" matters in interviews more than the number itself.

### Negative sampling (do not skip)
A reranker is only as good as its hard negatives.
- **Hard negatives:** same category / overlapping vocabulary but zero co-movement → these teach the model that lexical similarity ≠ economic relatedness. This is your strongest story.
- **Easy negatives:** random distant pairs.
Mix roughly 1:1 hard:easy. Critically, draw hard negatives from the **bi-encoder's top-100** (Module 3) so stage 1 and stage 2 are coupled — the reranker trains on exactly the confusable candidates it will see at inference.

### Output schema (`ml_data/relevance_labels.jsonl`)
```json
{"source_id": "...", "candidate_id": "...", "relevance": 3,
 "max_xcorr": 0.71, "best_lag_min": 35, "granger_p": 0.004,
 "source_first_seen": "2025-01-10T...", "candidate_first_seen": "2024-11-02T...",
 "window_start": "...", "window_end": "...", "event_family": "fed-rates-2025"}
```
`event_family` is the de-dup key for splitting (Module 2). `window_end` proves the label used no future data.

---

## 4. Module 2 — Temporal splits (the spine)

**File:** `chaoswing/ml/data.py` + command `build_temporal_splits`

This is the part that protects you in interviews. Three rules:

1. **Train labels:** both markets in the pair existed and had ≥ N probability observations *before* the train cutoff `T`. Co-movement computed only on data with timestamp < `T`.
2. **Test set:** source markets whose `first_seen > T` (future, never seen in training). Relevance measured on their own post-cutoff windows.
3. **Forecasting probe:** at forecast timestamp `t`, only use probability data with timestamp < `t`.

**De-dup by event family.** Random splits leak because one event ("Fed March decision") spawns many correlated sub-markets; a market and its near-twin would land on both sides. Split by `event_family`, not by row.

The single sentence to repeat everywhere: *"I used temporal splits instead of random splits, because random splits would leak event clusters across train and test."*

---

## 5. Module 3 — Bi-encoder retriever (stage 1)

**File:** `apps/ranker/services/biencoder.py`
**Command:** `build_biencoder_index`

Embed every market (question + short description) with a SentenceTransformers model (`BAAI/bge-small-en-v1.5` or `all-MiniLM-L6-v2`), build a FAISS index over markets that existed before the relevant cutoff, retrieve **top-100** by cosine.

- This replaces lexical overlap as stage 1.
- **Recall@100** is the metric that judges this stage — it caps everything downstream.
- *Optional upgrade:* fine-tune the bi-encoder with `MultipleNegativesRankingLoss` on label≥2 positives. Worth a sentence on the resume, but the cross-encoder is the headline — don't let this eat your time.

---

## 6. Module 4 — Cross-encoder reranker (the headline, fine-tuned on local GPU)

**Files:** `chaoswing/ml/train.py`, `losses.py`; inference in `apps/ranker/services/cross_encoder.py`
**Command:** `train_cross_encoder`

### "From scratch-ish," done right
Truly random-init is a weak, slow story. The strong-signal version that still sounds serious: **initialize from a pretrained encoder and full-fine-tune every layer** (not a frozen linear probe) with a **fresh ranking head** over your mined labels. You say in interviews: *"I fine-tuned the full encoder end-to-end on labels I mined myself, not a zero-shot off-the-shelf reranker."* That is the honest, strong framing.

- **Backbone:** `microsoft/deberta-v3-base` (strongest single-GPU option) or `cross-encoder/ms-marco-MiniLM-L-6-v2` (faster, lighter).
- **Input:** `[CLS] source question [SEP] candidate question [SEP]`. Keep it text-first to protect the NLP story; you can append a category/age token later if it helps.
- **Loss (this is the sophisticated choice):** optimize ranking directly, not just classification. Use a **pairwise (RankNet)** or **listwise (softmax cross-entropy over the per-source candidate list)** loss on the graded labels. Listwise tends to win on NDCG@5 and is the better interview answer than "I did 4-class cross-entropy."

### Local-GPU training config (starting point)
```
optimizer      AdamW, lr 2e-5, weight decay 0.01
schedule       linear warmup 10%, then decay
precision      bf16 (Ampere+) or fp16
batch          16–32 pairs; grad-accum if VRAM-limited
epochs         2–4, early-stop on val NDCG@5
checkpoint     save best-NDCG to models/cross_encoder/
```
Hard negatives come from Module 1 (the no-co-movement pairs inside the bi-encoder top-100). Log everything to local MLflow so the benchmark board has real curves.

---

## 7. Module 5 — RankGPT-style listwise LLM reranker (generative baseline)

**File:** `apps/ranker/services/listwise_llm.py` — reuses your `anthropic_agent.py` wiring.

Prompt the LLM with the source market + a numbered candidate list; ask for a reordered permutation. Use a sliding window when candidates > ~20. This is your **generative / autoregressive baseline** — it gives you the LLM-reranking angle without building a fake "market prediction transformer." Frame it honestly as a baseline you beat (or trade off against on latency), not the trained model.

---

## 8. Module 6 — Baselines + evaluation contract

**File:** `apps/ranker/services/baselines.py`, `metrics.py`
**Command:** `run_rerank_benchmark`

Score every method on the **same temporal test set**:

| Method | Role |
|---|---|
| BM25 (titles+desc) | lexical floor |
| Embedding cosine (bi-encoder, no rerank) | retrieval-only |
| Lexical overlap (existing heuristic) | your old baseline |
| **Fine-tuned cross-encoder** | **headline** |
| RankGPT-style listwise | generative baseline |
| Cohere Rerank API | commercial reference — eval only |

**Metrics:** NDCG@5, MRR, Recall@100, p95 latency per query.

**Resume framing (verbatim from your plan):** "Benchmarked against BM25, embedding cosine retrieval, a RankGPT-style listwise reranker, and commercial reranking APIs." Cohere stays a quiet interview detail, never the headline — you trained your own model.

---

## 9. Module 7 — Forecasting probe (secondary)

**File:** reuse resolution backtest; **command:** `run_forecasting_probe`

For each resolved source market: build the baseline forecast from its own probability history, then a challenger that adds aggregated signals (probability level / momentum at time `t`) from the **top-k related markets your reranker surfaced**. Fit the existing expanding-window logistic. Compare **Brier, log loss, calibration**. Strictly pre-`t` data only. Report lift.

This answers "do the related markets actually matter?" without becoming a forecasting project. Keep it small and secondary.

---

## 10. Build order

Dependency-ordered so nothing blocks:

1. **Module 1 + 2 together** — label miner emitting `event_family`, first/last-seen, window bounds; temporal split logic. Without leakage-safe labels, every later number is meaningless. Start here.
2. **Module 3** — bi-encoder index + Recall@100. Confirms stage 1 ceiling and produces hard negatives.
3. **Module 4** — cross-encoder fine-tune on local GPU. The headline. Iterate loss (pairwise → listwise) and watch val NDCG@5.
4. **Module 6** — wire up baselines + benchmark table so you can see lift over BM25/cosine/lexical.
5. **Module 5** — RankGPT baseline (cheap, reuses Anthropic).
6. **Module 7** — forecasting probe last.
7. **Cleanup** — demote briefs/watchlists/agent-trust/graph-quality/paper-trading from README top; rewrite the README headline around the reranker; update resume bullets.

A defensible milestone after step 4: *"trained reranker beats BM25 / cosine / lexical on NDCG@5 and MRR under a temporal split."* That alone is the project. Everything after is polish.

---

## 11. Resume bullets (final, mapped to this code)

> **ChaosWing: Self-Supervised Neural Reranker for Prediction-Market Events**
> Python, PyTorch, SentenceTransformers, DuckDB, MLflow, Django
>
> - Built a two-stage neural retrieval system that embeds prediction-market questions with a bi-encoder, retrieves top-100 candidates, and reranks them with a fine-tuned cross-encoder transformer.
> - Mined self-supervised 0–3 relevance labels from historical implied-probability series using lead-lag co-movement, learning economic event relationships without human labeling.
> - Benchmarked against BM25, embedding cosine similarity, RankGPT-style listwise reranking, and commercial reranking APIs using NDCG@5, MRR, Recall@100, and p95 latency.
> - Used strict temporal train/test splits to prevent event leakage, then ran a forecasting probe measuring whether top-k related markets improved Brier score, log loss, and calibration versus single-market baselines.

---

## 12. Interview one-liner on leakage (memorize)

"The part I was most careful about was leakage. I used temporal train/test splits, because random splits would make the project look better than it really is — the same event spawns many correlated sub-markets, so a random split leaks near-twins across train and test. I split by event family on a time cutoff, mined labels only from data before the cutoff, and the forecasting probe only uses information available before each forecast timestamp."
