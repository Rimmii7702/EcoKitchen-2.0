import json
import unittest
from datetime import date, timedelta

import numpy as np

from forecasting.freshretailnet.gbm import check_keys, fit_model, matrix_from, predictions
from forecasting.freshretailnet.supervised import (
    FEATURES, build_supervised_features, define_pairs, feature_batch, load_model_history, training_origins,
)
from forecasting.freshretailnet.train_lightgbm import CONFIG

try:
    from duckdb import connect
except ImportError:
    connect = None

try:
    from lightgbm import Booster
except ImportError:
    Booster = None


FIRST = date(2024, 3, 28)
CUTOFF = date(2024, 6, 11)


class TrainingScheduleTests(unittest.TestCase):
    def test_all_training_targets_end_by_fit_cutoff(self):
        origins = training_origins(FIRST, date(2024, 4, 16), CUTOFF, 7)
        self.assertEqual(len(origins), 8)
        self.assertEqual(origins[-1], date(2024, 6, 4))
        self.assertEqual(origins[-1] + timedelta(days=7), CUTOFF)
        with self.assertRaisesRegex(ValueError, "by the fit cutoff"):
            training_origins(FIRST, date(2024, 6, 5), CUTOFF, 7)
        with self.assertRaisesRegex(ValueError, "14 completed"):
            training_origins(FIRST, FIRST, CUTOFF, 7)


@unittest.skipUnless(connect, "Install requirements-freshretailnet-lightgbm.txt for DuckDB feature tests.")
class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect()
        self.addCleanup(self.connection.close)
        self.origin = date(2024, 6, 4)
        self.connection.execute("""
            CREATE TABLE retail_train AS SELECT 1 AS store_id, 10 AS product_id,
                CAST(DATE '2024-05-21' + i::INTEGER AS VARCHAR) AS dt, i::DOUBLE AS sale_amount,
                3 AS city_id, 5 AS third_category_id, (i % 3)::INTEGER AS stock_hour6_22_cnt,
                i::DOUBLE AS avg_temperature, i::DOUBLE AS discount, 0 AS activity_flag
            FROM range(24) t(i)
            """)
        define_pairs(self.connection, FIRST, CUTOFF)

    def project(self, through):
        load_model_history(self.connection, FIRST, through)
        build_supervised_features(self.connection, self.origin)
        return feature_batch(self.connection)

    def test_calendar_lags_rollup_and_labels_align_with_origin(self):
        batch = self.project(CUTOFF)
        np.testing.assert_array_equal(batch["horizon"], list(range(1, 8)))
        np.testing.assert_array_equal(batch["sales_same_weekday"], list(range(8, 15)))
        np.testing.assert_array_equal(batch["target_weekday"], [3, 4, 5, 6, 7, 1, 2])
        self.assertEqual(batch["sales_origin"][0], 14)
        self.assertEqual(batch["sales_lag_1"][0], 13)
        self.assertEqual(batch["sales_lag_7"][0], 7)
        self.assertEqual(batch["sales_lag_14"][0], 0)
        self.assertEqual(batch["sales_mean_7"][0], 11)
        self.assertEqual(batch["sales_mean_14"][0], 7.5)
        self.assertEqual(batch["stockout_hours_origin"][0], 2)
        self.assertAlmostEqual(batch["stockout_hours_mean_7"][0], 8 / 7, places=6)
        self.assertAlmostEqual(batch["stockout_days_share_14"][0], 10 / 14, places=6)
        labeled = feature_batch(self.connection, fit_cutoff=CUTOFF)
        np.testing.assert_array_equal(labeled["target"], list(range(15, 22)))
        early_cutoff = feature_batch(self.connection, fit_cutoff=self.origin + timedelta(days=2))
        np.testing.assert_array_equal(early_cutoff["target"], [15, 16])

    def test_future_changes_do_not_change_features_or_keys(self):
        before = self.project(date(2024, 6, 13))
        self.connection.execute("""
            UPDATE retail_train SET sale_amount = 999, stock_hour6_22_cnt = 16, avg_temperature = -15,
                discount = 0, activity_flag = 1, city_id = 11, third_category_id = 12
            WHERE dt > '2024-06-04'
            """)
        self.connection.execute("""
            INSERT INTO retail_train VALUES (2, 99, '2024-06-09', 50, 3, 8, 16, 25, .8, 1)
            """)
        define_pairs(self.connection, FIRST, CUTOFF)
        after = self.project(date(2024, 6, 13))
        for column in before:
            np.testing.assert_array_equal(before[column], after[column])
        model = train_fixture_model() if Booster else None
        if model is not None:
            np.testing.assert_array_equal(predictions(model, matrix_from(before), 8, 2),
                                          predictions(model, matrix_from(after), 8, 2))

    def test_missing_sales_lag_stays_missing_instead_of_using_other_dates(self):
        self.connection.execute("DELETE FROM retail_train WHERE dt = '2024-05-28'")
        batch = self.project(CUTOFF)
        self.assertTrue(np.isnan(matrix_from(batch)[0, FEATURES.index("sales_lag_7")]))

    def test_identical_key_guard_rejects_equal_counts_with_wrong_or_duplicate_keys(self):
        for table, key in (("model_predictions", 2), ("baseline_predictions", 1), ("scoring_labels", 1)):
            value = "actual" if table == "scoring_labels" else "prediction"
            self.connection.execute(f"""
                CREATE TEMP TABLE {table} AS
                SELECT {key} AS store_id, 10 AS product_id, DATE '2024-06-12' AS target_date, 2 AS {value}
                """)
        with self.assertRaisesRegex(ValueError, "identical, unique"):
            check_keys(self.connection, 1)
        self.connection.execute("UPDATE model_predictions SET store_id = 1")
        check_keys(self.connection, 1)
        self.connection.execute("INSERT INTO scoring_labels SELECT * FROM scoring_labels")
        with self.assertRaisesRegex(ValueError, "identical, unique"):
            check_keys(self.connection, 1)


def train_fixture_model():
    params = json.loads(CONFIG.read_text(encoding="utf-8"))["lightgbm"]
    params.update(num_threads=2, min_data_in_leaf=2, num_leaves=4)
    values = np.arange(128, dtype=np.float32)
    data = np.column_stack([values % 3] * 5 + [values] * (len(FEATURES) - 5))
    return fit_model(data, values / 10, params, rounds=8)


@unittest.skipUnless(Booster, "Install requirements-freshretailnet-lightgbm.txt for model tests.")
class ModelTests(unittest.TestCase):
    def test_fixed_seed_and_checkpoint_reload_reproduce_predictions(self):
        one, two = train_fixture_model(), train_fixture_model()
        self.assertEqual(one.model_to_string(), two.model_to_string())
        checkpoint = Booster(model_str=one.model_to_string(num_iteration=4))
        self.assertEqual(checkpoint.current_iteration(), 4)
        inputs = np.repeat(np.array([[0], [64], [127]], dtype=np.float32), len(FEATURES), axis=1)
        expected = predictions(one, inputs, 4, 2)
        self.assertGreater(len(set(expected)), 1)
        np.testing.assert_array_equal(expected, predictions(checkpoint, inputs, 4, 2))
