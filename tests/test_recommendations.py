import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from flask import Flask

from auth_fixture import authenticated_client

from dashboard_ai_api import register_ai
from inventory_api import register_inventory
from inventory_service import archive_lot, create_ingredient, create_lot
from recommendations_api import recommendations_api
from runtime_config import ROOT


API = "/api/recommendations"
ORIGIN = "http://127.0.0.1:5173"


class ServiceDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 24)


class RecommendationsTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / "instance" / f"test-recommendations-{uuid4().hex}"
        root.mkdir(parents=True)
        self.addCleanup(root.rmdir)
        database_path = root / "stock.sqlite3"
        self.addCleanup(database_path.unlink, missing_ok=True)
        self.app = Flask(__name__, instance_path=str(root))
        self.app.config.update(TESTING=True, INVENTORY_DB_PATH=database_path, INVENTORY_CORS_ORIGINS=ORIGIN)
        register_inventory(self.app)
        register_ai(self.app)
        self.app.register_blueprint(recommendations_api)
        self.db = self.app.extensions["inventory_database"]
        self.addCleanup(self.db.engine.dispose)
        self.client = authenticated_client(self, self.app)
        self.enterContext(patch("recommendations_api.date", ServiceDate))
        self.enterContext(patch("dashboard_ai_api.date", ServiceDate))

    def seed(self):
        self.db.initialize()
        with self.db.session(write=True) as session:
            def ingredient(name, minimum=4):
                return create_ingredient(session, {
                    "name": name, "category": "Produce", "unit": "kg", "minimum_stock": Decimal(minimum),
                })

            def lot(item, qty, expiry, price=None, source=None, source_expiry=None):
                return create_lot(session, item, {
                    "quantity": Decimal(qty), "expiry_date": date.fromisoformat(expiry) if expiry else None,
                    "cost_per_unit": Decimal(price) if price is not None else None,
                    "source_expiry_text": source_expiry,
                }, import_key=source)

            rice = ingredient("Rice", 9)
            late = lot(rice, "3", "2026-12-02", "2.250")
            early = lot(rice, "1", "2026-11-24", "2.250")
            same_day = lot(rice, "1.500", "2026-11-24", "2.250")
            demo = lot(rice, "1", "2026-12-01", "99.000", "rice-demo")
            unknown = lot(rice, "20", None, "9")
            invalid = lot(rice, "8", None, "4", "bad-date", "32/01/24")
            expired = lot(rice, "30", "2026-11-23", "25")
            zero = lot(rice, "0", "2026-11-24", "123")
            archived = lot(rice, "40", "2026-11-24", "456")
            archive_lot(session, archived)
            beans = ingredient("Beans")
            lot(beans, "1", "2026-11-29", "99", "beans-demo")
            carrots = ingredient("Carrots")
            lot(carrots, "1", "2026-12-04", "1")
            lot(carrots, "1", "2026-12-04", "2")
            onions = ingredient("Onions")
            lot(onions, "1", "2026-12-04", "4")
            lot(onions, "1", "2026-12-04", "0")
            unknown_only = ingredient("Unknown-only stock")
            lot(unknown_only, "9", None)
            self.ids = {name: item.id for name, item in locals().items() if hasattr(item, "id")}

    def simulate(self, **values):
        payload = {"ingredient_id": self.ids["rice"], "consume_quantity": "0", **values}
        return self.client.post(API + "/simulate", json=payload)

    def test_overview_prioritizes_fefo_and_explains_eligibility_and_cost(self):
        self.seed()
        ids = self.ids
        response = self.client.get(API, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], ORIGIN)
        data = response.json
        self.assertEqual(data["as_of"], "2026-11-24")
        self.assertEqual(data["near_expiry_days"], 7)
        self.assertEqual([lot["id"] for lot in data["near_expiry"][:2]], [ids["early"], ids["same_day"]])
        self.assertEqual(data["near_expiry"][-1]["id"], ids["demo"])
        self.assertEqual(data["near_expiry"][-1]["source"], "csv_import")
        self.assertEqual(data["recipe_ingredient_ids"], [ids["rice"], ids["beans"]])
        review = {lot["id"]: lot for lot in data["expiry_review"]}
        self.assertIn(ids["unknown"], review)
        self.assertEqual(review[ids["invalid"]]["source_expiry_text"], "32/01/24")
        self.assertEqual([lot["id"] for lot in data["expired"]], [ids["expired"]])

        gaps = {item["id"]: item for item in data["low_stock"]}
        rice = gaps[ids["rice"]]
        self.assertEqual((rice["eligible_quantity"], rice["minimum_stock"], rice["replenishment_quantity"]),
                         ("6.500", "9.000", "2.500"))
        self.assertEqual([lot["id"] for lot in rice["eligible_lots"]],
                         [ids["early"], ids["same_day"], ids["demo"], ids["late"]])
        self.assertEqual(rice["cost"]["estimated_cost"], "5.625000")
        self.assertEqual([row["lot_id"] for row in rice["cost"]["evidence"]],
                         [ids["early"], ids["same_day"], ids["late"]])
        for ingredient, reason in (("beans", "no_eligible_manual_lots"), ("carrots", "conflicting_unit_costs"),
                                   ("onions", "missing_unit_costs"), ("unknown_only", "no_eligible_manual_lots")):
            self.assertIsNone(gaps[ids[ingredient]]["cost"]["estimated_cost"])
            self.assertEqual(gaps[ids[ingredient]]["cost"]["basis"], reason)
        self.assertEqual(gaps[ids["unknown_only"]]["eligible_quantity"], "0.000")

    def test_scenario_is_read_only_reports_unfilled_and_accepts_assumed_cost(self):
        self.seed()
        before = self.db.path.read_bytes()
        response = self.simulate(consume_quantity="3.125", assumed_unit_cost="3.125")
        self.assertEqual(response.status_code, 200)
        result = response.json
        self.assertTrue(result["simulation"])
        self.assertEqual([(lot["id"], lot["allocated_quantity"]) for lot in result["allocations"]],
                         [(self.ids["early"], "1.000"), (self.ids["same_day"], "1.500"), (self.ids["demo"], "0.625")])
        self.assertEqual((result["projected_quantity"], result["replenishment_quantity"], result["unfulfilled_quantity"]),
                         ("3.375", "5.625", "0.000"))
        self.assertEqual(result["cost"], {
            "basis": "simulation_assumption", "unit_cost": "3.125", "estimated_cost": "17.578125", "evidence": [],
        })
        excess = self.simulate(consume_quantity="8").json
        self.assertEqual((excess["projected_quantity"], excess["unfulfilled_quantity"]), ("0.000", "1.500"))
        self.assertEqual(excess["cost"]["estimated_cost"], "20.250000")
        self.assertEqual(self.simulate(assumed_unit_cost=0).json["cost"]["estimated_cost"], "0.000000")
        self.assertEqual(self.db.path.read_bytes(), before)

    def test_validates_requests_and_refuses_remote_access(self):
        self.assertEqual(self.client.get(API).status_code, 503)
        self.assertIn("init-inventory-db", self.client.get(API).json["error"])
        self.seed()
        for values in ({"ingredient_id": True}, {"ingredient_id": 0}, {"ingredient_id": "1"},
                       {"consume_quantity": "0.0001"}, {"consume_quantity": "NaN"}, {"consume_quantity": -1},
                       {"assumed_unit_cost": None}, {"assumed_unit_cost": "1000000000"}, {"commit": True}):
            with self.subTest(values=values):
                self.assertEqual(self.simulate(**values).status_code, 400)
        self.assertEqual(self.simulate(ingredient_id=999).status_code, 404)
        self.assertEqual(self.client.post(API + "/simulate", data="not json").status_code, 415)
        self.assertEqual(self.client.post(API + "/simulate", json={"unused": "x" * 4096}).status_code, 413)
        self.assertEqual(self.client.get(API, headers={"Origin": "https://remote.example"}).status_code, 403)
        self.assertEqual(self.client.post(API + "/simulate", environ_overrides={"REMOTE_ADDR": "192.0.2.1"}).status_code, 403)
        options = self.client.options(API + "/simulate", headers={"Origin": ORIGIN})
        self.assertIn("POST", options.headers["Access-Control-Allow-Methods"])

    def test_recipe_path_rechecks_eligibility_before_calling_gemini(self):
        self.seed()
        recipe = {"name": "Rice recipe", "ingredients": ["Rice"], "steps": ["Cook."]}
        with patch("dashboard_ai_api.generate_recipe", return_value=recipe) as generate:
            for ingredient, status in (("unknown_only", 409), ("carrots", 409)):
                response = self.client.post("/api/ai/recipes", json={
                    "ingredient_ids": [self.ids[ingredient]], "near_expiry_only": True,
                })
                self.assertEqual(response.status_code, status)
            self.assertEqual(self.client.post("/api/ai/recipes", json={
                "ingredient_ids": [self.ids["rice"]], "near_expiry_only": "yes",
            }).status_code, 400)
            generate.assert_not_called()
            selected = self.client.get(API).json["recipe_ingredient_ids"]
            response = self.client.post("/api/ai/recipes", json={
                "ingredient_ids": selected, "near_expiry_only": True,
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["recipe"], recipe)
            self.assertEqual([item["id"] for item in response.json["inventory_inputs"]], selected)
            self.assertEqual([item["available_quantity"] for item in response.json["inventory_inputs"]], ["6.500", "1.000"])
            self.assertIn("prioritize near-expiry stock", generate.call_args.args[0][0])
