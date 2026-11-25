"""Refit the frozen model and forecast with the training source alone."""

from datetime import date
from time import perf_counter

from .baseline import predict as predict_naive
from .dataset import load_history
from .features import build_features
from .gbm import fit_model, matrix_from, predictions, store_predictions, training_matrix
from .supervised import build_supervised_features, define_pairs, feature_batch, load_model_history


def forecast(connection, model, first_day, origin, rounds, threads):
    load_model_history(connection, first_day, origin)
    build_supervised_features(connection, origin)
    batch = feature_batch(connection)
    store_predictions(connection, batch, predictions(model, matrix_from(batch), rounds, threads))
    load_history(connection, first_day, origin, study_only=True)
    build_features(connection, origin)
    predict_naive(connection)
    connection.execute("""
        CREATE OR REPLACE TEMP TABLE sealed_forecasts AS
        SELECT store_id, product_id, target_date, CAST(date_diff('day', ?, target_date) AS INTEGER) AS horizon,
               m.prediction AS lightgbm, b.prediction AS seasonal_naive, b.method AS baseline_method
        FROM model_predictions m FULL OUTER JOIN baseline_predictions b USING (store_id, product_id, target_date)
        """, [origin])


def refit_and_forecast(connection, spec):
    frozen = spec["selected_lightgbm"]
    first = date.fromisoformat(spec["protocol"]["fit_history"]["start"])
    cutoff = date.fromisoformat(spec["refit_through"])
    origin = date.fromisoformat(spec["forecast_origin"])
    pairs = define_pairs(connection, first, cutoff, seed=frozen["lightgbm"]["seed"])
    if pairs != spec["predictions_per_horizon"]:
        raise ValueError("Unexpected number of existing store–product pairs.")
    tick = perf_counter()
    data, labels, origins = training_matrix(connection, frozen, first, cutoff, pairs)
    timings = {"training_features_seconds": round(perf_counter() - tick, 3)}
    tick = perf_counter()
    rounds = spec["selected_iterations"]
    model = fit_model(data, labels, frozen["lightgbm"], rounds)
    if model.current_iteration() != rounds:
        raise ValueError("The refit did not produce the frozen number of trees.")
    timings["fit_seconds"] = round(perf_counter() - tick, 3)
    training = {"first_history_date": first.isoformat(), "target_through": cutoff.isoformat(),
                "origins": [day.isoformat() for day in origins], "rows": len(labels), "pairs": pairs}
    del data, labels
    tick = perf_counter()
    forecast(connection, model, first, origin, rounds, frozen["lightgbm"]["num_threads"])
    timings["forecast_seconds"] = round(perf_counter() - tick, 3)
    return model, training, timings
