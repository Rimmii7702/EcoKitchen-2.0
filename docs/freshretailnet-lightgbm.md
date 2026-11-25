# FreshRetailNet-50K CPU LightGBM validation

This offline experiment forecasts seven days of normalized **observed retail sales** for existing store–product pairs. It uses Dingdong-Inc's CC BY 4.0 dataset and is independent of EcoKitchen's restaurant, Flask, React, and synthetic waste workflows. [Source and license audit](freshretailnet-audit.md)

## Reproduce locally

From the repository root with Python 3.11 and the audited `data/external/freshretailnet/train.parquet`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-freshretailnet-lightgbm.txt
.\.venv\Scripts\python.exe -m forecasting.freshretailnet.train_lightgbm --pilot --run-name lightgbm-pilot-local
.\.venv\Scripts\python.exe -m forecasting.freshretailnet.train_lightgbm --run-name lightgbm-validation-local
.\.venv\Scripts\python.exe -m forecasting.freshretailnet.train_lightgbm --run-name lightgbm-validation-repeat
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_freshretailnet*.py" -v
Get-FileHash instance/freshretailnet/lightgbm-validation-local/model.txt,instance/freshretailnet/lightgbm-validation-repeat/model.txt
```

Use a fresh run name each time. The ignored `instance/freshretailnet/<run-name>/` holds the native LightGBM `model.txt`, `feature-schema.json`, `config.json`, `dataset-hashes.json`, `metrics.json`, `comparison-by-horizon.png`, and `run-environment.json`. The run reads and hashes only `train.parquet`. The official evaluation date range and SHA-256 in the manifest come from the prior audit. The [experiment configuration](../forecasting/freshretailnet/lightgbm-v1.json) and [validation protocol](../forecasting/freshretailnet/validation-v1.json) are versioned in the repository.

## Forecast origins and feature contract

| Stage | Origin(s) | Target dates |
| --- | --- | --- |
| Supervised fit | Tuesdays, 2024-04-16 through 2024-06-04 | 2024-04-17 through 2024-06-11 |
| Validation 1 | Close of 2024-06-11 | 2024-06-12 through 2024-06-18 |
| Validation 2 | Close of 2024-06-18 | 2024-06-19 through 2024-06-25 |
| Reserved official evaluation | Reserved | 2024-06-26 through 2024-07-02 |

Fit history starts on March 28. Eight weekly origins generate seven labels per pair. All training target dates end by June 11. The second validation origin incorporates the completed first validation week as feature history. Its model weights and categorical schema are identical to the first validation origin.

- **Features.** Store, product, city, third-level category, and target ISO weekday are categorical. City and category come from the latest history available at the origin. Numeric inputs include horizon, completed origin-day sales, and lags of 1, 7, and 14 days. The model also uses sales at target date minus seven days, 7- and 14-day rolling summaries, and historical daytime stockout hours. Rolling windows include the completed origin day. Missing exact-date lags stay missing and reach LightGBM as NaN. The ordered inputs and types are saved with each checkpoint.
- **Isolation.** Feature SQL limits rows to dates at or before the origin. Supervised target sales join separately with an explicit June 11 cutoff. Validation stocks and realized sales are used only for scoring. Target-day weather, promotions, activity flags, and stockout status are excluded from prediction. Tests alter future rows and verify both feature matrices and fixed-model predictions remain identical.
- **Training and selection.** The pilot chooses a stable roughly 5% sample of pairs by seeded hash. The full run uses every pair. Fit one global L1 LightGBM model with a fixed seed, four CPU threads, deterministic column-wise histogram construction, a 63-bin maximum, and a 128 MB histogram cache. Compare the 32-, 64-, and 96-tree prefixes on the two validation windows. Select the smallest pooled WAPE, breaking ties in favor of fewer trees. Clamp predicted sales at zero. [LightGBM CPU training parameters](https://lightgbm.readthedocs.io/en/stable/Parameters.html)
- **Comparison.** Enforce equality and uniqueness of LightGBM, seasonal-naive, and observed label keys before scoring. Report MAE, RMSE, WAPE, and signed bias by horizon for all rows and for 24-hour and daytime in-stock and stockout-affected groups. Keep the baseline when pooled validation WAPE does not improve.

## Verified validation

Both models scored 350,000 rows per week, 50,000 per horizon in each week. The pilot used 2,482 pairs and 138,992 supervised rows. It scored 34,748 validation rows and produced 33.82% LightGBM WAPE versus 41.49% seasonal-naive WAPE. The full run trained on 2,800,000 examples from all 50,000 pairs. The 32-, 64-, and 96-tree candidates scored 35.51%, 34.07%, and 33.95% pooled WAPE. The selected 96-tree checkpoint reduced pooled WAPE by 8.16 percentage points. The re-scored baseline matched the original scorecard.

| Validation cohort | Rows | Model | MAE | RMSE | WAPE | Signed bias |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| June 12–18 | 350,000 | Seasonal naive | 0.45594 | 0.75516 | 43.11% | −1.12% |
| June 12–18 | 350,000 | LightGBM | 0.34786 | 0.58395 | 32.89% | −2.02% |
| June 19–25 | 350,000 | Seasonal naive | 0.49337 | 0.82834 | 41.24% | −11.59% |
| June 19–25 | 350,000 | LightGBM | 0.41748 | 0.72506 | 34.90% | −12.77% |
| Pooled | 700,000 | Seasonal naive | 0.47466 | 0.79260 | 42.12% | −6.67% |
| Pooled | 700,000 | LightGBM | 0.38267 | 0.65830 | 33.95% | −7.72% |
| Fully in stock (24 h) | 287,376 | Seasonal naive | 0.46556 | 0.75273 | 41.77% | −5.92% |
| Fully in stock (24 h) | 287,376 | LightGBM | 0.36626 | 0.60831 | 32.86% | −9.06% |
| Stockout-affected (24 h) | 412,624 | Seasonal naive | 0.48100 | 0.81922 | 42.35% | −7.19% |
| Stockout-affected (24 h) | 412,624 | LightGBM | 0.39411 | 0.69097 | 34.70% | −6.81% |

MAE and RMSE are in normalized retail sales units. The [full validation scorecard](../forecasting/results/freshretailnet-lightgbm-validation-v1.json) includes both models' per-horizon and per-stockout-cohort results. LightGBM improved WAPE in every pooled horizon. Its pooled bias moved farther below zero, and the second validation week had the greatest aggregate underforecast.

On Windows with Python 3.11.15, the verified pilot took 22.4 seconds and peaked at 321 MiB. The first full run took 82.4 seconds and peaked at 829 MiB. Within that run, feature creation took 10.2 seconds, fitting 28.2 seconds, and validation 35.6 seconds. DuckDB projects and limits the source columns, processes one supervised origin at a time, and uses a 512 MB managed-memory budget with local spill. The preallocated float32 training feature matrix occupies about 182 MiB at full size. Process memory also includes LightGBM, DuckDB, and the Python runtime.

The second full run took 86.3 seconds and peaked at 829 MiB. It produced byte-identical model, metrics, configuration, schema, dataset hash manifest, and plot. Runtime and process memory measurements are recorded separately in `run-environment.json` and vary by run. The checkpoint SHA-256 is `055f551db3afc79fe418eaf8e4d9194db4715abd91ddc7c20c64aee6abacc893`.

## Interpretation

These validation weeks are used for model selection and cover existing pairs. They provide limited evidence about generalization across seasons, unseen stores or products, or restaurant operations. Observed sales can understate demand when stock is unavailable. Cohort scores use realized flags after prediction and describe observed sales, with no estimate of lost demand. The fully in-stock group is selective. Future covariate availability and recovery of censored demand need separate experiments.

The [official held-out evaluation](freshretailnet-holdout.md) reports a separate frozen refit and test week.
