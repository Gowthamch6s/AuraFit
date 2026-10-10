# AuraFit Phase 3 — Experimental ML Core

This project **extends Phase 2 without changing the Phase 1 SQL tables or Pydantic schemas**. It retains the original `sql/001_initial.sql`, `models.py`, synthetic data generator and ingestion loader. No Phase 3 migration is required.

## Requirements
Python >=3.12; install `python -m pip install -e '.[dev]'`. No GPU required. The loader and `--database-url` persistence need PostgreSQL/TimescaleDB and psycopg installed. This version's ML baseline uses scikit-learn's HistGradientBoostingRegressor, not a neural network.

## Quick start

```bash
# Windows: .venv\Scripts\Activate.ps1; macOS/Linux: source .venv/bin/activate
python -m venv .venv
python -m pip install -e '.[dev]'
aurafit-sim generate --output generated/balanced --days 30 --seed 42 --scenario balanced --missing-rate 0
aurafit-ml features --input generated/balanced --output generated/features
aurafit-ml train --input generated/balanced --output generated/model
aurafit-ml predict --input generated/balanced --output generated/model
pytest -q
```

`--missing-rate 0` is recommended for this small demonstration training dataset; with realistic missingness the implementation deliberately abstains rather than silently impute unobserved day-level metrics. The Phase 2 default of 3% missingness may make most days ineligible, because it applies per hourly tick. For actual datasets, add missing-data indicators, quality-specific rules and supported imputation policies before deploying models.

## SQL load and persistence

```bash
docker compose up -d db
aurafit migrate --database-url 'postgresql://aurafit:local_development_only@localhost:5433/aurafit'
aurafit-sim load --input generated/balanced --database-url 'postgresql://aurafit:local_development_only@localhost:5433/aurafit'
aurafit-ml features --input generated/balanced --output generated/features --database-url 'postgresql://aurafit:local_development_only@localhost:5433/aurafit'
```

This writes versioned `daily_baselines` and `daily_assessments` with transactional upserts; predictions stay in model artifacts until explicitly adopted in a future service integration. Database integration was not exercised in this environment, so run the SQL flow locally before relying on it.

## Temporal and feature contract

- Each local day is determined with the user's IANA timezone; a sleep session counts for the day it ends. HRV and resting heart rate are averaged per local date, hourly delta steps/active-kcal are summed, and stress is averaged with high-stress sample fraction defined as stress >=75.
- Rolling baseline for day D: **median of available days [D-7..D-1]**, never includes D. `sample_days` is the minimum count across HRV/resting HR/sleep history. Warm-up days are preserved for audit but excluded from supervised training; at least three baseline days are required.
- `hrv_ratio = current_hrv / prior_median`, `robust_z=(current-prior_median)/max(1.4826*MAD, 0.08*prior_median, 1)` clipped to [-5,+5]. Z is exploratory and not a clinical physiological threshold.
- Sleep debt: `sum(max(0, sleep_target - recorded_sleep_minutes))` over *available* trailing seven local dates. The first week is partial; missing sleep cannot be imputed as adequate sleep. This is an illustrative shortfall indicator, not clinical sleep debt.
- `HRV_penalty = clip(170*(1-HRV_ratio),0,100)`; `RHR_penalty = clip(6*(currentRHR-baselineRHR),0,100)`; `sleep_penalty=clip(sleep_debt/7/1.8,0,100)`; `stress_penalty=clip(1.4*(mean_stress-35),0,100)`; `spike_penalty=100*(# stress >=75 / # stress)`.
- `ALI = .30*HRV_penalty + .15*RHR_penalty + .30*sleep_penalty + .15*stress_penalty + .10*spike_pen` (clipped to 0–100). `recovery=100-(.44*HRV_penalty+.18*RHR_penalty+.25*sleep_penalty+.13*stress_penalty)`. `overreach_risk=.65*ALI+.35*(100-recovery)`.
- **These weights, thresholds, ALI and risk score are illustrative, unvalidated heuristic constructs.** They are not clinical allostatic-load measurements, burnout diagnoses, fitness-to-exercise determinations, or a validated classification probability. Model supervision learns to forecast the *heuristic recovery score*, not an independent clinical outcome. Even very low test errors would not establish clinical accuracy.
- No metabolic deficit is inferred: logged intake + active exercise calories cannot establish full energy expenditure. Do not prescribe calorie targets from these inputs.
- Missing metrics are not represented as zero in the output quality field, but model input encoding uses zero for missing records and refuses any day with missing modalities (`missing_fraction != 0`). A meal logging event is not proof of complete-day diet coverage.

## Train/evaluate

Forecast samples pair features D with next-day heuristic recovery D+1. The final ~25% of chronological pairs are held out as test data; model and naive training-median baseline metrics include MAE and RMSE. We neither shuffle nor fit preprocessing on the test period. All features of D are usable **after D ends**. Permutation importance is computed on the holdout for *description only*, not feature selection or tuning; the test should not be reused for iterative choices. For stronger evidence use expanding-window backtests across numerous people and independent prospective external validation.

## Model artifact security and governance

`model.joblib` uses Python pickle serialization, which can execute code at deserialization. **Only load signed/trusted artifacts created by your own pipeline.** Its SHA-256 in the manifest checks file consistency but is **not a signature**; an attacker controlling both files could replace both. Restrict artifact write/read permissions, use supply-chain scanning, and use a signed artifact registry for production. Persist provenance, build environment, training cohort identifiers (deidentified), and validation approval outside this demo.

## Validation

`PYTHONPATH=src pytest -q` covers the Phase 1/2 tests plus causal baselines, future-data invariance, deterministic features, next-day pairing, abstention on missingness, scenario differences, chronological train/test, naive baseline metrics, prediction, and artifact tampering. Live DB integration testing is still required in an environment with a running TimescaleDB server.
