# ChaosWing — Claude Code Project Memory

## What we're building
Refocusing ChaosWing into a **self-supervised neural reranker for prediction-market events**:
a bi-encoder retrieves top-100 candidate markets, a fine-tuned cross-encoder reranks them,
and the relevance labels are **mined from historical implied-probability lead-lag co-movement** —
no human labeling. Evaluated with leakage-safe ranking metrics + a forecasting probe.

**The full build plan is `docs/chaoswing-rebuild-plan.md`. Read it before starting any module.**
This file is the constitution; that file is the spec.

## Golden rules (non-negotiable)
1. **No leakage, ever.** Labels and features use only data *before* the relevant cutoff/forecast timestamp.
   Splits are **temporal**, never random, and de-duplicated by `event_family`. If a change could let
   future or near-twin information into training, stop and flag it.
2. **Stay in scope.** This is one sharp ML story. Do **not** build new product surfaces, dashboards,
   or a "predict-the-future" transformer. Do not expand the cross-venue paper-trading system.
3. **Don't touch demoted surfaces.** Briefs, watchlists, agent-trust, graph-quality, paper-trading,
   and CatBoost stay as-is and out of the headline. Touch them only if explicitly asked.
4. **The cross-encoder is the headline.** Fine-tune the full encoder from a pretrained backbone on
   self-mined labels. Never ship a zero-shot off-the-shelf reranker as the result.
5. **Ask before deleting.** Repoint and demote; don't remove existing working code without confirming.

## Repo conventions
- Django modular monolith. **All workflows are `manage.py` commands** — match the existing style in
  `apps/web/management/commands/`. New ML orchestration lives in `apps/ranker/`.
- **`chaoswing/ml/` is pure PyTorch and must not import Django.** Keep model/training/eval code there,
  callable from management commands. This keeps it testable and portable.
- Python 3.12, type hints on new code, ruff-clean. Pure functions for metrics and label math.
- Save model checkpoints to `models/` (gitignored). Dataset exports to `ml_data/*.jsonl`.
- New deps go behind the `[ml]` extra in `pyproject.toml`: torch (CUDA), sentence-transformers,
  transformers, faiss-cpu, rank-bm25, statsmodels, cohere (optional, eval-only).

## Where things go
```
apps/ranker/services/    label_mining, biencoder, cross_encoder, listwise_llm, baselines, metrics
apps/ranker/management/   mine_relevance_labels, build_temporal_splits, build_biencoder_index,
                          train_cross_encoder, run_rerank_benchmark, run_forecasting_probe
chaoswing/ml/             data.py, losses.py, train.py, eval.py   (NO django imports)
```

## Commands (run these to verify your own work)
```
python manage.py check
python manage.py test
python -m compileall chaoswing apps tests
ruff check .
```
Training runs on a **local GPU** (bf16/fp16). Log runs to local MLflow. Never assume cloud GPUs.

## Build order (do not reorder)
1. Label miner + temporal splits (`mine_relevance_labels`, `build_temporal_splits`) — leakage-safe labels first.
2. Bi-encoder index + Recall@100 (`build_biencoder_index`).
3. Cross-encoder fine-tune (`train_cross_encoder`) — headline model, pairwise/listwise loss.
4. Baselines + benchmark table (`run_rerank_benchmark`): BM25, cosine, lexical, RankGPT, Cohere.
5. Forecasting probe (`run_forecasting_probe`).
6. README/resume cleanup.

## Definition of done (per module / PR)
- New Django models have migrations; `python manage.py check` and `test` pass.
- New label/metric logic has a unit test asserting the leakage rule (no post-cutoff data used).
- Each command writes a deterministic `ml_data/*.jsonl` export and logs to MLflow where relevant.
- Benchmark numbers are reported on the **temporal test set**, with the method table from the plan.
- One module = one focused PR. Keep diffs reviewable; explain the leakage handling in the PR body.

## When unsure
Re-read `docs/chaoswing-rebuild-plan.md`. If the plan and a request conflict, surface the conflict
rather than guessing. Prefer the smallest change that advances the current build-order step.