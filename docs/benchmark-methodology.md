# Benchmark Methodology

ChaosWing currently exposes a real baseline benchmark layer built from persisted run artifacts.
The work is organized into three families:

- Core prediction and ranking benchmarks: resolution forecasting, related-market ranking, and judged related-market usefulness.
- Cross-venue signal research: mapped pairs, live ticks, screened signals, and paper-trade falsification for lead-lag hypotheses.
- Secondary AI-system evaluation: graph quality scoring, agent instrumentation coverage, and trust scoring over saved runs.

## Current live metrics

- Resolution forecasting rolling backtest:
  - binary YES/NO snapshots only
  - once a terminal outcome is known, it is propagated across the full saved snapshot history for that event
  - baseline is market-implied YES probability at snapshot time
  - challenger is an expanding-window logistic model trained on historical snapshot features
  - reported metrics: Brier score, log loss, calibration error, accuracy, and lift versus baseline
  - the benchmark is only promoted to the live board after it clears a minimum evaluated-example threshold
- Related-market ranking silver benchmark:
  - each saved run contributes a query
  - positives are the related markets actually selected in that run's graph
  - hard negatives are mined from the global related-market catalog based on lexical overlap
  - baseline uses source-title lexical overlap only
  - challenger reranks with graph-context tokens, category hints, and popularity penalties
  - reported metrics: Recall@3, NDCG@5, MRR, and lift versus baseline
- Human-labeled related-market usefulness:
  - labels are captured through the review queue over surfaced related markets
  - each label marks a surfaced market as `core`, `watch`, or `reject`
  - multiple named reviewers can score the same candidate, and ChaosWing aggregates them into reviewer-aware consensus labels
  - the benchmark tracks agreement rate and contested candidates in addition to ranking quality among judged candidates for each run
  - reported metrics: NDCG@5, Recall@3, MRR, and label coverage
- Cross-venue lead-lag paper trading:
  - candidate pairs come from Polymarket and Kalshi market mappings
  - signals are screened on semantics, causal direction, resolution compatibility, and a stability heuristic
  - paper trades are net of estimated spread, fees, and slippage
  - reported metrics: net PnL, gross PnL, hit rate, average edge, slippage-adjusted return, and decay time
- Graph quality scoring baseline:
  - labels come from persisted review outputs stored on each `GraphRun`
  - the baseline model is the current heuristic scorer in `apps/web/services/ml_hooks.py`
  - reported metrics: MAE and RMSE against stored review quality scores
- Related-market coverage proxy:
  - average related markets per run
  - top-related confidence across recent runs
- Evidence density tracking:
  - average evidence nodes per run
  - average edges per run
- Agent instrumentation coverage:
  - persisted workflow stages per run
  - historical runs can be backfilled into the staged pipeline when they predate the current trace model
  - required stages are normalized per run so repeated backfills do not inflate coverage
  - run coverage plus citation/token/latency/cost metadata coverage
  - latency coverage is scored over non-skipped stages
  - token and cost coverage are scored only on stages expected to carry LLM telemetry
  - the backfill path can repair missing execution metadata and estimate Anthropic trace cost from configured or model-family pricing
  - fallback / failed stage counts
- Agent trust benchmark:
  - evaluates each saved run directly from the graph payload, review output, and staged traces
  - reported metrics: trust score, unsupported-claim rate, explained-edge rate, citation-backed stage rate, telemetry coverage
  - this is a structural trust benchmark, not a full human citation-correctness study

## Commands

```powershell
python manage.py sync_crossvenue_market_map
python manage.py stream_live_ticks --duration-seconds 60 --iterations 0 --rebuild-pairs-every 1 --scan-signals-every 1 --run-paper-trader --transport hybrid --active-pairs-only
python manage.py build_leadlag_pairs
python manage.py run_leadlag_backtest
python manage.py run_paper_trader
python manage.py collect_market_snapshots
python manage.py label_resolved_markets --refresh-remote
python manage.py build_benchmark_dataset
python manage.py run_resolution_backtest --refresh-labels --refresh-remote
python manage.py run_related_market_ranking_benchmark
python manage.py run_related_market_usefulness_benchmark --min-reviewers-per-candidate 1
python manage.py run_golden_dataset_eval --strategy baseline --log-mlflow
python manage.py run_quality_backtest
python manage.py backfill_agent_pipeline_traces
python manage.py run_agent_eval --backfill-missing
python manage.py run_agent_trust_benchmark
python manage.py export_benchmark_report --pretty
```

## Dataset artifacts

The benchmark dataset builder exports:

- `ml_data/runs.jsonl`
- `ml_data/snapshots.jsonl`
- `ml_data/resolution_labels.jsonl`
- `ml_data/resolution_forecast_examples.jsonl`
- `ml_data/related_market_ranking_examples.jsonl`
- `ml_data/related_market_judgments.jsonl`
- `ml_data/related_market_usefulness_examples.jsonl`
- `ml_data/agent_traces.jsonl`
- `ml_data/agent_trust_examples.jsonl`
- `ml_data/experiments.jsonl`
- `ml_data/crossvenue_market_map.jsonl`
- `ml_data/market_event_ticks.jsonl`
- `ml_data/orderbook_snapshots.jsonl`
- `ml_data/leadlag_pairs.jsonl`
- `ml_data/leadlag_signals.jsonl`
- `ml_data/paper_trades.jsonl`

If `duckdb` is installed, it also writes `ml_data/chaoswing_analytics.duckdb`.

## What is not claimed yet

ChaosWing now ships a real rolling backtest for binary YES/NO resolution forecasting, a silver-label related-market ranking benchmark, and a human-judgment usefulness benchmark, but it still does not claim production-grade forecasting accuracy or final research completeness.
Those require:

- historical resolved-market labels at scale
- broader human-labeled ranking coverage for adjacent market usefulness
- richer agent traces with citation correctness and unsupported-claim checks beyond stage coverage and metadata backfill

The current benchmark layer is deliberately honest: it shows the persisted seams the next model and agent iterations will build on.

For local MLOps, the human-labeled related-market usefulness benchmark is also the cleanest golden-dataset path. ChaosWing can export that judged set, score the lexical baseline or context-aware reranker against it, and log the result to local MLflow without requiring Databricks.

The lead-lag subsystem should be read the same way: it is a falsification harness for cross-venue spillover hypotheses, not proof that “correlated markets = free money.”
