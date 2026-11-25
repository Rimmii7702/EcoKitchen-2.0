# FreshRetailNet-50K official held-out evaluation

Phase 4D.4 evaluates seven days of **observed retail sales** after freezing the 96-tree CPU LightGBM configuration selected in Phase 4D.3. The [validation report](freshretailnet-lightgbm.md) covers June 12–25 and its 33.95% LightGBM versus 42.12% seasonal-naive WAPE. The results below are for June 26–July 2 after the fixed refit and use the official evaluation file.

## Frozen forecast and scoring protocol

- **Model freeze.** The [holdout manifest](../forecasting/freshretailnet/holdout-v1.json) pins the Phase 4D.3 configuration, validation scorecard, protocol, and ordered feature schema by canonical JSON SHA-256. The refit uses the same seed, L1 loss, CPU settings, and 96 trees. It does not run checkpoint selection.
- **Refit.** March 28 through June 25, 2024 is available as history. Ten weekly training origins from April 16 through June 18 produce 3,500,000 supervised examples across 50,000 pairs. The two additional origins are June 11 and June 18. The last training target is June 25.
- **Features at issuance.** The June 25 close is the single forecast origin. The model uses store, product, city and category codes from available history, target weekday, horizon, historical sales lags and rolling summaries, and historical daytime stockout counts. The same-weekday feature and the seasonal-naive comparator use sales from seven days before each target date. The model outputs are floored at zero. No future weather, promotions, sales, or stockout flags enter either forecast. [Feature definitions and validation settings](freshretailnet-lightgbm.md#forecast-origins-and-feature-contract)
- **Sealed forecasts.** The refit created one Parquet row for each of the 350,000 unique store–product–date keys, with both predictions on the row. It saved and hashed the forecasts, model, configuration, and feature schema before the scoring process was started. Seasonal naive used the exact seven-day lag on every row.
- **Official scoring.** The separate scoring command verifies the seal and forecast coverage before creating a write-once scoring marker. It then verifies the official evaluation file's SHA-256 and projects only keys, observed daily sales, and the annotations used for stockout strata. It requires both models and the labels to match on every unique key. It cannot train, select a checkpoint, or modify the sealed predictions.

## Final holdout results

MAE and RMSE are measured in normalized retail sales units. WAPE divides total absolute error by total observed sales. Signed bias divides total signed forecast error by the same denominator, with negative values indicating underforecasting.

| Official holdout cohort | Scored rows per model | Model | MAE | RMSE | WAPE | Signed bias |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| All observations | 350,000 | Seasonal naive | 0.4990708057 | 0.8358186962 | 41.8314054769% | +0.2772555727% |
| All observations | 350,000 | LightGBM | 0.4014028435 | 0.7098477855 | 33.6450157277% | −3.1445163587% |
| Fully in stock (24 h) | 151,944 | Seasonal naive | 0.4978879456 | 0.8187851909 | 38.5217459365% | −0.1938565668% |
| Fully in stock (24 h) | 151,944 | LightGBM | 0.3871964261 | 0.6989255505 | 29.9575084864% | −6.0221183144% |
| Stockout-affected (24 h) | 198,056 | Seasonal naive | 0.4999782688 | 0.8486546892 | 44.7700059571% | +0.6955494615% |
| Stockout-affected (24 h) | 198,056 | LightGBM | 0.4123016797 | 0.7181144719 | 36.9191019042% | −0.5895337735% |

LightGBM improved overall WAPE by **8.1863897492 percentage points**. Its WAPE was lower on all seven horizons, each with 50,000 scored rows. Its aggregate underforecast was larger in the fully in-stock cohort. The [complete scorecard](../forecasting/results/freshretailnet-holdout-v1.json) includes all four metrics by date, horizon, 24-hour stock status, and 06:00–21:59 stock status. It also contains daily observed and forecast totals used in the plots.

| Horizon | Target (2024) | Seasonal-naive WAPE | LightGBM WAPE |
| ---: | --- | ---: | ---: |
| 1 | June 26 | 40.81% | 31.83% |
| 2 | June 27 | 41.57% | 32.89% |
| 3 | June 28 | 42.71% | 32.02% |
| 4 | June 29 | 40.20% | 31.80% |
| 5 | June 30 | 38.54% | 33.06% |
| 6 | July 1 | 44.66% | 36.78% |
| 7 | July 2 | 46.09% | 37.94% |

The refit and forecast stage took **80.868 s** and peaked at **779.01 MiB**. Feature construction took 12.447 s, model fitting 51.385 s, and forecasting 6.023 s. The scoring stage, including plotting, took **5.259 s** and peaked at **176.32 MiB**. These process measurements are from Windows, Python 3.11.15, DuckDB 2.0.1, and LightGBM 4.6.0. DuckDB uses a 512 MB managed-memory setting and the LightGBM fit uses four CPU threads.

## Reproduction and artifacts

In a fresh local workspace, place the audited v1.0 Parquet files under `data/external/freshretailnet/` and run the two stages in order. The actual run used the pinned packages from the requirements file. The first command can run with `eval.parquet` absent.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-freshretailnet-lightgbm.txt
.\.venv\Scripts\python.exe -m forecasting.freshretailnet.holdout refit
Get-FileHash instance/freshretailnet/holdout-v1/forecasts.parquet
.\.venv\Scripts\python.exe -m forecasting.freshretailnet.holdout score
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_freshretailnet*.py" -v
```

The stages write to ignored `instance/freshretailnet/holdout-v1/`. The refit refuses an existing run directory, and scoring refuses an existing scoring marker. Use the original artifacts for analysis after the final score.

| Artifact | Purpose |
| --- | --- |
| `model.txt`, `feature-schema.json`, `config.json` | The frozen 96-tree refit, ordered inputs, and run configuration |
| `forecasts.parquet`, `forecast-seal.json`, `scoring-started.json` | Both predictions for every key, hashes before label access, and the one-shot scoring marker |
| `dataset-hashes.json`, `metrics.json` | Verified hashes for both source Parquets and the complete holdout comparison |
| `actual-vs-predicted.png`, `comparison-by-horizon.png` | Daily totals for all rows and stockout strata, and a four-metric comparison by horizon |
| `refit-environment.json`, `score-environment.json` | Versions, runtime, and process-memory measurements |

The sealed forecast SHA-256 is `a3a2c5397950f528e827121468815eac956c30a840dc3d7296c2a33cea2427be`. The final model SHA-256 is `15129128d55af6b49e349c913be6f5a6856a77533237668515cf2c66d587883a`. Both source hashes match the [published data revision recorded in the audit](freshretailnet-audit.md#source-and-license).

## Scope and attribution

The official test covers a single week and existing store–product pairs. It does not evaluate new stores, new products, longer horizons, or restaurant-specific demand. Stockout-affected sales can understate unconstrained demand, and fully in-stock observations form a selective subset. The published normalization cannot be reversed from the supplied data. There is no Flask, React, or inventory integration in this experiment.

Dataset by **Dingdong-Inc**, FreshRetailNet-50K v1.0, distributed under **CC BY 4.0**. Cite Yangyang Wang et al. (2025), arXiv:2505.16319. The source data remains unmodified. [Dataset card](https://huggingface.co/datasets/Dingdong-Inc/FreshRetailNet-50K) · [Technical report](https://arxiv.org/abs/2505.16319) · [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
