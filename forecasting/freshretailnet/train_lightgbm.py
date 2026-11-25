"""Train a fixed-seed CPU LightGBM model and score only the two validation weeks."""

import argparse
import json
import os
import re
import sys
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

from .dataset import open_train
from .reporting import plot_comparison, write_json
from .supervised import feature_schema


ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).with_name("lightgbm-v1.json")


def run(run_name, pilot):
    start = perf_counter()
    import duckdb
    import psutil

    from .gbm import experiment

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    protocol = json.loads(CONFIG.with_name(config["protocol"]).read_text(encoding="utf-8"))
    output_root = ROOT / "instance" / "freshretailnet"
    output = output_root / run_name
    if not output.resolve().is_relative_to(ROOT / "instance"):
        raise ValueError("The run directory must resolve inside instance/.")
    output.mkdir(parents=True, exist_ok=False)
    cache = output_root / "matplotlib-cache"
    cache.mkdir(exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache))

    train = protocol["dataset"]["train"]
    with duckdb.connect(config={**config["duckdb"], "temp_directory": str(output / "spill")}) as connection:
        digest = open_train(connection, ROOT / train["path"], train["sha256"])
        model, report, timings = experiment(connection, config, protocol, pilot)
    model.save_model(str(output / "model.txt"), num_iteration=report["selected_iterations"])

    write_json(output / "config.json", {"experiment": config, "validation_protocol": protocol})
    write_json(output / "feature-schema.json", feature_schema())
    write_json(output / "dataset-hashes.json", {
        "train_sha256_verified": digest,
        "reserved_eval_sha256_from_prior_audit": protocol["dataset"]["reserved_eval"]["sha256_from_audit"],
    })
    write_json(output / "metrics.json", report)
    plot_comparison(output, report)

    memory = psutil.Process().memory_info()
    write_json(output / "run-environment.json", {
        "python": sys.version.split()[0],
        "packages": {name: version(name) for name in (
            "duckdb", "lightgbm", "matplotlib", "numpy", "pandas", "psutil", "scipy",
        )},
        **timings, "elapsed_seconds": round(perf_counter() - start, 3),
        "current_rss_mib": round(memory.rss / 2**20, 2),
        "peak_working_set_mib": round(memory.peak_wset / 2**20, 2) if hasattr(memory, "peak_wset") else None,
    })
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-name", required=True, help="New output directory under instance/freshretailnet/")
    parser.add_argument("--pilot", action="store_true", help="Use a fixed approximately 5%% pair sample")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", args.run_name):
        parser.error("Run name must be 1 to 64 letters, digits, hyphens or underscores, starting with a letter or digit.")
    try:
        output = run(args.run_name, args.pilot)
    except (ImportError, OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"LightGBM validation written to {output}")


if __name__ == "__main__":
    main()
