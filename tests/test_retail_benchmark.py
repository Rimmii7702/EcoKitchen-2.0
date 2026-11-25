import json
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

from app import app
from runtime_config import ROOT


API = "/api/forecasting/retail-benchmark"
NAME = "freshretailnet-holdout-v1.json"
ARTIFACT = ROOT / "forecasting" / "results" / NAME


class RetailBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.directory = ROOT / "instance" / ("test-retail-benchmark-" + uuid4().hex)
        self.directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.directory)

    def test_verified_metrics_daily_totals_and_comparison(self):
        response = self.client.get(API)
        self.assertEqual(response.status_code, 200)
        result = response.json
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["dataset"]["publisher"], "Dingdong-Inc")
        self.assertEqual(result["dataset"]["license"], "CC BY 4.0")
        self.assertEqual(result["evaluation"], {
            "forecast_origin": "2024-06-25", "start": "2024-06-26", "end": "2024-07-02",
            "kind": "official_held_out_evaluation", "target": "Observed retail sales",
            "units": "normalized retail sales units", "rows": 350000, "pairs": 50000, "horizon_days": 7,
        })
        self.assertEqual(result["models"]["lightgbm"]["trees"], 96)
        self.assertEqual(result["models"]["lightgbm"]["metrics"], {
            "mae": 0.4014028435, "rmse": 0.7098477855, "wape_pct": 33.6450157277, "signed_bias_pct": -3.1445163587,
        })
        naive = result["models"]["seasonal_naive"]["metrics"]
        self.assertEqual(naive["wape_pct"], 41.8314054769)
        self.assertEqual(result["wape_improvement_pp"], 8.1863897492)
        self.assertAlmostEqual(result["wape_improvement_pp"], naive["wape_pct"] - 33.6450157277, places=10)
        raw = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        for index, (daily, horizon) in enumerate(zip(result["daily_sales"], result["horizons"], strict=True), 1):
            source = [row for row in raw["daily_totals_by_24h_stockout"] if row["target_date"] == daily["date"]]
            self.assertEqual(len(source), 2)
            self.assertEqual(horizon["day"], index)
            self.assertEqual(horizon["date"], daily["date"])
            self.assertEqual(horizon["rows"], 50000)
            for key in ("actual", "seasonal_naive", "lightgbm"):
                self.assertAlmostEqual(daily[key], sum(row[key] for row in source), places=7)
            self.assertEqual(horizon["lightgbm"]["wape_pct"], raw["lightgbm"]["by_horizon"][str(index)]["all"]["wape_pct"])
        self.assertEqual(len(result["daily_sales"]), 7)
        groups = result["stockout_groups"]
        self.assertEqual([group["rows"] for group in groups], [151944, 198056])
        self.assertEqual([group["lightgbm"]["wape_pct"] for group in groups], [29.9575084864, 36.9191019042])
        self.assertNotIn(b"forecasts.parquet", response.data)
        self.assertNotIn(b"sha256", response.data)

    def test_missing_or_unreadable_scorecard_returns_503(self):
        with patch("forecasting_api.RESULTS", self.directory):
            missing = self.client.get(API)
            self.assertEqual(missing.status_code, 503)
            self.assertNotIn(str(self.directory), missing.json["error"])
            (self.directory / NAME).write_text("{bad", encoding="utf-8")
            self.assertEqual(self.client.get(API).status_code, 503)

    def test_malformed_values_and_inconsistent_evaluation_return_503(self):
        original = ARTIFACT.read_text(encoding="utf-8")
        cases = (
            lambda data: data["lightgbm"]["overall"]["all"].update(wape_pct=True),
            lambda data: data["lightgbm"]["overall"]["all"].update(mae=float("nan")),
            lambda data: data["daily_totals_by_24h_stockout"].pop(),
            lambda data: data["seasonal_naive"]["by_horizon"]["2"]["all"].update(scored=17),
            lambda data: data.update(wape_improvement_percentage_points=0),
            lambda data: data.update(target="uncensored_demand"),
        )
        with patch("forecasting_api.RESULTS", self.directory):
            for change in cases:
                with self.subTest(change=change):
                    fixture = json.loads(original)
                    change(fixture)
                    (self.directory / NAME).write_text(json.dumps(fixture), encoding="utf-8")
                    self.assertEqual(self.client.get(API).status_code, 503)

    def test_local_get_with_static_server_origin_and_no_writes(self):
        origin = "http://127.0.0.1:4173"
        response = self.client.get(API + "?path=private-model.txt", headers={"Origin": origin})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
        self.assertNotIn(b"private-model.txt", response.data)
        self.assertEqual(self.client.get(API, headers={"Origin": "https://external.example"}).status_code, 403)
        self.assertEqual(self.client.post(API).status_code, 405)
