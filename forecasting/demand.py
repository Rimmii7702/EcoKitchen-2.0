"""Leakage-safe, one-week demand forecasting on complete weekly sales."""

from datetime import date, timedelta

import numpy as np

from .data import WEEK, read_weekly_totals
from .metrics import score


LAGS = 4
RIDGE_ALPHA = 1.0
METHODS = {"naive": "Last observed week", "ridge": "Four-lag ridge regression"}


def fit_ridge(values):
    windows = np.lib.stride_tricks.sliding_window_view(np.asarray(values, dtype=float), LAGS + 1)
    predictors, target = windows[:, :-1], windows[:, -1]
    mean = predictors.mean(axis=0)
    scale = predictors.std(axis=0)
    centered = (predictors - mean) / np.where(scale == 0, 1, scale)
    coefficients = np.linalg.solve(
        centered.T @ centered + RIDGE_ALPHA * np.eye(LAGS),
        centered.T @ (target - target.mean()),
    ) / np.where(scale == 0, 1, scale)
    return {"coefficients": coefficients.tolist(), "intercept": float(target.mean() - mean @ coefficients)}


def estimate(method, window, parameters=None):
    if method == "naive":
        return float(window[-1])
    if method != "ridge":
        raise ValueError("Unknown demand model.")
    value = float(np.dot(parameters["coefficients"], window) + parameters["intercept"])
    if not np.isfinite(value):
        raise ValueError("The model returned a nonfinite value.")
    return max(0.0, value)


def rolling_scores(values, start, end):
    forecasts = {method: [] for method in METHODS}
    for origin in range(start, end):
        past = values[:origin]
        window = past[-LAGS:]
        forecasts["naive"].append(estimate("naive", window))
        forecasts["ridge"].append(estimate("ridge", window, fit_ridge(past)))
    return {method: score(values[start:end], predicted) for method, predicted in forecasts.items()}


def train_demand(path, source_name):
    source_name = source_name.strip()
    if not source_name or len(source_name) > 160:
        raise ValueError("Supply a source name for the verified sales export (1 to 160 characters).")
    timeline, digest = read_weekly_totals(path)
    dates, values = zip(*timeline)
    block = len(values) // 5
    validation_start = len(values) - 2 * block
    test_start = len(values) - block

    validation = rolling_scores(values, validation_start, test_start)
    selected = min(METHODS, key=lambda method: validation[method]["mae"])
    test = rolling_scores(values, test_start, len(values))
    history = [
        {"week_start": start.isoformat(), "week_end": (start + timedelta(days=6)).isoformat(), "units": count}
        for start, count in timeline
    ]
    report = {
        "schema_version": 1, "task": "demand", "version": "demand-v1",
        "source": {"name": source_name, "kind": "operator_verified_weekly_sales", "sha256": digest},
        "units": "units sold", "algorithms": METHODS, "selected": selected,
        "methodology": {
            "seed": None, "numpy": np.__version__, "lags": LAGS, "ridge_alpha": RIDGE_ALPHA,
            "evaluation": "Expanding-window, one-step evaluation. Select by validation MAE; report test separately.",
        },
        "split": {
            "training": {"from": dates[0].isoformat(), "through": dates[validation_start - 1].isoformat(),
                         "weeks": validation_start},
            "validation": {"from": dates[validation_start].isoformat(), "through": dates[test_start - 1].isoformat(),
                           "weeks": block},
            "test": {"from": dates[test_start].isoformat(), "through": dates[-1].isoformat(), "weeks": block},
        },
        "validation": validation, "test": test,
    }
    return {
        "report": report, "history": history, "window": list(values[-LAGS:]),
        "parameters": fit_ridge(values) if selected == "ridge" else None,
    }


def predict_demand(artifact):
    past = artifact["history"]
    method = artifact["report"]["selected"]
    start = date.fromisoformat(past[-1]["week_start"]) + WEEK
    return {
        "week_start": start.isoformat(), "week_end": (start + timedelta(days=6)).isoformat(),
        "predicted_units": estimate(method, artifact["window"], artifact["parameters"]),
        "model": method, "as_of_week": past[-1]["week_start"],
    }
