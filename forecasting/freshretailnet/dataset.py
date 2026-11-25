"""Project Parquet reads into separate historical inputs and scoring labels."""

from datetime import date, timedelta
from hashlib import file_digest


LABEL_COLUMNS = """
    store_id, product_id, CAST(dt AS DATE) AS target_date, sale_amount AS actual,
    list_contains(hours_stock_status, 1) AS stockout_24h,
    stock_hour6_22_cnt > 0 AS stockout_daytime
"""


def sha256_file(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def open_train(connection, path, expected_hash):
    actual_hash = sha256_file(path)
    if actual_hash != expected_hash:
        raise ValueError("train.parquet SHA-256 does not match the audited release.")
    connection.read_parquet(str(path)).create_view("retail_train")
    return actual_hash


def load_history(connection, first_day, origin, *, study_only=False):
    scope = "SEMI JOIN study_pairs USING (store_id, product_id)" if study_only else ""
    connection.execute(f"""
        CREATE OR REPLACE TEMP TABLE forecast_history AS
        SELECT store_id, product_id, CAST(dt AS DATE) AS sale_date, sale_amount
        FROM retail_train {scope}
        WHERE dt BETWEEN ? AND ?
        """, [first_day.isoformat(), origin.isoformat()])


def load_labels(connection, first_day, last_day, *, study_only=False):
    scope = "SEMI JOIN study_pairs USING (store_id, product_id)" if study_only else ""
    connection.execute(f"""
        CREATE OR REPLACE TEMP TABLE scoring_labels AS
        SELECT {LABEL_COLUMNS}
        FROM retail_train {scope}
        WHERE dt BETWEEN ? AND ?
        """, [first_day.isoformat(), last_day.isoformat()])


def validation_windows(config):
    expected_origin = date.fromisoformat(config["fit_history"]["end"])
    reserved_start = date.fromisoformat(config["dataset"]["reserved_eval"]["start"])
    for fold in config["validation"]:
        origin, first, last = (date.fromisoformat(fold[key]) for key in ("origin", "start", "end"))
        if (origin != expected_origin or first != origin + timedelta(days=1)
                or last != origin + timedelta(days=7) or last >= reserved_start):
            raise ValueError("Validation dates must follow their fixed origin and precede reserved evaluation.")
        yield fold, origin, first, last
        expected_origin = last
