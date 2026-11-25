# FreshRetailNet-50K seasonal-naive baseline

This offline run uses normalized observed sales from Dingdong-Inc's fresh retail benchmark. See the [dataset audit](freshretailnet-audit.md) for source, license, and file hashes. The CLI is independent of Flask, SQLite, React, and the synthetic waste models.

## Reproduce the run

From the repository root with Python 3.11 and the audited `data/external/freshretailnet/train.parquet` in place:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-freshretailnet.txt
.\.venv\Scripts\python.exe -m forecasting.freshretailnet --run-name validation-v1
.\.venv\Scripts\python.exe -m forecasting.freshretailnet --run-name validation-v1-repeat
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_freshretailnet_baseline.py -v
Get-FileHash instance/freshretailnet/validation-v1/metrics.json,instance/freshretailnet/validation-v1-repeat/metrics.json
```

Each run name creates a new directory under ignored `instance/freshretailnet/`. Each run writes `config.json`, `dataset-hashes.json`, `metrics.json`, `validation-by-horizon.png`, and `run-environment.json`. The training hash is computed from local bytes. The reserved evaluation hash is copied from the prior audit. This CLI opens only the training file, so the evaluation file is unnecessary to reproduce validation. The versioned [configuration](../forecasting/freshretailnet/validation-v1.json) and [validation scorecard](../forecasting/results/freshretailnet-validation-v1.json) can be compared with generated outputs.

## Forecasting and scoring

| Run | History through | Forecast targets | Scored rows |
| --- | --- | --- | ---: |
| Validation 1 | 2024-06-11 | 2024-06-12 to 2024-06-18 | 350,000 |
| Validation 2 | 2024-06-18 | 2024-06-19 to 2024-06-25 | 350,000 |
| Reserved official evaluation | 2024-06-25 | 2024-06-26 to 2024-07-02 | Unscored |

The first history starts March 28. The second extends through the completed first validation week. At each origin DuckDB projects only pair identifiers, dates, and past observed sales into the feature table. Each history-visible pair receives seven target dates. The primary rule repeats the corresponding sale exactly seven days earlier. If it is missing, use the nearest earlier value with the same weekday, then the pair's historical mean, the product's historical same-weekday mean, the global historical same-weekday mean, and finally the global historical mean. If every usable observation is absent, the prediction is unavailable. Pairs first seen after issuance are excluded from issuance and count as unscored if they appear among the labels.

The label projection runs after the prediction table is materialized. It supplies same-day observed sales and stockout flags only for scoring. The baseline uses no weather, promotions, activity flags, or stock annotations as predictors. The scorecard reports all observations and the fully in-stock and affected subsets under both 24-hour and daytime definitions. MAE and RMSE are in normalized sales units. WAPE and signed bias use the sum of scored observed sales as denominator. Negative bias indicates underforecasting. Both percentages are undefined when the denominator is zero. Pooled results combine error totals and counts across weeks before computing metrics.

## Verified validation results

| Cohort | Scored | MAE | RMSE | WAPE | Signed bias |
| --- | ---: | ---: | ---: | ---: | ---: |
| Validation 1, all | 350,000 | 0.45594 | 0.75516 | 43.11% | −1.12% |
| Validation 2, all | 350,000 | 0.49337 | 0.82834 | 41.24% | −11.59% |
| Pooled, all | 700,000 | 0.47466 | 0.79260 | 42.12% | −6.67% |
| Pooled, fully in stock (24 h) | 287,376 | 0.46556 | 0.75273 | 41.77% | −5.92% |
| Pooled, stockout-affected (24 h) | 412,624 | 0.48100 | 0.81922 | 42.35% | −7.19% |

Each horizon has 50,000 scored rows per validation week. All 700,000 predictions used the exact seven-day lag. The complete scorecard includes counts and metrics for each horizon and stockout group. Two runs on Windows with Python 3.11.15 produced byte-identical metrics, configuration, hash manifests, and the chart. The runs took 21.057 and 15.784 seconds with peak process working sets of 472.93 and 474.26 MiB. These timings are local observations. Elapsed time and process memory are recorded separately from the reproducible metrics.

The runner sets DuckDB to two threads with a 512 MB managed-memory budget and an ignored spill directory. It projects scalar Parquet columns for features and reads the hourly stock arrays only for scoring cohort flags. It does not expand the arrays or load the full panel into a Python dataframe. DuckDB's memory setting governs managed buffers and can differ from total process memory.

The scorecard describes observed sales. Stockout-affected records can understate demand, and the fully in-stock subset is selective. Fallbacks were exercised with fixtures because every audited validation pair had an exact lag. Two validation weeks cover limited calendar variation and the same store–product pairs throughout.

## Next step

The [global CPU LightGBM validation](freshretailnet-lightgbm.md) compares with this baseline on the same keys and uses the same fixed forecast origins. Keep the seasonal-naive baseline for future comparisons. Latent-demand recovery and its controlled masking study have a separate scope.
