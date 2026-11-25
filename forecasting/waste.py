"""Seeded reproduction of the notebook's synthetic waste experiment."""

from math import isfinite

import numpy as np
import pandas as pd

from .metrics import score


SEED = 42
SAMPLES = 1000
CATEGORIES = ["Vegetable", "Dairy", "Meat", "Grain", "Fruit"]
NUMERIC_FIELDS = {
    "stock_quantity": {"min": 50, "max": 499, "step": 1},
    "sales_volume": {"min": 10, "max": 449, "step": 1},
    "days_to_expire": {"min": 1, "max": 14, "step": 1},
    "unit_price": {"min": 1, "max": 15, "step": 0.01},
    "inventory_turnover_rate": {"min": 0.1, "max": 1.5, "step": 0.01},
}
SCENARIO_FIELDS = {
    **NUMERIC_FIELDS,
    "category": {"options": sorted(CATEGORIES)},
}


def synthetic_samples():
    rng = np.random.RandomState(SEED)
    frame = pd.DataFrame({
        "stock_quantity": rng.randint(50, 500, SAMPLES),
        "sales_volume": rng.randint(10, 450, SAMPLES),
        "days_to_expire": rng.randint(1, 15, SAMPLES),
        "unit_price": np.round(rng.uniform(1.0, 15.0, SAMPLES), 2),
        "inventory_turnover_rate": np.round(rng.uniform(0.1, 1.5, SAMPLES), 2),
        "category": rng.choice(CATEGORIES, SAMPLES),
    })
    expiry_factor = float(rng.uniform(1, 2))
    return frame, rule(frame, expiry_factor), expiry_factor


def rule(frame, expiry_factor):
    return np.maximum(
        0, frame["stock_quantity"] - frame["sales_volume"]
        - 10 * frame["inventory_turnover_rate"] - expiry_factor * frame["days_to_expire"],
    )


def scenario_values(payload):
    if not isinstance(payload, dict) or set(payload) != set(SCENARIO_FIELDS):
        raise ValueError("Supply all six scenario fields and no additional fields.")
    for key, limits in NUMERIC_FIELDS.items():
        value = payload[key]
        if type(value) not in (int, float) or (limits["step"] == 1 and type(value) is not int):
            raise ValueError(f"{key} has the wrong numeric type.")
        if not limits["min"] <= value <= limits["max"]:
            raise ValueError(f"{key} must be between {limits['min']} and {limits['max']}.")
    if payload["category"] not in CATEGORIES:
        raise ValueError("Choose a supported scenario category.")
    return payload


def tree_value(tree, features):
    node = 0
    for _ in tree["left"]:
        left = tree["left"][node]
        if left == -1:
            return tree["value"][node]
        right = tree["right"][node]
        feature = float(features[tree["feature"][node]])
        node = left if feature <= tree["threshold"][node] else right
        if node < 0 or node >= len(tree["left"]):
            break
    raise ValueError("Invalid tree structure.")


def predict_waste(artifact, payload):
    predictors = [payload[key] for key in NUMERIC_FIELDS]
    predictors += [int(payload["category"] == category) for category in artifact["categories"]]
    # scikit-learn compares float32 predictors to the stored split thresholds.
    predictors = np.asarray(predictors, dtype=np.float32)
    trees = artifact["trees"]
    value = sum(tree_value(tree, predictors) for tree in trees) / len(trees)
    if not isfinite(value) or value < 0:
        raise ValueError("Invalid scenario prediction.")
    return value


def train_waste_synthetic():
    import scipy
    import sklearn
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import OneHotEncoder

    frame, target, factor = synthetic_samples()
    train, test, target_train, target_test = train_test_split(frame, target, test_size=0.2, random_state=SEED)
    encoder = ColumnTransformer([
        ("numeric", "passthrough", list(NUMERIC_FIELDS)),
        ("category", OneHotEncoder(handle_unknown="error"), ["category"]),
    ])
    forest = RandomForestRegressor(n_estimators=100, random_state=SEED, n_jobs=1)
    pipeline = make_pipeline(encoder, forest)
    pipeline.fit(train, target_train)
    predicted = pipeline.predict(test)
    report = {
        "schema_version": 1, "task": "waste_simulation", "version": "waste-synthetic-v1",
        "source": {"name": "Notebook-generated scenario data", "kind": "synthetic"},
        "units": "simulated units", "selected": "random_forest",
        "algorithms": {
            "random_forest": "Random Forest",
            "mean": "Training mean",
            "midpoint_rule": "Fixed midpoint simulation rule",
        },
        "split": {"train_samples": len(train), "test_samples": len(test)},
        "methodology": {
            "seed": SEED, "numpy": np.__version__, "pandas": pd.__version__,
            "scipy": scipy.__version__, "scikit_learn": sklearn.__version__,
            "trees": 100, "expiry_factor": factor,
            "evaluation": "Seeded 80/20 holdout from an artificial target. Category encoding is fit on training only.",
        },
        "test": {
            "random_forest": score(target_test, predicted, regression=True),
            "mean": score(target_test, np.full(len(test), target_train.mean()), regression=True),
            "midpoint_rule": score(target_test, rule(test, 1.5), regression=True),
        },
    }
    trees = [{
        "left": estimator.tree_.children_left.tolist(),
        "right": estimator.tree_.children_right.tolist(),
        "feature": estimator.tree_.feature.tolist(),
        "threshold": estimator.tree_.threshold.tolist(),
        "value": estimator.tree_.value[:, 0, 0].tolist(),
    } for estimator in forest.estimators_]
    artifact = {
        "report": report, "categories": encoder.named_transformers_["category"].categories_[0].tolist(),
        "trees": trees,
    }
    replay = [predict_waste(artifact, row) for row in test.to_dict(orient="records")]
    np.testing.assert_allclose(replay, predicted, rtol=1e-12, atol=1e-12)
    return artifact
