# Rules: ranker / ML core (apps/ranker, chaoswing/ml)

These apply whenever you touch `apps/ranker/` or `chaoswing/ml/`.

## Leakage discipline (the spine)
- Every label row records `source_first_seen`, `candidate_first_seen`, `window_start`, `window_end`,
  and `event_family`. Co-movement is computed **only** within `[window_start, window_end]`, all before cutoff.
- **Train:** both markets existed with >= N observations before train cutoff `T`; co-movement on data < `T`.
- **Test:** source markets with `first_seen > T` (unseen futures). Never let a test source's family appear in train.
- **Split by `event_family`, not by row.** A market and its near-twin must land on the same side.
- **Forecasting probe:** at forecast time `t`, use only data with timestamp `< t`.
- Add/keep a unit test that fails if any feature or label touches data at/after its cutoff.

## Label miner (`services/label_mining.py`)
- Reuse the existing lead-lag math (cross-correlation / Granger / shock co-movement). Do not rewrite it.
- Graded 0-3: 3 = significant lead-lag at non-zero lag AND Granger p<0.01; 2 = significant contemporaneous;
  1 = weak/shock-only; 0 = negative sample. Thresholds are config constants — document each.
- **Negatives matter most.** Draw hard negatives from the bi-encoder top-100 (same-category, no co-movement)
  ~1:1 with easy random negatives. This couples stage 1 and stage 2 — do not skip it.

## Cross-encoder (`chaoswing/ml/train.py`, `losses.py`)
- Init from a pretrained backbone (deberta-v3-base or ms-marco MiniLM) and **full-fine-tune all layers**
  with a fresh head. No frozen linear probe. No random init.
- Input: `[CLS] source question [SEP] candidate question [SEP]`. Text-first.
- Prefer a **listwise (softmax CE over per-source candidate list)** or pairwise (RankNet) ranking loss
  over plain 4-class CE — it optimizes NDCG directly and is the stronger result.
- bf16 on Ampere+; AdamW lr 2e-5, warmup, 2-4 epochs, early-stop on val NDCG@5, checkpoint best to `models/`.

## Metrics (`services/metrics.py`, `chaoswing/ml/eval.py`)
- Implement NDCG@5, MRR, Recall@100, p95 latency as pure functions with unit tests.
- Report every method on the same temporal test set. Cohere is eval-only and never headlined.

## Don't
- Don't import Django from `chaoswing/ml/`.
- Don't add new product surfaces, endpoints, or dashboards from here.
- Don't tune thresholds on the test set. Use a held-out validation slice.