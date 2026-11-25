import unittest
from contextlib import ExitStack
from copy import deepcopy
from datetime import date
from shutil import rmtree
from unittest.mock import Mock, patch
from uuid import uuid4

import numpy as np

from forecasting.freshretailnet import holdout
from forecasting.freshretailnet.dataset import sha256_file
from forecasting.freshretailnet.holdout_forecasts import forecast, refit_and_forecast
from forecasting.freshretailnet.supervised import FEATURES, define_pairs, training_origins

try:
    from duckdb import connect
except ImportError:
    connect = None


ORIGIN = date(2024, 6, 25)
FIRST = date(2024, 3, 28)


class HoldoutProtocolTests(unittest.TestCase):
    def test_final_schedule_ends_with_june_18_origin_and_june_25_labels(self):
        spec = holdout.frozen_spec()
        schedule = spec["selected_lightgbm"]["training_origins"]
        origins = training_origins(FIRST, date.fromisoformat(schedule["first"]), ORIGIN, schedule["stride_days"])
        self.assertEqual(len(origins), 10)
        self.assertEqual(origins[-2:], [date(2024, 6, 11), date(2024, 6, 18)])
        self.assertEqual(spec["selected_iterations"], 96)
        self.assertEqual(spec["forecast_origin"], ORIGIN.isoformat())

    def test_configuration_changes_cannot_reselect_or_change_the_checkpoint(self):
        read_json = holdout.read_json

        def changed(path):
            data = read_json(path)
            if path.name == "lightgbm-v1.json":
                data["lightgbm"]["num_leaves"] += 1
            return data

        with patch.object(holdout, "read_json", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "Frozen validation_config checksum"):
                holdout.frozen_spec()

    def test_refit_fits_once_at_frozen_iteration_count(self):
        spec = holdout.frozen_spec()
        model = Mock()
        model.current_iteration.return_value = 96
        with (
            patch("forecasting.freshretailnet.holdout_forecasts.define_pairs", return_value=50_000),
            patch("forecasting.freshretailnet.holdout_forecasts.training_matrix",
                  return_value=(np.empty((1, len(FEATURES))), [2], [date(2024, 6, 18)])) as training,
            patch("forecasting.freshretailnet.holdout_forecasts.fit_model", return_value=model) as fit,
            patch("forecasting.freshretailnet.holdout_forecasts.forecast") as issue,
            patch("forecasting.freshretailnet.gbm.evaluate_models", side_effect=AssertionError("Model selection")),
        ):
            refit_and_forecast(Mock(), spec)
        self.assertEqual(training.call_args.args[2:4], (FIRST, ORIGIN))
        fit.assert_called_once()
        self.assertEqual(fit.call_args.args[2:], (spec["selected_lightgbm"]["lightgbm"], 96))
        issue.assert_called_once()
        self.assertEqual(issue.call_args.args[3:5], (ORIGIN, 96))


class FixtureModel:
    def predict(self, matrix, **_):
        return matrix[:, FEATURES.index("sales_origin")] + matrix[:, FEATURES.index("horizon")]

    def save_model(self, path, **_):
        from pathlib import Path

        Path(path).write_text("test-model\n", encoding="utf-8")


@unittest.skipUnless(connect, "Install requirements-freshretailnet-lightgbm.txt for holdout integration tests.")
class HoldoutIsolationTests(unittest.TestCase):
    def setUp(self):
        self.directory = holdout.ROOT / "instance" / f"test-freshretailnet-holdout-{uuid4().hex}"
        self.directory.mkdir(parents=True)
        self.addCleanup(rmtree, self.directory)
        self.spec = holdout.frozen_spec()
        self.spec["predictions_per_horizon"] = 1
        train = self.directory / "train.parquet"
        with connect() as connection:
            connection.execute("""
                COPY (SELECT 1 AS store_id, 20 AS product_id, 3 AS city_id, 4 AS third_category_id,
                             CAST(DATE '2024-06-05' + i::INTEGER AS VARCHAR) AS dt, i::DOUBLE AS sale_amount,
                             0 AS stock_hour6_22_cnt
                      FROM range(21) t(i)) TO ? (FORMAT PARQUET)
                """, [str(train)])
        self.spec["protocol"]["dataset"]["train"] = {"path": train.name, "sha256": sha256_file(train)}

    def heldout(self, name, actual, stockout):
        path = self.directory / name
        with connect() as connection:
            connection.execute("""
                COPY (SELECT 1 AS store_id, 20 AS product_id,
                             CAST(DATE '2024-06-25' + i::INTEGER AS VARCHAR) AS dt,
                             (? + i)::DOUBLE AS sale_amount,
                             list_resize([?::INTEGER], 24, 0) AS hours_stock_status,
                             0 AS stock_hour6_22_cnt, ?::DOUBLE AS avg_temperature,
                             ?::DOUBLE AS discount, ?::INTEGER AS activity_flag
                      FROM range(1, 8) t(i)) TO ? (FORMAT PARQUET)
                """, [actual, stockout, actual, actual / 100, stockout, str(path)])
        spec = deepcopy(self.spec)
        spec["protocol"]["dataset"]["reserved_eval"].update(path=path.name, sha256_from_audit=sha256_file(path))
        return spec

    def seal(self, output, spec):
        def fixture_refit(connection, _):
            define_pairs(connection, FIRST, ORIGIN)
            model = FixtureModel()
            forecast(connection, model, FIRST, ORIGIN, 96, 2)
            return model, {"target_through": ORIGIN.isoformat()}, {}

        with (
            patch.object(holdout, "ROOT", self.directory),
            patch.object(holdout, "load_heldout_labels", side_effect=AssertionError("Read evaluation during refit")),
            patch("forecasting.freshretailnet.holdout_forecasts.refit_and_forecast", side_effect=fixture_refit),
            patch.object(holdout, "write_environment"),
        ):
            holdout.refit(output, spec)

    def score(self, output, spec):
        load = holdout.load_heldout_labels

        def after_seal(connection, path, expected):
            marker = holdout.read_json(output / "scoring-started.json")
            self.assertEqual(marker["forecasts_sha256"], sha256_file(output / "forecasts.parquet"))
            return load(connection, path, expected)

        with ExitStack() as stack:
            stack.enter_context(patch.object(holdout, "ROOT", self.directory))
            reader = stack.enter_context(patch.object(holdout, "load_heldout_labels", side_effect=after_seal))
            for name in ("plot_actuals", "plot_comparison", "write_environment"):
                stack.enter_context(patch.object(holdout, name))
            for name in ("fit_model", "evaluate_models"):
                stack.enter_context(patch(f"forecasting.freshretailnet.gbm.{name}", side_effect=AssertionError(name)))
            holdout.score(output, spec)
            reader.assert_called_once()
        return holdout.read_json(output / "metrics.json")

    def test_changed_holdout_outcomes_change_scores_but_never_forecast_bytes(self):
        spec1 = self.heldout("eval-a.parquet", 4, 0)
        spec2 = self.heldout("eval-b.parquet", 22, 1)
        one, two = self.directory / "one", self.directory / "two"
        self.seal(one, spec1)
        self.seal(two, spec2)
        expected = sha256_file(one / "forecasts.parquet")
        self.assertEqual(expected, sha256_file(two / "forecasts.parquet"))
        first, second = self.score(one, spec1), self.score(two, spec2)
        self.assertEqual(first["forecasts_sha256"], second["forecasts_sha256"])
        self.assertEqual(sha256_file(one / "forecasts.parquet"), expected)
        self.assertEqual(sha256_file(two / "forecasts.parquet"), expected)
        self.assertNotEqual(first["lightgbm"]["overall"]["all"]["mae"], second["lightgbm"]["overall"]["all"]["mae"])
        self.assertEqual(first["lightgbm"]["overall"]["in_stock_24h"]["scored"], 7)
        self.assertEqual(second["lightgbm"]["overall"]["stockout_24h"]["scored"], 7)
        self.assertEqual((first["selected_iterations"], second["selected_iterations"]), (96, 96))
        with patch.object(holdout, "load_heldout_labels") as reader:
            with self.assertRaises(FileExistsError):
                holdout.score(one, spec1)
            reader.assert_not_called()

    def test_modified_forecast_refuses_to_open_holdout(self):
        spec = self.heldout("eval.parquet", 4, 0)
        output = self.directory / "run"
        self.seal(output, spec)
        with (output / "forecasts.parquet").open("ab") as stream:
            stream.write(b"modified")
        with patch.object(holdout, "load_heldout_labels") as reader:
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                holdout.score(output, spec)
            reader.assert_not_called()
        self.assertFalse((output / "scoring-started.json").exists())

    def test_wrong_evaluation_checksum_prevents_parquet_loading(self):
        path = self.directory / "eval.parquet"
        self.heldout(path.name, 4, 0)
        connection = Mock()
        with self.assertRaisesRegex(ValueError, "evaluation file SHA-256"):
            holdout.load_heldout_labels(connection, path, "wrong")
        connection.execute.assert_not_called()
