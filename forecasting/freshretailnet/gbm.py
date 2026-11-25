"""Fit the global CPU regressor and score frozen checkpoints against seasonal naive."""

from datetime import date
from time import perf_counter

import numpy as np
import pandas as pd

from .baseline import predict as predict_naive
from .dataset import load_history, load_labels, validation_windows
from .features import build_features
from .scoring import check_keys, score_totals, scorecard
from .supervised import (
    CATEGORIES, FEATURES, build_supervised_features, define_pairs, feature_batch, load_model_history,
    training_origins,
)


def fill_matrix(matrix, batch):
    for index, name in enumerate(FEATURES):
        matrix[:, index] = np.ma.filled(batch[name], np.nan)


def matrix_from(batch):
    matrix = np.empty((len(batch[FEATURES[0]]), len(FEATURES)), dtype=np.float32)
    fill_matrix(matrix, batch)
    return matrix


def training_matrix(connection, config, first_day, cutoff, pairs):
    schedule = config["training_origins"]
    origins = training_origins(first_day, date.fromisoformat(schedule["first"]), cutoff, schedule["stride_days"])
    expected = pairs * 7
    data = np.empty((expected * len(origins), len(FEATURES)), dtype=np.float32)
    targets = np.empty(len(data), dtype=np.float32)
    load_model_history(connection, first_day, cutoff)
    for index, origin in enumerate(origins):
        build_supervised_features(connection, origin)
        batch = feature_batch(connection, fit_cutoff=cutoff)
        if len(batch["target"]) != expected:
            raise ValueError(f"Unexpected training coverage for origin {origin}.")
        block = slice(index * expected, (index + 1) * expected)
        fill_matrix(data[block], batch)
        targets[block] = batch["target"]
    connection.execute("DROP TABLE model_features")
    connection.execute("DROP TABLE model_history")
    return data, targets, origins


def fit_model(data, targets, params, rounds):
    import lightgbm

    dataset = lightgbm.Dataset(data, label=targets, feature_name=list(FEATURES),
                               categorical_feature=list(CATEGORIES))
    return lightgbm.train(params, dataset, num_boost_round=rounds)


def predictions(model, matrix, rounds, threads):
    result = np.maximum(model.predict(matrix, num_iteration=rounds, num_threads=threads), 0)
    if not np.isfinite(result).all():
        raise ValueError("LightGBM forecast produced nonfinite values.")
    return result


def store_predictions(connection, batch, values):
    frame = pd.DataFrame({
        "store_id": batch["key_store"], "product_id": batch["key_product"],
        "target_date": batch["target_date"], "prediction": values,
    })
    connection.register("prediction_batch", frame)
    try:
        connection.execute("""
            CREATE OR REPLACE TEMP TABLE model_predictions AS
            SELECT store_id, product_id, CAST(target_date AS DATE) AS target_date, prediction
            FROM prediction_batch
            """)
    finally:
        connection.unregister("prediction_batch")


def evaluate_models(connection, config, protocol, model, pairs, first_day):
    results = {method: {"folds": {}, "totals": []} for method in ["seasonal_naive", *config["candidate_iterations"]]}
    for fold, origin, first, last in validation_windows(protocol):
        load_model_history(connection, first_day, origin)
        build_supervised_features(connection, origin)
        batch = feature_batch(connection)
        matrix = matrix_from(batch)
        load_history(connection, first_day, origin, study_only=True)
        build_features(connection, origin)
        predict_naive(connection)

        # Only the materialized origin-bounded matrix and its keys reach the predictor.
        for rounds in config["candidate_iterations"]:
            values = predictions(model, matrix, rounds, config["lightgbm"]["num_threads"])
            store_predictions(connection, batch, values)
            if rounds == config["candidate_iterations"][0]:
                load_labels(connection, first, last, study_only=True)
                check_keys(connection, pairs * 7)
                rows = score_totals(connection, origin)
                append_scores(results["seasonal_naive"], fold, rows)
            rows = score_totals(connection, origin, "model_predictions")
            append_scores(results[rounds], fold, rows)
    return {method: {"folds": values["folds"], "pooled_validation": scorecard(values["totals"])}
            for method, values in results.items()}


def append_scores(result, fold, rows):
    result["folds"][fold["name"]] = {**fold, **scorecard(rows)}
    result["totals"].extend(rows)


def experiment(connection, config, protocol, pilot):
    first_day, cutoff = (date.fromisoformat(protocol["fit_history"][key]) for key in ("start", "end"))
    pairs = define_pairs(connection, first_day, cutoff, config["pilot"] if pilot else None, config["lightgbm"]["seed"])
    if not pairs or (not pilot and pairs != protocol["expected_pairs"]):
        raise ValueError("Unexpected study-pair coverage.")
    ticks = perf_counter()
    data, targets, origins = training_matrix(connection, config, first_day, cutoff, pairs)
    timing = {"training_features_seconds": round(perf_counter() - ticks, 3)}
    ticks = perf_counter()
    model = fit_model(data, targets, config["lightgbm"], max(config["candidate_iterations"]))
    timing["fit_seconds"] = round(perf_counter() - ticks, 3)
    count = len(targets)
    del data, targets
    ticks = perf_counter()
    scores = evaluate_models(connection, config, protocol, model, pairs, first_day)
    timing["validation_seconds"] = round(perf_counter() - ticks, 3)
    wape = {method: report["pooled_validation"]["overall"]["all"]["wape_pct"] for method, report in scores.items()}
    selected = min(config["candidate_iterations"], key=lambda rounds: (wape[rounds], rounds))
    return model, {
        "experiment_version": config["experiment_version"], "pilot": pilot,
        "target": "observed_sales", "units": "normalized retail sales units",
        "official_eval_status": "reserved_unread",
        "training": {"through": cutoff.isoformat(), "rows": count, "pairs": pairs,
                     "origins": [day.isoformat() for day in origins]},
        "selection_metric": config["selection_metric"], "selected_iterations": selected,
        "candidate_pooled_wape_pct": {str(rounds): wape[rounds] for rounds in config["candidate_iterations"]},
        "recommended_validation_model": "lightgbm" if wape[selected] < wape["seasonal_naive"] else "seasonal_naive",
        "seasonal_naive": scores["seasonal_naive"], "lightgbm": scores[selected],
    }, timing
