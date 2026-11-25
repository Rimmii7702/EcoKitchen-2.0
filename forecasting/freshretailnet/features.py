"""Build a fixed-origin feature table from historical rows alone."""


def build_features(connection, origin):
    connection.execute("""
        CREATE OR REPLACE TEMP TABLE baseline_features AS
        WITH history AS (
            SELECT store_id, product_id, sale_date, sale_amount, isodow(sale_date) AS weekday
            FROM forecast_history
            WHERE sale_date <= $origin
        ), pairs AS (
            SELECT store_id, product_id, avg(sale_amount) AS pair_mean
            FROM history
            GROUP BY store_id, product_id
        ), pair_weekdays AS (
            SELECT store_id, product_id, weekday,
                   arg_max(sale_amount, sale_date) AS latest_weekday_sales,
                   max(sale_date) FILTER (WHERE sale_amount IS NOT NULL) AS latest_weekday_date
            FROM history
            GROUP BY store_id, product_id, weekday
        ), product_weekdays AS (
            SELECT product_id, weekday, avg(sale_amount) AS product_weekday_mean
            FROM history
            GROUP BY product_id, weekday
        ), global_weekdays AS (
            SELECT weekday, avg(sale_amount) AS global_weekday_mean
            FROM history
            GROUP BY weekday
        ), global_history AS (
            SELECT avg(sale_amount) AS global_mean FROM history
        ), dates AS (
            SELECT horizon::INTEGER AS horizon, $origin + horizon::INTEGER AS target_date
            FROM range(1, 8) AS offsets(horizon)
        )
        SELECT pairs.store_id, pairs.product_id, dates.target_date, dates.horizon,
               pw.latest_weekday_sales, pw.latest_weekday_date, pairs.pair_mean,
               product_weekdays.product_weekday_mean, global_weekdays.global_weekday_mean,
               global_history.global_mean
        FROM pairs CROSS JOIN dates CROSS JOIN global_history
        LEFT JOIN pair_weekdays pw ON pw.store_id = pairs.store_id AND pw.product_id = pairs.product_id
            AND pw.weekday = isodow(dates.target_date)
        LEFT JOIN product_weekdays ON product_weekdays.product_id = pairs.product_id
            AND product_weekdays.weekday = isodow(dates.target_date)
        LEFT JOIN global_weekdays ON global_weekdays.weekday = isodow(dates.target_date)
        """, {"origin": origin})
