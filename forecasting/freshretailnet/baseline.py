"""Materialize seven seasonal-naive forecasts per pair known at the origin."""


def predict(connection):
    connection.execute("""
        CREATE OR REPLACE TEMP TABLE baseline_predictions AS
        SELECT store_id, product_id, target_date, horizon,
               coalesce(latest_weekday_sales, pair_mean, product_weekday_mean,
                        global_weekday_mean, global_mean) AS prediction,
               CASE
                   WHEN latest_weekday_date = target_date - 7 THEN 'previous_week'
                   WHEN latest_weekday_date IS NOT NULL THEN 'earlier_same_weekday'
                   WHEN pair_mean IS NOT NULL THEN 'pair_mean'
                   WHEN product_weekday_mean IS NOT NULL THEN 'product_weekday_mean'
                   WHEN global_weekday_mean IS NOT NULL THEN 'global_weekday_mean'
                   WHEN global_mean IS NOT NULL THEN 'global_mean'
                   ELSE 'unavailable'
               END AS method
        FROM baseline_features
        """)
