# FreshRetailNet-50K audit and evaluation plan

## Source and license

Dingdong-Inc published FreshRetailNet-50K v1.0 from fresh-grocery retail operations in China. Cite Yangyang Wang et al. (2025), *FreshRetailNet-50K: A Stockout-Annotated Censored Demand Dataset for Latent Demand Recovery and Forecasting in Fresh Retail*, arXiv:2505.16319. The dataset uses **CC BY 4.0**. Retain attribution and the license link, and indicate modifications when sharing adapted data. The baseline code has its own license. [Dataset card](https://huggingface.co/datasets/Dingdong-Inc/FreshRetailNet-50K) · [Technical report](https://arxiv.org/abs/2505.16319) · [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) · [Baseline repository](https://github.com/Dingdong-Inc/frn-50k-baseline)

Keep the downloaded copies in the ignored `data/external/freshretailnet/` folder. Both files matched the official v1.0 downloads at data revision `01a4be6`. [Upstream train file](https://huggingface.co/datasets/Dingdong-Inc/FreshRetailNet-50K/blob/01a4be6/data/train.parquet) · [Upstream eval file](https://huggingface.co/datasets/Dingdong-Inc/FreshRetailNet-50K/blob/01a4be6/data/eval.parquet)

| Local file | Bytes | SHA-256 |
| --- | ---: | --- |
| `train.parquet` | 106,436,287 | `6706832db892bbae4969c19d87e07975d2543d2ba7d7d4756360654785de5a3d` |
| `eval.parquet` | 8,440,124 | `1b118840664280c6b88bffc84c80ee1f54c05d911e354b7599e5da10995e960e` |

## Local file audit

Read-only DuckDB 2.0.1 queries scanned both complete files on November 24, 2026. All date ranges below are inclusive. Each row represents one store, product, and calendar day.

| Check | Official train | Official eval |
| --- | ---: | ---: |
| Rows | 4,500,000 | 350,000 |
| Date range | 2024-03-28 to 2024-06-25 | 2024-06-26 to 2024-07-02 |
| Days per store–product pair | 90 | 7 |
| Cities / stores / products | 18 / 898 / 865 | 18 / 898 / 865 |
| Distinct store–product pairs | 50,000 | 50,000 |
| Duplicate keys or pairs with date gaps | 0 | 0 |
| Daily sales min / median / p95 / max | 0 / 0.7 / 2.9 / 44.9 | 0 / 0.8 / 3.5 / 49.9 |
| Zero-sales days | 200,760 (4.46%) | 14,563 (4.16%) |
| Days with any 06:00–21:59 stockout flag | 1,992,006 (44.27%) | 143,438 (40.98%) |
| Flagged hours within 06:00–21:59 | 14,311,536 of 72,000,000 (19.88%) | 1,014,031 of 5,600,000 (18.11%) |
| Days with any stockout flag across 24 hours | 2,700,787 (60.02%) | 198,056 (56.59%) |
| Flagged hours with positive recorded sales | 772,426 | 59,300 |

The same 50,000 pairs appear in both files, with zero overlapping store, product and date keys and 50,000 rows on every date. The benchmark measures forecasting for existing pairs. Both files cover 7 management groups and 32, 84, and 233 IDs at successive category levels. The strict 24-hour in-stock subset contains 1,799,213 train rows and 151,944 eval rows (43.41% of eval).

All 19 columns have zero top-level nulls. Dates parse and match ISO format. Both hourly lists have exactly 24 entries per row with zero missing elements. Sales are finite and nonnegative, stock flags are binary, and floating-point scalar covariates have zero NaNs or infinities. Discount values include 16,039 train rows and 1,300 eval rows at zero, plus 34 train rows above 1.0 (maximum 1.088). Preserve these values and confirm their meaning before using them as inputs. The holiday and activity flags are binary.

The publisher describes `sale_amount` and `hours_sale` as globally normalized. The conversion back to physical units and the normalization fitting period are undocumented in the supplied files and dataset card. Daily values are fractional on roughly nine out of ten rows. Daily totals agree with the sum of the 24 hourly values within `1e-8` on every row. Report errors and predictions in **normalized retail sales units** and keep them separate from EcoKitchen inventory quantities. [Dataset field definitions](https://huggingface.co/datasets/Dingdong-Inc/FreshRetailNet-50K#data-fields)

The released array uses `1` for an out-of-stock flag. Its zero-based indices 6 through 21 sum to `stock_hour6_22_cnt` on every row. Including the 22:00 slot fails that check on 1,886,333 train rows and 134,182 eval rows. A zero daily count can still coexist with a stockout outside that window. Positive sales in flagged hours show why the flag should be treated as a censoring indicator. Preserve those sales as a lower bound on demand. The unobserved part of demand remains unknown even though the Parquet fields are populated.

## Feature availability and leakage controls

Issue all seven daily predictions from a fixed origin at the close of the last observed day.

| Fields | Use at forecast time |
| --- | --- |
| `dt`, city, store, product, management group, and category IDs | Derive weekdays from the target dates. Use identifiers from the catalog available at the origin. |
| `holiday_flag` | Start with weekday and verify the released flag against an advance holiday calendar before using it for target dates. |
| `sale_amount`, `hours_sale` | Use completed historical days only. Same-day totals, partial target-day sales, and aggregates containing either would leak future observations. |
| `stock_hour6_22_cnt`, `hours_stock_status` | Use completed historical days as inputs. Use realized target-date flags after prediction for scoring strata or retrospective recovery. |
| `discount`, `activity_flag` | Use only available historical values initially. Add target-date values only with timestamped price or campaign plans known at the origin. |
| `precpt`, `avg_temperature`, `avg_humidity`, `avg_wind_level` | Treat as historical observations. Future weather requires archived forecasts with issue timestamps at or before the origin. The release lacks those timestamps. |

Fit encoders, scaling, imputers, and mask-generation rules on the corresponding fit history, and choose model settings on chronological validation. Sort by pair and date. Anchor rolling features and cross-series totals to the forecast origin. Label any ablation using realized future covariates as an oracle experiment with its own scorecard. [Publisher's seven-day task and covariate assumptions](https://arxiv.org/html/2505.16319v1#S4.SS2)

## Chronological protocol and model

| Stage | Target dates | Days | Raw rows |
| --- | --- | ---: | ---: |
| Fit inside `train.parquet` | 2024-03-28 to 2024-06-11 | 76 | 3,800,000 |
| Validation week 1 | 2024-06-12 to 2024-06-18 | 7 | 350,000 |
| Validation week 2 | 2024-06-19 to 2024-06-25 | 7 | 350,000 |
| Final evaluation from unmodified `eval.parquet` | 2024-06-26 to 2024-07-02 | 7 | 350,000 |

Fit validation candidates through June 11. Score the two validation weeks from the June 11 and June 18 origins. Update feature history with the completed first validation week at the second origin, keeping model weights fixed. Ensure training labels for every horizon end by the fit cutoff. After choosing settings, refit once through June 25. Generate all seven official evaluation predictions from the June 25 origin before joining evaluation targets and stockout masks for scoring. The table counts rows before lag warmup. Record eligible rows and exclusions by horizon.

- **Seasonal naive.** Repeat each pair's same-weekday observed sales from the most recent week at the origin. The stockout-aware daily baseline uses the most recent fully in-stock same weekday. For masked-hour recovery, use a median of earlier in-stock values for the same weekday and hour. Predeclare a past-only product/category fallback for missing matches and report how often it is used.
- **Scalable model.** Start with one global, CPU histogram-based LightGBM regressor with horizon as a feature. Pool across pairs using compact categorical codes, calendar features, lagged sales, shifted rolling summaries, and historical stock status. Use projected Parquet reads and bounded DuckDB work for feature generation. Materialize compact per-origin features, process horizons in batches, and profile memory on validation before a full run. Expand hourly arrays only for the recovery experiment. [LightGBM histogram and categorical features](https://lightgbm.readthedocs.io/en/stable/Features.html)

## Separate scorecards

1. **Observed sales.** Score daily `sale_amount` on all official eval rows. Compare the seasonal-naive and LightGBM forecasts on identical keys. Report MAE and RMSE in normalized units, WAPE and signed bias when the actual sum is positive, and counts by horizon and stockout status. Use the full 24-hour window for stockout status and the 06:00–21:59 window as a sensitivity check. WAPE divides total absolute error by total actual sales. Signed bias uses forecast minus actual, divided by total actual sales.
2. **Stockout-aware demand.** For prospective forecasting, score fully in-stock days as an observable proxy and report the subset coverage and selection bias. For retrospective recovery, fit a mask generator from train stockout patterns and hide contiguous blocks in known in-stock validation or eval hours. Retain the original labels only for scoring. Remove masked sales from every recovery feature, including daily totals. Freeze mask rules and seed before final evaluation. Score masked hours separately using MAE, RMSE, WAPE and bias, with breakdowns by block duration and hour. For naturally flagged hours, report diagnostics, uncertainty and lower-bound consistency. Their counterfactual demand has no labels in the release. [Publisher's evaluation framework](https://arxiv.org/html/2505.16319v1#S5.SS1)

The benchmark covers 97 calendar days and one official evaluation week. It provides limited evidence about longer seasonal cycles, unseen stores or products, or restaurant demand.

## Experiment reports

The [offline baseline report](freshretailnet-baseline.md) and [global LightGBM validation](freshretailnet-lightgbm.md) use the same forecast origins and comparison keys. The [official holdout report](freshretailnet-holdout.md) covers the frozen refit and June 26–July 2 observed-sales forecast. Stockout recovery remains a separate experiment.
