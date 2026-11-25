import csv
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from flask import Flask

from app import app as eco_app
from auth_fixture import authenticated_client, sign_in
from inventory_api import register_inventory
from runtime_config import DATA_DIR, ROOT


API = "/api/inventory"
CSV_HEADER = "Name,Catagory,Quantity,Unit,Cost per Unit,Expiry date,Minimum stock level,No of days after restock.\n"


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "instance" / f"test-inventory-{uuid4().hex}"
        self.directory.mkdir(parents=True)
        self.addCleanup(self.directory.rmdir)
        self.path = self.directory / "stock.sqlite3"
        self.addCleanup(self.path.unlink, missing_ok=True)
        self.app = self.make_app(self.path)
        self.client = authenticated_client(self, self.app)
        self.runner = self.app.test_cli_runner()

    def make_app(self, path):
        app = Flask(__name__)
        app.config.update(TESTING=True, INVENTORY_DB_PATH=path)
        register_inventory(app)
        self.addCleanup(app.extensions["inventory_database"].engine.dispose)
        return app

    def initialize(self):
        result = self.runner.invoke(args=["init-inventory-db"])
        self.assertEqual(result.exit_code, 0, (result.output, result.exception))

    def ingredient(self, name="Rice", minimum_stock="1.000"):
        response = self.client.post(f"{API}/ingredients", json={
            "name": name, "category": "Pantry", "unit": "kg", "minimum_stock": minimum_stock,
        })
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.headers["Location"], f"{API}/ingredients/{response.json['id']}")
        return response.json

    def lot(self, ingredient_id, quantity="2.500", expiry_date=None):
        response = self.client.post(API, json={
            "ingredient_id": ingredient_id, "quantity": quantity, "expiry_date": expiry_date,
        })
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.headers["Location"], f"{API}/{response.json['id']}")
        return response.json

    def fixture_csv(self, rows):
        path = self.directory / f"source-{uuid4().hex}.csv"
        self.addCleanup(path.unlink, missing_ok=True)
        path.write_text(CSV_HEADER + rows, encoding="utf-8")
        return path

    def test_missing_schema_and_legacy_inventory_view(self):
        self.assertEqual(self.client.get(API).status_code, 503)
        self.assertFalse(self.path.exists())
        with patch.dict(eco_app.extensions, self.app.extensions), sign_in(eco_app) as client:
            self.assertIn(b"Demo lentils", client.get("/inventory-page").data)
            self.initialize()
            self.assertNotIn(b"Demo lentils", client.get("/inventory-page").data)
            ingredient = self.ingredient("Database-only ingredient")
            self.lot(ingredient["id"])
            response = client.get("/inventory-page")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Database-only ingredient", response.data)
            self.assertIn(b"Expiry unknown", response.data)

    def test_crud_and_history_survive_archiving(self):
        self.initialize()
        item = self.ingredient()
        item_url = f"{API}/ingredients/{item['id']}"
        lot = self.lot(item["id"], expiry_date="2025-02-08")
        url = f"{API}/{lot['id']}"
        self.assertEqual(self.client.get(item_url).json, item)
        self.assertEqual(self.client.get(url).json, lot)
        self.assertEqual(self.client.get(f"{API}?ingredient_id={item['id']}").json["items"], [lot])
        self.assertEqual(self.client.patch(item_url, json={"name": "Jasmine rice"}).json["name"], "Jasmine rice")
        self.assertEqual(self.client.patch(item_url, json={"unit": "g"}).status_code, 409)
        self.assertEqual(self.client.delete(item_url).status_code, 409)
        response = self.client.patch(url, json={"quantity": "1.25", "expiry_date": "2025-03-01"})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["quantity"], "1.250")
        self.assertEqual(self.client.patch(url, json={"quantity": "1.250"}).status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.delete(item_url).status_code, 204)
        self.assertEqual(self.client.get(item_url).status_code, 404)
        self.assertEqual(self.client.get(API).json["items"], [])
        self.assertEqual(self.client.get(f"{API}/ingredients").json["items"], [])
        history = self.client.get(f"{API}/history?lot_id={lot['id']}").json["items"]
        self.assertEqual([event["kind"] for event in history], ["archived", "updated", "created"])
        self.assertEqual([event["quantity_change"] for event in history], ["-1.250", "-1.250", "2.500"])
        self.assertEqual(history[1]["expiry_before"], "2025-02-08")
        self.assertEqual(history[1]["expiry_after"], "2025-03-01")
        self.assertTrue(history[0]["occurred_at"].endswith("+00:00"))
        self.assertNotEqual(self.ingredient("Jasmine rice")["id"], item["id"])

    def test_expiry_and_low_stock_alerts(self):
        self.initialize()
        today = date.today()
        ingredient_id = self.ingredient(minimum_stock="0.800")["id"]
        expired = self.lot(ingredient_id, "9", (today - timedelta(days=1)).isoformat())
        due_today = self.lot(ingredient_id, "0.100", today.isoformat())
        due_in_week = self.lot(ingredient_id, "0.700", (today + timedelta(days=7)).isoformat())
        self.lot(ingredient_id, "0", (today + timedelta(days=2)).isoformat())
        missing_stock = self.ingredient("Salt")["id"]
        unknown_expiry = self.lot(missing_stock, "0.200")
        self.assertIsNone(unknown_expiry["days_until_expiry"])
        default = self.client.get(f"{API}/alerts").json
        self.assertEqual(default["as_of"], today.isoformat())
        self.assertEqual([lot["id"] for lot in default["near_expiry"]], [due_today["id"], due_in_week["id"]])
        self.assertEqual([lot["id"] for lot in default["expired"]], [expired["id"]])
        self.assertEqual([lot["id"] for lot in default["unknown_expiry"]], [unknown_expiry["id"]])
        self.assertEqual([(row["id"], row["available_quantity"]) for row in default["low_stock"]],
                         [(missing_stock, "0.200")])
        same_day = self.client.get(f"{API}/alerts?days=0").json
        self.assertEqual([lot["id"] for lot in same_day["near_expiry"]], [due_today["id"]])
        self.client.patch(f"{API}/{due_in_week['id']}", json={"quantity": "0.100"})
        self.assertEqual(self.client.get(f"{API}/alerts").json["low_stock"][0]["available_quantity"], "0.200")

    def test_commits_persist_across_app_instances(self):
        self.initialize()
        item = self.ingredient()
        lot = self.lot(item["id"], "1.125", "2027-02-01")
        self.app.extensions["inventory_database"].engine.dispose()
        self.assertTrue(self.path.is_file())
        with authenticated_client(self, self.make_app(self.path)) as client:
            self.assertEqual(client.get(f"{API}/{lot['id']}").json, lot)
            self.assertEqual(client.get(f"{API}/history").json["items"][0]["kind"], "created")

    def test_overlapping_updates_preserve_both_fields_and_history(self):
        self.initialize()
        item = self.ingredient()
        lot = self.lot(item["id"])
        url = f"{API}/{lot['id']}"
        start = Barrier(2)

        def update(payload):
            with sign_in(self.app) as client:
                start.wait(timeout=10)
                return client.patch(url, json=payload).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            updates = [pool.submit(update, patch_data) for patch_data in (
                {"quantity": "5.500"}, {"expiry_date": "2027-02-01"},
            )]
            self.assertEqual([task.result(timeout=15) for task in updates], [200, 200])
        updated = self.client.get(url).json
        self.assertEqual((updated["quantity"], updated["expiry_date"]), ("5.500", "2027-02-01"))
        events = self.client.get(f"{API}/history?lot_id={lot['id']}").json["items"]
        self.assertEqual(events[0]["quantity_before"], events[1]["quantity_after"])
        self.assertEqual(events[0]["expiry_before"], events[1]["expiry_after"])

    def test_import_keeps_fictional_rows_dates_and_csv_bytes(self):
        path = DATA_DIR / "fictional-ingredients.csv"
        original = path.read_bytes()
        with path.open(encoding="utf-8-sig", newline="") as csv_file:
            expected = Counter(
                (row["Name"].strip(), row["Catagory"].strip(), row["Unit"].strip(),
                 row["Expiry date"].strip(), Decimal(row["Quantity"]))
                for row in csv.DictReader(csv_file)
            )
        first = self.runner.invoke(args=["import-inventory"])
        self.assertEqual(first.exit_code, 0, (first.output, first.exception))
        self.assertIn(f"Imported {sum(expected.values())} lot(s)", first.output)
        self.assertIn("2028-02-30", first.output)
        second = self.runner.invoke(args=["import-inventory"])
        self.assertEqual(second.exit_code, 0, (second.output, second.exception))
        self.assertIn("Imported 0 lot(s)", second.output)
        lots = self.client.get(f"{API}?limit=500").json["items"]
        self.assertEqual({lot["source"] for lot in lots}, {"csv_import"})
        actual = Counter(
            (lot["ingredient"]["name"], lot["ingredient"]["category"], lot["ingredient"]["unit"],
             lot["expiry_date"] or lot["source_expiry_text"], Decimal(lot["quantity"]))
            for lot in lots
        )
        self.assertEqual(actual, expected)
        whipped = [lot for lot in lots if lot["ingredient"]["name"] == "Demo lentils"]
        self.assertEqual(len({lot["ingredient_id"] for lot in whipped}), 1)
        self.assertEqual({lot["ingredient"]["minimum_stock"] for lot in whipped}, {"7.000"})
        self.assertEqual(path.read_bytes(), original)
        tea = next(lot for lot in lots if lot["ingredient"]["name"] == "Demo mint")
        self.assertIsNone(tea["expiry_date"])
        self.assertEqual(tea["source_expiry_text"], "2028-02-30")
        alerts = self.client.get(f"{API}/alerts").json
        self.assertIn(tea["id"], [lot["id"] for lot in alerts["unknown_expiry"]])
        self.assertEqual(next(row["available_quantity"] for row in alerts["low_stock"]
                              if row["id"] == tea["ingredient_id"]), "0.000")

    def test_import_skips_existing_rows_even_after_edits_and_archival(self):
        source = self.fixture_csv(
            "Rice,Pantry,2.500,kg,4,2025-01-03,1,7\n"
            "Rice,Pantry,2.500,kg,4.000,2025-01-03,1,7\n"
        )
        command = ["import-inventory", "--csv", str(source)]
        self.assertIn("Imported 1 lot(s). Skipped 1", self.runner.invoke(args=command).output)
        lot = self.client.get(API).json["items"][0]
        url = f"{API}/{lot['id']}"
        self.assertEqual(self.client.patch(url, json={"quantity": "4", "expiry_date": "2025-03-05"}).status_code, 200)
        self.assertIn("Imported 0 lot(s). Skipped 2", self.runner.invoke(args=command).output)
        self.assertEqual(self.client.get(url).json["expiry_date"], "2025-03-05")
        self.assertEqual(self.client.get(url).json["quantity"], "4.000")
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertIn("Imported 0 lot(s). Skipped 2", self.runner.invoke(args=command).output)
        self.assertEqual(self.client.get(API).json["items"], [])
        self.assertEqual(len(self.client.get(f"{API}/history").json["items"]), 3)

    def test_bad_csv_does_not_partially_load_or_change_stock(self):
        bad_stock = self.fixture_csv(
            "Flour,Pantry,1,kg,2,2025-01-03,0,1\n"
            "Rice,Pantry,-2,kg,2,2025-03-01,0,1\n"
        )
        failed = self.runner.invoke(args=["import-inventory", "--csv", str(bad_stock)])
        self.assertEqual(failed.exit_code, 1)
        self.assertIn("CSV line 3", failed.output)
        self.assertFalse(self.path.exists())

        self.initialize()
        rice = self.ingredient()
        self.lot(rice["id"], "1", "2025-02-26")
        conflict = self.fixture_csv(
            "Flour,Pantry,1,kg,2,2025-01-03,0,1\n"
            "Rice,Pantry,2,kg,2,2025-02-26,0,1\n"
        )
        failed = self.runner.invoke(args=["import-inventory", "--csv", str(conflict)])
        self.assertEqual(failed.exit_code, 1)
        self.assertIn("conflict", failed.output)
        self.assertEqual([lot["ingredient_id"] for lot in self.client.get(API).json["items"]], [rice["id"]])
        self.assertEqual(len(self.client.get(f"{API}/history").json["items"]), 1)

    def test_invalid_input_is_rejected_without_mutation(self):
        self.initialize()
        item = self.ingredient()
        duplicate = self.client.post(f"{API}/ingredients", json={
            "name": " rice ", "category": " PANTRY ", "unit": "KG",
        })
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(self.client.post(API, data="text").status_code, 415)
        self.assertEqual(self.client.post(API, data="{bad", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post(API, json=[]).status_code, 400)
        self.assertEqual(self.client.get(f"{API}/alerts?days=-1").status_code, 400)
        self.assertEqual(self.client.get(f"{API}?limit=0").status_code, 400)
        self.assertEqual(self.client.get(f"{API}/9223372036854775808").status_code, 400)
        for fields in (
            {"quantity": "-1"}, {"quantity": "0.0004"}, {"quantity": "NaN"}, {"quantity": True},
            {"quantity": "1e1000"}, {"expiry_date": "2026-02-30"}, {"expiry_date": "20261124"},
            {"ingredient_id": True}, {"days_after_restock": 0.5}, {"cost_per_unit": "-2"}, {"fake": 1},
        ):
            with self.subTest(fields=fields):
                response = self.client.post(API, json={"ingredient_id": item["id"], "quantity": "1", **fields})
                self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.client.post(API, json={"ingredient_id": 999, "quantity": 1}).status_code, 404)
        self.assertEqual(self.client.get(API).json["items"], [])
        self.assertEqual(self.client.get(f"{API}/history").json["items"], [])

    def test_dashboard_search_totals_and_provenance(self):
        self.initialize()
        first = self.ingredient("Rice 100%")
        second = self.ingredient("Brown rice")
        self.lot(first["id"])
        self.lot(second["id"])

        page = self.client.get(f"{API}?q=RICE&limit=1&offset=1").json
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["items"][0]["ingredient"]["name"], second["name"])
        literal = self.client.get(f"{API}/ingredients?q=%25").json
        self.assertEqual(literal, {"items": [first], "total": 1})
        self.assertEqual(self.client.get(f"{API}?source=csv_import").json["total"], 0)
        self.assertEqual(self.client.get(f"{API}?source=manual").json["items"][0]["source"], "manual")
        self.assertEqual(self.client.get(f"{API}?source=unknown").status_code, 400)
        history = self.client.get(f"{API}/history?limit=1").json
        self.assertEqual(history["total"], 2)
        self.assertEqual(history["items"][0]["ingredient_name"], second["name"])

    def test_dashboard_cors_and_local_write_guard(self):
        self.initialize()
        local = "http://127.0.0.1:5173"
        evil = "https://remote.example"
        self.app.config["INVENTORY_CORS_ORIGINS"] = f"{local},{evil}"
        body = {"name": "Rice", "category": "Pantry", "unit": "kg"}
        endpoint = f"{API}/ingredients"

        preflight = self.client.options(endpoint, headers={
            "Origin": local, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(preflight.headers["Access-Control-Allow-Origin"], local)
        self.assertIn("PATCH", preflight.headers["Access-Control-Allow-Methods"])
        self.assertEqual(preflight.headers["Access-Control-Allow-Credentials"], "true")

        for options in (
            {"headers": {"Origin": evil}},
            {"headers": {"Origin": "null"}},
            {"headers": {"Origin": "http://localhost:5174"}},
            {"base_url": "http://public.example"},
            {"environ_overrides": {"REMOTE_ADDR": "192.0.2.1"}, "headers": {"Origin": local}},
        ):
            with self.subTest(options=options):
                blocked = self.client.post(endpoint, json=body, **options)
                self.assertEqual(blocked.status_code, 403)
        self.assertEqual(self.client.get(endpoint).json["total"], 0)
        self.assertNotIn("Access-Control-Allow-Origin", self.client.get(endpoint, headers={"Origin": evil}).headers)
        created = self.client.post(endpoint, json=body, headers={"Origin": local})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.headers["Access-Control-Allow-Origin"], local)
        same_origin = self.client.patch(created.headers["Location"], json={"name": "White rice"},
                                        headers={"Origin": "http://localhost"})
        self.assertEqual(same_origin.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", self.client.get("/inventory-page", headers={"Origin": local}).headers)
