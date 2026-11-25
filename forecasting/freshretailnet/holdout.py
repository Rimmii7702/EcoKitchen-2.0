"""Write sealed forecasts, then score the official holdout in a separate process."""

import argparse
import json
import os
import sys
from datetime import date, timedelta
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

from .dataset import LABEL_COLUMNS, open_train, sha256_file, validation_windows
from .reporting import plot_actuals, plot_comparison, write_json
from .scoring import check_keys, score_totals, scorecard
from .supervised import feature_schema


ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).with_name("holdout-v1.json")
OUTPUT = ROOT / "instance" / "freshretailnet" / "holdout-v1"
SEALED_FILES = ("forecasts.parquet", "model.txt", "config.json", "feature-schema.json")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_digest(data):
    return sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def frozen_spec():
    frozen = read_json(CONFIG)
    config = read_json(CONFIG.with_name("lightgbm-v1.json"))
    protocol = read_json(CONFIG.with_name("validation-v1.json"))
    validation = read_json(ROOT / "forecasting" / "results" / "freshretailnet-lightgbm-validation-v1.json")
    for key, value in (
        ("validation_protocol", protocol), ("validation_config", config),
        ("validation_scorecard", validation), ("feature_schema", feature_schema()),
    ):
        if canonical_digest(value) != frozen[f"{key}_sha256"]:
            raise ValueError(f"Frozen {key} checksum differs from phase 4D.3.")
    if validation["selected_iterations"] != frozen["selected_iterations"] or validation["pilot"]:
        raise ValueError("Final refit must use the checkpoint selected on full validation.")
    origin = date.fromisoformat(frozen["forecast_origin"])
    validation_end = list(validation_windows(protocol))[-1][3]
    heldout = protocol["dataset"]["reserved_eval"]
    if (
        origin.isoformat() != frozen["refit_through"] or origin != validation_end
        or date.fromisoformat(heldout["start"]) != origin + timedelta(days=1)
        or date.fromisoformat(heldout["end"]) != origin + timedelta(days=frozen["horizons"])
        or frozen["predictions_per_horizon"] != protocol["expected_pairs"]
        or frozen["horizons"] != protocol["forecast_horizon_days"]
    ):
        raise ValueError("Final refit and forecast dates must follow the frozen chronological protocol.")
    return {**frozen, "protocol": protocol, "selected_lightgbm": config}


def verify_forecast_panel(connection, spec):
    counts = connection.execute("""
        SELECT horizon, count(*), count(DISTINCT (store_id, product_id)),
               count(*) FILTER (WHERE store_id IS NULL OR product_id IS NULL OR target_date IS NULL
                   OR NOT coalesce(isfinite(lightgbm) AND lightgbm >= 0, false)
                   OR NOT coalesce(isfinite(seasonal_naive) AND seasonal_naive >= 0, false)
                   OR horizon IS DISTINCT FROM date_diff('day', ?, target_date))
        FROM sealed_forecasts GROUP BY horizon ORDER BY horizon
        """, [date.fromisoformat(spec["forecast_origin"])]).fetchall()
    pairs = spec["predictions_per_horizon"]
    expected = [(day, pairs, pairs, 0) for day in range(1, spec["horizons"] + 1)]
    if counts != expected:
        raise ValueError("The forecast seal requires both forecasts on every unique key of the seven-day panel.")
    panel_pairs = connection.execute(
        "SELECT count(DISTINCT (store_id, product_id)) FROM sealed_forecasts"
    ).fetchall()[0][0]
    if panel_pairs != pairs:
        raise ValueError("Each forecast horizon must cover the same store–product pairs.")


def refit(output, spec):
    start = perf_counter()
    import duckdb

    from .holdout_forecasts import refit_and_forecast

    output.mkdir(parents=True, exist_ok=False)
    prepare_plot_cache(output.parent)
    train = spec["protocol"]["dataset"]["train"]
    with duckdb.connect(config={**spec["selected_lightgbm"]["duckdb"],
                                "temp_directory": str(output / "refit-spill")}) as connection:
        train_hash = open_train(connection, ROOT / train["path"], train["sha256"])
        model, training, timings = refit_and_forecast(connection, spec)
        verify_forecast_panel(connection, spec)
        connection.execute("""
            COPY (SELECT * FROM sealed_forecasts ORDER BY store_id, product_id, target_date)
            TO ? (FORMAT PARQUET, COMPRESSION ZSTD)
            """, [str(output / "forecasts.parquet")])
        methods = dict(connection.execute("""
            SELECT baseline_method, count(*) FROM sealed_forecasts GROUP BY baseline_method
            """).fetchall())
    model.save_model(str(output / "model.txt"), num_iteration=spec["selected_iterations"])
    write_json(output / "config.json", spec)
    write_json(output / "feature-schema.json", feature_schema())
    write_environment(output / "refit-environment.json", start, timings)
    write_json(output / "forecast-seal.json", {
        "experiment_version": spec["experiment_version"],
        "forecast_origin": spec["forecast_origin"],
        "training": training, "seasonal_naive_method_counts": methods,
        "pairs_per_horizon": spec["predictions_per_horizon"],
        "forecast_rows": spec["predictions_per_horizon"] * spec["horizons"],
        "train_sha256_verified": train_hash,
        "sha256": {name: sha256_file(output / name) for name in SEALED_FILES},
    })


def verify_seal(output, spec):
    seal = read_json(output / "forecast-seal.json")
    if (
        seal["experiment_version"] != spec["experiment_version"]
        or seal["forecast_origin"] != spec["forecast_origin"]
        or seal["training"]["target_through"] != spec["refit_through"]
        or seal["train_sha256_verified"] != spec["protocol"]["dataset"]["train"]["sha256"]
        or seal["forecast_rows"] != spec["predictions_per_horizon"] * spec["horizons"]
        or set(seal["sha256"]) != set(SEALED_FILES)
    ):
        raise ValueError("Forecast seal does not match the frozen experiment.")
    for name in SEALED_FILES:
        if sha256_file(output / name) != seal["sha256"][name]:
            raise ValueError(f"Forecast seal checksum mismatch for {name}.")
    if (read_json(output / "config.json") != spec
            or canonical_digest(read_json(output / "feature-schema.json")) != spec["feature_schema_sha256"]):
        raise ValueError("Sealed configuration or schema differs from the frozen experiment.")
    return seal


def load_heldout_labels(connection, path, expected_hash):
    verified = sha256_file(path)
    if verified != expected_hash:
        raise ValueError("Official evaluation file SHA-256 does not match the audited release.")
    connection.execute(f"""
        CREATE TEMP TABLE scoring_labels AS SELECT {LABEL_COLUMNS} FROM read_parquet(?)
        """, [str(path)])
    return verified


def score_forecasts(connection, origin, expected):
    connection.execute("""
        CREATE TEMP VIEW model_predictions AS
        SELECT store_id, product_id, target_date, lightgbm AS prediction FROM sealed_forecasts;
        CREATE TEMP VIEW baseline_predictions AS
        SELECT store_id, product_id, target_date, seasonal_naive AS prediction FROM sealed_forecasts;
        """)
    check_keys(connection, expected)
    return {
        "seasonal_naive": scorecard(score_totals(connection, origin)),
        "lightgbm": scorecard(score_totals(connection, origin, "model_predictions")),
    }


def daily_totals(connection):
    rows = connection.execute("""
        SELECT CAST(target_date AS VARCHAR) AS forecast_date, l.stockout_24h, count(*),
               sum(l.actual), sum(f.seasonal_naive), sum(f.lightgbm)
        FROM scoring_labels l JOIN sealed_forecasts f USING (store_id, product_id, target_date)
        GROUP BY ALL ORDER BY forecast_date, l.stockout_24h
        """).fetchall()
    return [{"target_date": day, "stockout_24h": flag, "scored": count,
             "actual": round(actual, 8), "seasonal_naive": round(naive, 8), "lightgbm": round(model, 8)}
            for day, flag, count, actual, naive, model in rows]


def score(output, spec):
    start = perf_counter()
    import duckdb

    if (output / "scoring-started.json").exists():
        raise FileExistsError("Official holdout scoring has already been started for this sealed version.")
    seal = verify_seal(output, spec)
    heldout = spec["protocol"]["dataset"]["reserved_eval"]
    origin = date.fromisoformat(spec["forecast_origin"])
    with duckdb.connect(config=spec["selected_lightgbm"]["duckdb"]) as connection:
        connection.execute("""
            CREATE TEMP TABLE sealed_forecasts AS SELECT * FROM read_parquet(?)
            """, [str(output / "forecasts.parquet")])
        verify_forecast_panel(connection, spec)
        write_json(output / "scoring-started.json", {
            "forecast_seal_sha256": sha256_file(output / "forecast-seal.json"),
            "forecasts_sha256": seal["sha256"]["forecasts.parquet"],
            "expected_eval_sha256": heldout["sha256_from_audit"],
        })
        eval_hash = load_heldout_labels(connection, ROOT / heldout["path"], heldout["sha256_from_audit"])
        results = score_forecasts(connection, origin, seal["forecast_rows"])
        totals = daily_totals(connection)
    naive_wape = results["seasonal_naive"]["overall"]["all"]["wape_pct"]
    model_wape = results["lightgbm"]["overall"]["all"]["wape_pct"]
    report = {
        "experiment_version": spec["experiment_version"], "subset": "official_held_out_evaluation",
        "target": "observed_sales", "units": "normalized retail sales units",
        "forecast_origin": origin.isoformat(), "target_start": heldout["start"], "target_end": heldout["end"],
        "training": seal["training"], "selected_iterations": spec["selected_iterations"],
        "seasonal_naive_method_counts": seal["seasonal_naive_method_counts"],
        "dataset_hashes": {"train_sha256_verified": seal["train_sha256_verified"], "eval_sha256_verified": eval_hash},
        "forecasts_sha256": seal["sha256"]["forecasts.parquet"],
        "wape_improvement_percentage_points": round(naive_wape - model_wape, 10),
        **results, "daily_totals_by_24h_stockout": totals,
    }
    write_json(output / "metrics.json", report)
    write_json(output / "dataset-hashes.json", report["dataset_hashes"])
    prepare_plot_cache(output.parent)
    plot_comparison(output, report, score_key=None, scope="official holdout")
    plot_actuals(output, totals)
    write_environment(output / "score-environment.json", start)


def prepare_plot_cache(parent):
    cache = parent / "matplotlib-cache"
    cache.mkdir(exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache))


def write_environment(path, start, timings=None):
    import psutil

    memory = psutil.Process().memory_info()
    write_json(path, {
        "python": sys.version.split()[0],
        "packages": {name: version(name) for name in ("duckdb", "lightgbm", "matplotlib", "numpy", "pandas", "psutil", "scipy")},
        **(timings or {}), "elapsed_seconds": round(perf_counter() - start, 3),
        "current_rss_mib": round(memory.rss / 2**20, 2),
        "peak_working_set_mib": round(memory.peak_wset / 2**20, 2) if hasattr(memory, "peak_wset") else None,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("refit", "score"))
    args = parser.parse_args()
    try:
        if not OUTPUT.resolve().is_relative_to(ROOT / "instance"):
            raise ValueError("The holdout directory must resolve inside instance/.")
        stage = refit if args.stage == "refit" else score
        stage(OUTPUT, frozen_spec())
    except (ImportError, OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"Completed {args.stage}: {OUTPUT}")


if __name__ == "__main__":
    main()
