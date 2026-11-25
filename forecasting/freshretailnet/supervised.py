"""Origin-bounded features and separately joined observed-sales training labels."""

from datetime import timedelta


CATEGORIES = ("store_id", "product_id", "city_id", "third_category_id", "target_weekday")
FEATURES = (
    *CATEGORIES, "horizon",
    "sales_origin", "sales_lag_1", "sales_lag_7", "sales_lag_14", "sales_same_weekday",
    "sales_mean_7", "sales_mean_14", "sales_stddev_14", "sales_zero_share_14",
    "stockout_hours_origin", "stockout_hours_mean_7", "stockout_days_share_14",
)
KEYS = ("store_id", "product_id", "target_date")


def training_origins(first_history, first_origin, cutoff, stride_days):
    if first_origin < first_history + timedelta(days=14) or stride_days < 1:
        raise ValueError("Training origins require 14 completed historical lag days and a positive stride.")
    last = cutoff - timedelta(days=7)
    if first_origin > last:
        raise ValueError("Every training origin must have seven labels by the fit cutoff.")
    return list(first_origin + timedelta(days=offset) for offset in range(0, (last - first_origin).days + 1, stride_days))


def define_pairs(connection, history_start, fit_cutoff, pilot=None, seed=42):
    connection.execute("""
        CREATE OR REPLACE TEMP TABLE study_pairs AS
        SELECT DISTINCT store_id, product_id FROM retail_train
        WHERE dt BETWEEN $start AND $end
          AND hash(store_id, product_id, $seed) % $modulus = $remainder
        """, {"start": history_start.isoformat(), "end": fit_cutoff.isoformat(), "seed": seed,
              "modulus": pilot["pair_hash_modulus"] if pilot else 1,
              "remainder": pilot["pair_hash_remainder"] if pilot else 0})
    return connection.execute("SELECT count(*) FROM study_pairs").fetchone()[0]


def load_model_history(connection, history_start, cutoff):
    connection.execute("""
        CREATE OR REPLACE TEMP TABLE model_history AS
        SELECT r.store_id, r.product_id, CAST(dt AS DATE) AS sale_date, sale_amount, city_id,
               third_category_id, stock_hour6_22_cnt AS stockout_hours
        FROM retail_train r SEMI JOIN study_pairs USING (store_id, product_id)
        WHERE dt BETWEEN ? AND ?
        """, [history_start.isoformat(), cutoff.isoformat()])


def build_supervised_features(connection, origin):
    connection.execute("""
        CREATE OR REPLACE TEMP TABLE model_features AS
        WITH history AS (
            SELECT * FROM model_history WHERE sale_date BETWEEN $origin - 14 AND $origin
        ), origin_stats AS (
            SELECT store_id, product_id, arg_max(city_id, sale_date) AS city_id,
                   arg_max(third_category_id, sale_date) AS third_category_id,
                   max(sale_amount) FILTER (WHERE sale_date = $origin) AS sales_origin,
                   max(sale_amount) FILTER (WHERE sale_date = $origin - 1) AS sales_lag_1,
                   max(sale_amount) FILTER (WHERE sale_date = $origin - 7) AS sales_lag_7,
                   max(sale_amount) FILTER (WHERE sale_date = $origin - 14) AS sales_lag_14,
                   avg(sale_amount) FILTER (WHERE sale_date >= $origin - 6) AS sales_mean_7,
                   avg(sale_amount) FILTER (WHERE sale_date >= $origin - 13) AS sales_mean_14,
                   stddev_pop(sale_amount) FILTER (WHERE sale_date >= $origin - 13) AS sales_stddev_14,
                   avg(CAST(sale_amount = 0 AS INTEGER)) FILTER (WHERE sale_date >= $origin - 13) AS sales_zero_share_14,
                   max(stockout_hours) FILTER (WHERE sale_date = $origin) AS stockout_hours_origin,
                   avg(stockout_hours) FILTER (WHERE sale_date >= $origin - 6) AS stockout_hours_mean_7,
                   avg(CAST(stockout_hours > 0 AS INTEGER)) FILTER (WHERE sale_date >= $origin - 13) AS stockout_days_share_14
            FROM history GROUP BY store_id, product_id
        ), dates AS (
            SELECT h::INTEGER AS horizon, $origin + h::INTEGER AS target_date FROM range(1, 8) AS offsets(h)
        )
        SELECT stats.*, dates.*, isodow(target_date) AS target_weekday, weekly.sale_amount AS sales_same_weekday
        FROM origin_stats stats CROSS JOIN dates
        LEFT JOIN history weekly ON weekly.store_id = stats.store_id AND weekly.product_id = stats.product_id
                                AND weekly.sale_date = dates.target_date - 7
        """, {"origin": origin})


def feature_batch(connection, fit_cutoff=None):
    columns = ", ".join(f"CAST(f.{name} AS FLOAT) AS {name}" for name in FEATURES)
    if fit_cutoff is None:
        return connection.execute(f"""
            SELECT f.store_id AS key_store, f.product_id AS key_product, f.target_date, {columns}
            FROM model_features f ORDER BY f.store_id, f.product_id, f.target_date
            """).fetchnumpy()
    return connection.execute(f"""
        SELECT {columns}, CAST(t.sale_amount AS FLOAT) AS target
        FROM model_features f JOIN model_history t ON t.store_id = f.store_id AND t.product_id = f.product_id
                                            AND t.sale_date = f.target_date
        WHERE f.target_date <= ? AND t.sale_amount IS NOT NULL
        ORDER BY f.store_id, f.product_id, f.target_date
        """, [fit_cutoff]).fetchnumpy()


def feature_schema():
    return {
        "features_in_order": list(FEATURES),
        "categorical_features": list(CATEGORIES),
        "matrix_dtype": "float32",
        "categorical_encoding": "Nonnegative source IDs as exact float32 integers, plus ISO weekday.",
        "missing_numeric": "NaN, handled by LightGBM",
        "keys": list(KEYS),
        "rolling_windows": "Inclusive of origin, 7 or 14 observed calendar days",
        "lag_reference": "Days before origin, except sales_same_weekday = target date minus seven days",
        "stockout_definition": "Historical 06:00–21:59 stockout counts; target flags are scoring-only",
        "target": "Normalized observed daily sales",
        "prediction_floor": 0,
    }
