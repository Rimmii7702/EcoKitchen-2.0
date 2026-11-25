"""Validate and project the phase 4D.4 scorecard for the local dashboard."""

from datetime import date, timedelta
from math import isclose, isfinite


MODELS = {"seasonal_naive": "Seasonal naive", "lightgbm": "LightGBM"}
GROUPS = {"in_stock_24h": "Fully in stock", "stockout_24h": "Stockout-affected"}
ORIGIN = date(2024, 6, 25)
DATES = [(ORIGIN + timedelta(days=day)).isoformat() for day in range(1, 8)]
PAIRS = 50_000
ROWS = PAIRS * len(DATES)


def number(value, *, signed=False):
    if type(value) not in (int, float) or not isfinite(value) or (value < 0 and not signed):
        raise ValueError("Expected a finite scorecard number.")
    return value


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError("Expected a nonnegative integer count.")
    return value


def metrics(row, expected):
    if (count(row["scored"]) != expected or count(row["observations"]) != expected
            or count(row["unscored"]) != 0):
        raise ValueError("Unexpected scored row count.")
    return {key: number(row[key], signed=key == "signed_bias_pct")
            for key in ("mae", "rmse", "wape_pct", "signed_bias_pct")}


def aggregate_sales(rows):
    grouped = {}
    for row in rows:
        day = row["target_date"]
        flag = row["stockout_24h"]
        key = day, flag
        if day not in DATES or type(flag) is not bool or key in grouped:
            raise ValueError("Invalid daily stockout grouping.")
        grouped[key] = {"rows": count(row["scored"]), **{
            field: number(row[field]) for field in ("actual", *MODELS)
        }}
    daily = []
    for day in DATES:
        cohorts = [grouped[(day, flag)] for flag in (False, True)]
        if sum(row["rows"] for row in cohorts) != PAIRS:
            raise ValueError("Unexpected daily row count.")
        daily.append({"date": day, **{
            field: number(sum(row[field] for row in cohorts)) for field in ("actual", *MODELS)
        }})
    return daily, grouped


def project_holdout(scorecard):
    if (
        scorecard["experiment_version"] != "freshretailnet-official-holdout-v1"
        or scorecard["subset"] != "official_held_out_evaluation"
        or scorecard["target"] != "observed_sales"
        or scorecard["units"] != "normalized retail sales units"
        or scorecard["forecast_origin"] != ORIGIN.isoformat()
        or [scorecard["target_start"], scorecard["target_end"]] != [DATES[0], DATES[-1]]
        or count(scorecard["training"]["pairs"]) != PAIRS
        or count(scorecard["selected_iterations"]) != 96
    ):
        raise ValueError("Unexpected benchmark identity.")

    daily, grouped = aggregate_sales(scorecard["daily_totals_by_24h_stockout"])
    models = {key: {"label": label, "metrics": metrics(scorecard[key]["overall"]["all"], ROWS)}
              for key, label in MODELS.items()}
    models["lightgbm"]["trees"] = scorecard["selected_iterations"]
    difference = round(models["seasonal_naive"]["metrics"]["wape_pct"] - models["lightgbm"]["metrics"]["wape_pct"], 10)
    if not isclose(difference, number(scorecard["wape_improvement_percentage_points"], signed=True),
                   rel_tol=0, abs_tol=1e-9):
        raise ValueError("WAPE comparison differs from the scorecard.")

    groups = []
    for flag, (key, label) in enumerate(GROUPS.items()):
        rows = sum(grouped[(day, bool(flag))]["rows"] for day in DATES)
        groups.append({"id": key, "label": label, "rows": rows, **{
            model: metrics(scorecard[model]["overall"][key], rows) for model in MODELS
        }})
    horizons = []
    for index, day in enumerate(DATES, 1):
        models_at_day = {}
        for model in MODELS:
            at_day = scorecard[model]["by_horizon"][str(index)]
            for flag, group in zip((False, True), GROUPS):
                metrics(at_day[group], grouped[(day, flag)]["rows"])
            models_at_day[model] = metrics(at_day["all"], PAIRS)
        horizons.append({"day": index, "date": day, "rows": PAIRS, **models_at_day})

    return {
        "schema_version": 1,
        "dataset": {
            "name": "FreshRetailNet-50K", "publisher": "Dingdong-Inc", "domain": "Fresh grocery retail",
            "url": "https://huggingface.co/datasets/Dingdong-Inc/FreshRetailNet-50K",
            "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
        },
        "evaluation": {
            "kind": scorecard["subset"], "target": "Observed retail sales", "units": scorecard["units"],
            "forecast_origin": ORIGIN.isoformat(), "start": DATES[0], "end": DATES[-1],
            "rows": ROWS, "pairs": PAIRS, "horizon_days": len(DATES),
        },
        "models": models, "wape_improvement_pp": difference,
        "daily_sales": daily, "horizons": horizons, "stockout_groups": groups,
    }
