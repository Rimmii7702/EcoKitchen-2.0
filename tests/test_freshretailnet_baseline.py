import json
import unittest
from datetime import date, timedelta
from hashlib import file_digest
from math import sqrt
from pathlib import Path
from uuid import uuid4

from forecasting.freshretailnet.__main__ import CONFIG, evaluate
from forecasting.freshretailnet.baseline import predict
from forecasting.freshretailnet.dataset import load_history, load_labels, open_train
from forecasting.freshretailnet.features import build_features
from forecasting.freshretailnet.scoring import score_totals, scorecard, summarize

try:
    from duckdb import connect
except ImportError:
    connect = None


ORIGIN = date(2024, 6, 11)
FIRST = date(2024, 3, 28)


class MetricTests(unittest.TestCase):
    def test_metrics_use_sums_and_correct_bias_direction(self):
        metrics = summarize([(2, 2, 3, 5, -1, 4), (1, 1, 0, 0, 0, 0)])
        self.assertEqual(metrics["scored"], 3)
        self.assertEqual(metrics["mae"], 1)
        self.assertAlmostEqual(metrics["rmse"], sqrt(5 / 3))
        self.assertEqual(metrics["wape_pct"], 75)
        self.assertEqual(metrics["signed_bias_pct"], -25)

    def test_zero_actuals_and_empty_cohort(self):
        self.assertIsNone(summarize([(1, 1, 2, 4, 2, 0)])["wape_pct"])
        self.assertIsNone(summarize([(1, 1, 2, 4, 2, 0)])["signed_bias_pct"])
        self.assertIsNone(summarize([])["mae"])
        self.assertEqual(summarize([(3, 0, None, None, None, None)])["unscored"], 3)


@unittest.skipUnless(connect, "Install requirements-freshretailnet.txt to run DuckDB baseline tests.")
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect()
        self.addCleanup(self.connection.close)
        self.connection.execute("""
            CREATE TABLE retail_train (
                store_id BIGINT, product_id BIGINT, dt VARCHAR, sale_amount DOUBLE,
                hours_sale DOUBLE[], hours_stock_status INTEGER[], stock_hour6_22_cnt INTEGER,
                avg_temperature DOUBLE, precpt DOUBLE, avg_humidity DOUBLE, avg_wind_level DOUBLE,
                discount DOUBLE, holiday_flag INTEGER, activity_flag INTEGER
            )
            """)

    def add(self, store, product, day, sale, stockout_hour=None):
        flags = [int(hour == stockout_hour) for hour in range(24)]
        self.connection.execute("""
            INSERT INTO retail_train VALUES (?, ?, ?, ?, ?, ?, ?, 30, 1, 40, 2, .9, 0, 0)
            """, [store, product, day, sale, [sale] + [0] * 23, flags, sum(flags[6:22])])

    def predictions(self, origin=ORIGIN):
        load_history(self.connection, FIRST, origin)
        build_features(self.connection, origin)
        predict(self.connection)
        return self.connection.execute("""
            SELECT store_id, product_id, target_date, horizon, prediction, method
            FROM baseline_predictions ORDER BY store_id, product_id, horizon
            """).fetchall()

    def test_date_alignment_and_new_origin_uses_completed_validation_history(self):
        for index in range(14):
            day = ORIGIN - timedelta(days=6) + timedelta(days=index)
            self.add(1, 10, day.isoformat(), index)
        first = self.predictions()
        second = self.predictions(date(2024, 6, 18))
        for index, (first_row, second_row) in enumerate(zip(first, second), start=1):
            self.assertEqual(first_row[2:], (ORIGIN + timedelta(days=index), index, index - 1, "previous_week"))
            self.assertEqual(second_row[2:], (ORIGIN + timedelta(days=index + 7), index, index + 6, "previous_week"))
        self.assertEqual((len(first), len(second)), (7, 7))

    def test_missing_history_falls_back_without_consulting_targets(self):
        for day, sale in (("2024-05-29", 1), ("2024-05-30", 4), ("2024-06-05", 2), ("2024-06-11", 8)):
            self.add(1, 10, day, sale)
        self.add(2, 10, "2024-06-11", None)
        self.add(3, 20, "2024-06-11", None)
        self.add(4, 99, "2024-06-12", 999)  # Unknown at issuance.
        values = {(store, horizon): (prediction, method)
                  for store, _, _, horizon, prediction, method in self.predictions()}
        self.assertEqual(values[1, 1], (2, "previous_week"))
        self.assertEqual(values[1, 2], (4, "earlier_same_weekday"))
        self.assertEqual(values[1, 3], (3.75, "pair_mean"))
        self.assertEqual(values[2, 1], (1.5, "product_weekday_mean"))
        self.assertEqual(values[3, 1], (1.5, "global_weekday_mean"))
        self.assertEqual(values[3, 3], (3.75, "global_mean"))
        self.assertNotIn((4, 1), values)

    def test_all_missing_observations_are_unavailable(self):
        self.add(1, 10, "2024-06-11", None)
        self.assertEqual([row[4:] for row in self.predictions()], [(None, "unavailable")] * 7)

    def test_future_sales_stock_weather_and_promotions_do_not_change_predictions(self):
        self.add(1, 10, "2024-06-05", 2)
        self.add(1, 10, "2024-06-12", 3)
        original = self.predictions()
        self.connection.execute("""
            UPDATE retail_train SET sale_amount = 1000, hours_sale = list_resize([1000.0], 24, 0.0),
                hours_stock_status = list_resize([1], 24, 1), stock_hour6_22_cnt = 16,
                avg_temperature = -10, precpt = 200, avg_humidity = 100, avg_wind_level = 9,
                discount = .1, holiday_flag = 1, activity_flag = 1
            WHERE dt > '2024-06-11'
            """)
        self.add(9, 29, "2024-06-12", 2000)
        self.assertEqual(self.predictions(), original)
        self.assertEqual(self.connection.execute("SELECT max(sale_date) FROM forecast_history").fetchone()[0],
                         date(2024, 6, 5))

    def test_labels_are_joined_after_prediction_and_stratified_by_both_windows(self):
        for store, old, actual, flag in [(1, 2, 1, None), (2, 1, 3, 6), (3, 0, 0, 2)]:
            self.add(store, 10, "2024-06-05", old)
            self.add(store, 10, "2024-06-12", actual, flag)
        self.add(4, 99, "2024-06-12", 7)
        self.predictions()
        load_labels(self.connection, date(2024, 6, 12), date(2024, 6, 18))
        scores = scorecard(score_totals(self.connection, ORIGIN))
        by_horizon = scores["by_horizon"]
        self.assertEqual(scores["overall"], by_horizon["1"])
        self.assertEqual(by_horizon["1"]["all"]["observations"], 4)
        self.assertEqual(by_horizon["1"]["all"]["scored"], 3)
        self.assertEqual(by_horizon["1"]["all"]["wape_pct"], 75)
        self.assertEqual(by_horizon["1"]["stockout_24h"]["observations"], 2)
        self.assertEqual(by_horizon["1"]["stockout_daytime"]["observations"], 1)
        self.assertEqual(by_horizon["2"]["all"]["observations"], 0)

    def test_rejects_reserved_evaluation_dates_before_loading(self):
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        config["fit_history"]["end"] = "2024-06-25"
        config["validation"] = [{"name": "bad", "origin": "2024-06-25", "start": "2024-06-26", "end": "2024-07-02"}]
        with self.assertRaisesRegex(ValueError, "precede reserved evaluation"):
            evaluate(self.connection, config)

    def test_parquet_loader_verifies_bytes_before_exposing_training_view(self):
        root = Path(__file__).resolve().parents[1] / "instance"
        root.mkdir(exist_ok=True)
        path = root / f"freshretailnet-test-{uuid4().hex}.parquet"
        self.addCleanup(path.unlink, missing_ok=True)
        self.add(1, 10, "2024-06-11", 2)
        self.connection.execute("COPY retail_train TO ? (FORMAT PARQUET)", [str(path)])
        self.connection.execute("DROP TABLE retail_train")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            open_train(self.connection, path, "wrong")
        with path.open("rb") as stream:
            digest = file_digest(stream, "sha256").hexdigest()
        self.assertEqual(open_train(self.connection, path, digest), digest)
        self.assertEqual(self.predictions()[6][4:], (2, "previous_week"))
