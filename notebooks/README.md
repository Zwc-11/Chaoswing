# ChaosWing Research Notebooks

These notebooks turn the exported benchmark artifacts into lightweight research reports.
They are designed to sit on top of the existing application and dataset builder rather than introduce a separate modeling stack.

## Setup

Install the optional research dependencies:

```powershell
python -m pip install -e ".[research]"
```

Build the exported datasets:

```powershell
python manage.py build_benchmark_dataset
```

The notebooks expect the default export paths under `ml_data/`.

## Notebooks

- `resolution_forecasting_baselines.ipynb`
  - Profiles the resolution dataset, inspects rolling backtest artifacts, and sketches calibration/error slices.
- `related_market_ranking_analysis.ipynb`
  - Compares lexical overlap against the context-aware reranker and inspects judged usefulness labels plus contested cases.
- `lead_lag_signal_validation.ipynb`
  - Reviews mapped pairs, candidate signals, paper trades, and latency-aware failure modes for lead-lag research.
- `benchmark_registry_overview.ipynb`
  - Builds a compact experiment registry across tasks, datasets, metrics, and current readiness.

## Notes

- These notebooks are intentionally honest about current scope: they analyze exported artifacts and benchmark outputs that already exist in the repo.
- They do not claim production-grade models or live trading readiness.
- Each notebook ends with `Findings` and `Limitations` so the analysis stays reviewable instead of becoming an unbounded scratchpad.
