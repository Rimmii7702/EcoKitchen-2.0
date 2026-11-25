import json
import shutil
import unittest
from datetime import date, timedelta
from importlib.util import find_spec
from unittest.mock import patch
from uuid import uuid4

from auth_fixture import authenticated_client

from app import app
from forecasting.data import audit_sales_sample, read_weekly_totals
from forecasting.demand import predict_demand, train_demand
from forecasting.train import write_json
from forecasting.waste import predict_waste, synthetic_samples, train_waste_synthetic
from runtime_config import ROOT


API = "/api/forecasting"
SCENARIO = {
    "stock_quantity": 160, "sales_volume": 85, "days_to_expire": 6,
    "unit_price": 5.5, "inventory_turnover_rate": 0.6, "category": "Fruit",
}


class ForecastingTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "instance" / ("test-forecast-" + uuid4().hex)
        self.directory.mkdir()
        self.addCleanup(shutil.rmtree, self.directory)
        self.demand_path = self.directory / "demand.json"
        self.waste_path = self.directory / "waste.json"
        config = patch.dict(app.config, {
            "FORECAST_DEMAND_ARTIFACT": self.demand_path,
            "FORECAST_WASTE_SIM_ARTIFACT": self.waste_path,
        })
        config.start()
        self.addCleanup(config.stop)
        self.client = authenticated_client(self, app)

    def weekly_file(self, name, values, *, gap_at=None):
        start = date(2025, 1, 6)
        path = self.directory / name
        rows = ["week_start,units_sold"]
        for index, amount in enumerate(values):
            week = start + timedelta(weeks=index + int(gap_at is not None and index >= gap_at))
            rows.append(f"{week},{amount}")
        path.write_text("\n".join(rows), encoding="utf-8")
        return path

    def test_repository_sales_are_audited_but_not_forecast(self):
        checked_in = audit_sales_sample()
        self.assertEqual(checked_in["coverage"], {
            "rows": 3, "periods": 2, "distinct_dishes": 2, "max_rows_per_dish": 2,
            "min_rows_per_period": 1, "max_rows_per_period": 2, "short_periods": 1,
        })
        self.assertEqual([row["units"] for row in checked_in["history"]], [9, 27])
        self.assertEqual(checked_in["source"]["kind"], "self_made_demo")
        response = self.client.get(API).json
        self.assertFalse(response["demand"]["available"])
        self.assertFalse(response["waste"]["available"])
        self.assertFalse(response["waste"]["simulation_available"])
        self.assertEqual(response["demand"]["history"], checked_in["history"])
        self.assertFalse(response["demand"]["history"][0]["complete_period"])
        self.assertEqual(self.client.post(API + "/demand/predict", json={"horizon_weeks": 1}).status_code, 503)
        self.assertEqual(self.client.post(API + "/waste-simulation/predict", json=SCENARIO).status_code, 503)

    def test_invalid_requests_and_origin_are_rejected(self):
        cases = ({"horizon_weeks": True}, {"horizon_weeks": 2}, {}, None)
        for body in cases:
            with self.subTest(body=body):
                response = self.client.post(API + "/demand/predict", data=json.dumps(body),
                                            content_type="application/json")
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post(API + "/demand/predict", data="form").status_code, 415)
        for change in (
            {"days_to_expire": 0}, {"stock_quantity": True}, {"unit_price": float("nan")},
            {"inventory_turnover_rate": 100}, {"category": "Unsupported"}, {"unknown": 1},
        ):
            self.assertEqual(self.client.post(API + "/waste-simulation/predict", json={**SCENARIO, **change}).status_code, 400)
        self.assertEqual(self.client.get(API, headers={"Origin": "https://remote.example"}).status_code, 403)
        self.assertEqual(self.client.post(API + "/demand/predict", json={"horizon_weeks": 1},
                                         environ_overrides={"REMOTE_ADDR": "192.0.2.2"}).status_code, 403)

    def test_demand_training_rejects_partial_or_missing_weekly_history(self):
        with self.assertRaisesRegex(ValueError, "Expected CSV columns"):
            read_weekly_totals(ROOT / "data/fictional-weekly-sales.csv")
        with self.assertRaisesRegex(ValueError, "at least 26"):
            read_weekly_totals(self.weekly_file("short.csv", [20] * 12))
        with self.assertRaisesRegex(ValueError, "consecutive"):
            read_weekly_totals(self.weekly_file("gap.csv", [20] * 30, gap_at=15))

    def test_rolling_validation_ignores_test_tail_and_model_is_reproducible(self):
        values = [100 + index + 2 * (index % 4) for index in range(30)]
        path = self.weekly_file("weekly.csv", values)
        first = train_demand(path, "Test fixture")
        self.assertEqual(first, train_demand(path, "Test fixture"))
        changed = train_demand(self.weekly_file("changed.csv", values[:-6] + [700] * 6), "Test fixture")
        self.assertEqual(first["report"]["validation"], changed["report"]["validation"])
        self.assertEqual(first["report"]["selected"], changed["report"]["selected"])
        self.assertNotEqual(first["report"]["test"], changed["report"]["test"])
        split = first["report"]["split"]
        self.assertLess(split["training"]["through"], split["validation"]["from"])
        self.assertLess(split["validation"]["through"], split["test"]["from"])
        self.assertEqual(set(first["report"]["test"]), {"naive", "ridge"})
        write_json(self.demand_path, first)
        with self.assertRaises(FileExistsError):
            write_json(self.demand_path, first)
        self.assertTrue(self.client.get(API).json["demand"]["available"])
        forecast = self.client.post(API + "/demand/predict", json={"horizon_weeks": 1})
        self.assertEqual(forecast.status_code, 200, forecast.json)
        self.assertEqual(forecast.json["predicted_units"], predict_demand(first)["predicted_units"])
        self.assertEqual(forecast.json["source_kind"], "operator_verified_weekly_sales")
        expected = date.fromisoformat(first["history"][-1]["week_start"]) + timedelta(weeks=1)
        self.assertEqual(forecast.json["week_start"], expected.isoformat())

    def test_synthetic_predictor_runs_from_json_without_sklearn(self):
        # This one-split fixture exercises model loading and bounds independently of training dependencies.
        model = {
            "report": {"schema_version": 1, "task": "waste_simulation", "selected": "random_forest",
                       "source": {"kind": "synthetic"}},
            "categories": ["Dairy", "Fruit", "Grain", "Meat", "Vegetable"],
            "trees": [{"left": [1, -1, -1], "right": [2, -1, -1],
                       "feature": [0, -2, -2], "threshold": [150, -2, -2], "value": [0, 5, 30]}],
        }
        write_json(self.waste_path, model)
        status = self.client.get(API).json["waste"]
        self.assertFalse(status["available"])
        self.assertTrue(status["simulation_available"])
        for stock, predicted in ((160, 30), (120, 5)):
            result = self.client.post(API + "/waste-simulation/predict", json={**SCENARIO, "stock_quantity": stock})
            self.assertEqual(result.json["simulated_waste_units"], predicted)
            self.assertEqual(result.json["source_kind"], "synthetic")

    def test_corrupt_models_return_unavailable(self):
        self.waste_path.write_text('{"report":NaN}', encoding="utf-8")
        self.demand_path.write_text("invalid json", encoding="utf-8")
        self.assertFalse(self.client.get(API).json["demand"]["available"])
        self.assertEqual(self.client.post(API + "/waste-simulation/predict", json=SCENARIO).status_code, 503)

    @unittest.skipUnless(find_spec("sklearn"), "Install requirements-forecasting.txt to test training.")
    def test_synthetic_training_is_reproducible(self):
        frame, target, factor = synthetic_samples()
        again, repeated, repeated_factor = synthetic_samples()
        self.assertTrue(frame.equals(again))
        self.assertTrue(target.equals(repeated))
        self.assertEqual(factor, repeated_factor)
        first, second = train_waste_synthetic(), train_waste_synthetic()
        self.assertEqual(first, second)
        self.assertEqual(predict_waste(first, SCENARIO), predict_waste(second, SCENARIO))
        self.assertEqual(first["report"]["split"], {"train_samples": 800, "test_samples": 200})
        self.assertEqual(first["report"]["source"]["kind"], "synthetic")