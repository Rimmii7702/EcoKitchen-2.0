"""Train and evaluate locally. Output directories are write-once."""

import argparse
import json
from pathlib import Path

from .data import audit_sales_sample
from .demand import train_demand
from .waste import train_waste_synthetic


def write_json(path, data):
    with path.open("x", encoding="utf-8", newline="\n") as file:
        json.dump(data, file, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        file.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("audit-demand", "demand", "waste-synthetic"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True, help="New output directory")
        if name == "demand":
            command.add_argument("--input", type=Path, required=True, help="Verified complete weekly sales CSV")
            command.add_argument("--source-name", required=True, help="Name of the verified export")
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise FileExistsError(f"Output already exists: {args.output}. Choose a new directory.")
        if args.command == "audit-demand":
            report, artifact = audit_sales_sample(), None
        else:
            artifact = train_demand(args.input, args.source_name) if args.command == "demand" else train_waste_synthetic()
            report = artifact["report"]
        args.output.mkdir(parents=True, exist_ok=False)
        write_json(args.output / "evaluation.json", report)
        if artifact is not None:
            write_json(args.output / "model.json", artifact)
    except (OSError, ValueError, ImportError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()