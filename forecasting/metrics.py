"""Holdout error measures in the units of the supplied target."""

import numpy as np


def score(actual, predicted, *, regression=False):
    actual = np.asarray(actual, dtype=float)
    residual = actual - np.asarray(predicted, dtype=float)
    total = float(np.abs(actual).sum())
    result = {
        "mae": float(np.abs(residual).mean()),
        "rmse": float(np.sqrt(np.mean(residual ** 2))),
    }
    if regression:
        variance = float(np.sum((actual - actual.mean()) ** 2))
        result["r2"] = float(1 - np.sum(residual ** 2) / variance) if variance else None
    else:
        result["wape_pct"] = float(100 * np.abs(residual).sum() / total) if total else None
    return result