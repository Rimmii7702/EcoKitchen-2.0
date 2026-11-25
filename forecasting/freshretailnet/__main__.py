"""Validate an offline seasonal-naive retail sales baseline against two fixed windows."""

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

from .baseline import predict
from .dataset import load_history, load_labels, open_train, validation_windows
from .features import build_features
from .reporting import plot_validation, write_json
from .scoring import score_totals, scorecard


ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).with_name("validation-v1.json")


def evaluate(connection, config):
    fit_first = date.fromisoformat(config["fit_history"]["start"])
    expected_pairs = config["expected_pairs"]
    folds, pooled_rows, pooled_methods = {}, [], Counter()

    for fold, origin, first, last in validation_windows(config):
        load_history(connection, fit_first, origin)
        build_features(connection, origin)
        predict(connection)
        coverage = connection.execute("""
            SELECT horizon, count(*) FROM baseline_predictions GROUP BY horizon ORDER BY horizon
            """).fetchall()
        if coverage != [(horizon, expected_pairs) for horizon in range(1, 8)]:
            raise ValueError("Unexpected pair coverage at this validation origin.")
        methods = dict(connection.execute("""
            SELECT method, count(*) FROM baseline_predictions GROUP BY method
            """).fetchall())
        load_labels(connection, first, last)
        rows = score_totals(connection, origin)
        scores = scorecard(rows)
        if any(scores["by_horizon"][str(i)]["all"]["observations"] != expected_pairs for i in range(1, 8)):
            raise ValueError("Unexpected validation label coverage.")

        folds[fold["name"]] = {**fold, "method_counts": methods, **scores}
        pooled_rows.extend(rows)
        pooled_methods.update(methods)

    return {
        "experiment_version": config["experiment_version"],
        "target": "observed_sales",
        "units": "normalized retail sales units",
        "official_eval_status": "reserved_unread",
        "folds": folds,
        "pooled_validation": {"method_counts": dict(pooled_methods), **scorecard(pooled_rows)},
    }


def run(run_name):
    import duckdb
    import psutil

    start = perf_counter()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    output_root = ROOT / "instance" / "freshretailnet"
    output = output_root / run_name
    if not output.resolve().is_relative_to(ROOT / "instance"):
        raise ValueError("The run directory must resolve inside instance/.")
    output.mkdir(parents=True, exist_ok=False)
    train = config["dataset"]["train"]
    with duckdb.connect(config={**config["duckdb"], "temp_directory": str(output / "spill")}) as connection:
        actual_hash = open_train(connection, ROOT / train["path"], train["sha256"])
        report = evaluate(connection, config)

    write_json(output / "config.json", config)
    write_json(output / "dataset-hashes.json", {
        "train_sha256_verified": actual_hash,
        "reserved_eval_sha256_from_prior_audit": config["dataset"]["reserved_eval"]["sha256_from_audit"],
    })
    write_json(output / "metrics.json", report)
    cache = output_root / "matplotlib-cache"
    cache.mkdir(exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache))
    plot_validation(output, report)
    memory = psutil.Process().memory_info()
    write_json(output / "run-environment.json", {
        "python": sys.version.split()[0],
        "packages": {name: version(name) for name in ("duckdb", "numpy", "matplotlib", "psutil")},
        "elapsed_seconds": round(perf_counter() - start, 3),
        "current_rss_mib": round(memory.rss / 2**20, 2),
        "peak_working_set_mib": round(memory.peak_wset / 2**20, 2) if hasattr(memory, "peak_wset") else None,
    })
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-name", required=True, help="New run name under instance/freshretailnet/")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", args.run_name):
        parser.error("Run name must be 1 to 64 letters, digits, hyphens or underscores, starting with a letter or digit.")
    try:
        output = run(args.run_name)
    except (ImportError, OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"Validation results written to {output}")


if __name__ == "__main__":
    main()
