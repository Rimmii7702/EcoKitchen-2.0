"""Read demo sales and operator-verified weekly histories without changing the source files."""

import csv
from collections import Counter
from datetime import date, datetime, timedelta
from hashlib import sha256
from io import StringIO
from pathlib import Path

from runtime_config import DATA_DIR


MIN_WEEKS = 26
WEEK = timedelta(days=7)
SALES_SAMPLE = DATA_DIR / "fictional-weekly-sales.csv"


def read_csv(path, required):
    path = Path(path)
    content = path.read_bytes()
    with StringIO(content.decode("utf-8-sig"), newline="") as source:
        reader = csv.DictReader(source, strict=True)
        header = reader.fieldnames or []
        if not required.issubset(header):
            raise ValueError("Expected CSV columns: " + ", ".join(sorted(required)))
        if len(header) != len(set(header)):
            raise ValueError("CSV column names must be unique.")
        rows = list(reader)
    if not rows:
        raise ValueError("The CSV has no data rows.")
    if any(None in row or any(row[column] is None for column in required) for row in rows):
        raise ValueError("CSV rows must contain the declared fields.")
    return rows, sha256(content).hexdigest()


def units(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 9:
        raise ValueError("Units must be nonnegative whole numbers with at most nine digits.")
    return int(value)


def audit_sales_sample(path=SALES_SAMPLE):
    rows, digest = read_csv(path, {"Week", "Dish Name", "Quantity Sold"})
    periods, dishes = {}, Counter()
    for row in rows:
        dates = row["Week"].split(" to ")
        if len(dates) != 2:
            raise ValueError("Expected a start and end date for each sample period.")
        start, end = (datetime.strptime(value, "%d-%b-%Y").date() for value in dates)
        if end < start:
            raise ValueError("A sample period ends before it starts.")
        key = (start, end)
        entry = periods.setdefault(key, {"units": 0, "row_count": 0})
        entry["units"] += units(row["Quantity Sold"])
        entry["row_count"] += 1
        dishes[row["Dish Name"]] += 1

    return {
        "schema_version": 1,
        "task": "demand_audit",
        "source": {
            "name": Path(path).name, "kind": "self_made_demo", "sha256": digest,
            "provenance": "Independently generated fictional public-release fixture.",
        },
        "coverage": {
            "rows": len(rows), "periods": len(periods), "distinct_dishes": len(dishes),
            "max_rows_per_dish": max(dishes.values()),
            "min_rows_per_period": min(row["row_count"] for row in periods.values()),
            "max_rows_per_period": max(row["row_count"] for row in periods.values()),
            "short_periods": sum(end - start != timedelta(days=6) for start, end in periods),
        },
        "history": [
            {"week_start": start.isoformat(), "week_end": end.isoformat(), **row,
             "complete_period": end - start == timedelta(days=6)}
            for (start, end), row in sorted(periods.items())
        ],
        "status": "unavailable",
        "reason": "The fictional sales sample has uneven dish coverage and a partial period. A demand forecast needs complete historical weekly totals.",
    }


def read_weekly_totals(path):
    rows, digest = read_csv(path, {"week_start", "units_sold"})
    timeline = []
    for row in rows:
        try:
            start = date.fromisoformat(row["week_start"])
        except (ValueError, TypeError):
            raise ValueError("Each week_start must be an ISO date (YYYY-MM-DD).") from None
        if start.isoformat() != row["week_start"] or start.weekday() != 0:
            raise ValueError("Each week_start must be a Monday in YYYY-MM-DD format.")
        timeline.append((start, units(row["units_sold"])))
    timeline.sort()
    if len(timeline) < MIN_WEEKS:
        raise ValueError(f"Provide at least {MIN_WEEKS} complete weeks.")
    if any(later[0] - earlier[0] != WEEK for earlier, later in zip(timeline, timeline[1:])):
        raise ValueError("Supply exactly one record per consecutive week. Gaps and duplicates are rejected.")
    if timeline[-1][0] + timedelta(days=6) >= date.today():
        raise ValueError("Include only completed historical weeks.")
    return timeline, digest
