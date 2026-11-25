import io
import json
import shutil
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import numpy as np
import pandas as pd

from auth_fixture import authenticated_client

from app import app
from main_application import RecipeRecommendationSystem
from recipe_generator import RecipeRecommender
from inventory_db import InventoryDatabase
from inventory_service import create_ingredient, create_lot
from runtime_config import ROOT


class FixtureDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 24)


JPEG = b"\xff\xd8\xff\xe0photo-fixture"
ORIGIN = "http://127.0.0.1:5173"


class LegacyMenuInputTests(unittest.TestCase):
    def test_expired_and_undated_produce_are_excluded_from_legacy_menu_inputs(self):
        inventory = pd.DataFrame([
            {"Name": name, "Catagory": "Fruits & Vegetables",
             "No of days after restock.": 9, "Days until expiry": days}
            for name, days in (("Old produce", -2), ("Unverified produce", None), ("Verified produce", 1))
        ])
        model = Mock()
        model.generate_content.return_value.text = json.dumps({
            "dish": {"name": "Produce dish", "description": "Fixture", "ingredients": ["Verified produce"]},
        })
        with patch("recipe_generator.recipe_model", return_value=model):
            system = RecipeRecommendationSystem()
            system.load_data(inventory)
            result = system.get_recommendations(recommender=RecipeRecommender())
        self.assertEqual([row["Name"] for row in result["expiring_ingredients"]], ["Verified produce"])
        prompt = model.generate_content.call_args_list[0].args[0]
        self.assertIn("Verified produce", prompt)
        self.assertNotIn("Old produce", prompt)
        self.assertNotIn("Unverified produce", prompt)


class DashboardAITests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "instance" / ("test-dashboard-ai-" + uuid4().hex)
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.db = InventoryDatabase(self.directory / "test.sqlite3")
        self.addCleanup(self.db.engine.dispose)
        self.db.initialize()
        self.saved = self.directory / "saved.json"
        self.saved_bytes = json.dumps({
            "date": "2025-06-20",
            "todays_special": {"dish": {"name": "Saved special", "ingredients": ["Lentils"], "recipe": "Simmer."}},
        }).encode("utf-8")
        self.saved.write_bytes(self.saved_bytes)
        for fixture in (
            patch.dict(app.extensions, {"inventory_database": self.db}),
            patch.dict(app.config, {
                "AI_MENU_DIR": self.directory / "menus",
                "VISION_MEDIA_DIR": self.directory / "media",
                "INVENTORY_CORS_ORIGINS": ORIGIN,
            }),
            patch.dict("os.environ", {"RECOMMENDATIONS_FILE": str(self.saved), "GEMINI_API_KEY": ""}),
            patch("dashboard_ai_api.date", FixtureDate),
        ):
            fixture.start()
            self.addCleanup(fixture.stop)

        with self.db.session(write=True) as session:
            rice = create_ingredient(session, {"name": "Rice", "category": "Pantry", "unit": "kg"})
            create_lot(session, rice, {"quantity": Decimal("2.000"), "expiry_date": date(2026, 11, 26)}, import_key="fixture")
            create_lot(session, rice, {"quantity": Decimal("1.250"), "expiry_date": None})
            create_lot(session, rice, {"quantity": Decimal("5.000"), "expiry_date": date(2026, 11, 1)})
            spoiled = create_ingredient(session, {"name": "Expired stock", "category": "Pantry", "unit": "kg"})
            create_lot(session, spoiled, {"quantity": Decimal("9.000"), "expiry_date": date(2025, 1, 1)})
            self.rice_id, self.expired_id = rice.id, spoiled.id
        self.client = authenticated_client(self, app)

    def upload(self, endpoint):
        return self.client.post(f"/api/ai/{endpoint}", data={"image": (io.BytesIO(JPEG), "food.jpg")})

    def test_recipe_inputs_and_structured_generation(self):
        model = Mock()
        model.generate_content.return_value = SimpleNamespace(text=json.dumps({
            "name": "Lemon rice", "ingredients": ["200 g rice"], "steps": ["Cook the rice."],
            "waste_reduction_tips": ["Use the older rice lot first."],
        }))
        with patch("own_recipe_generation.recipe_model", return_value=model) as factory:
            available = self.client.get("/api/ai/recipe-ingredients", headers={"Origin": ORIGIN})
            self.assertEqual(available.status_code, 200)
            self.assertEqual(available.headers["Access-Control-Allow-Origin"], ORIGIN)
            factory.assert_not_called()
            only = available.json["items"][0]
            self.assertEqual(len(available.json["items"]), 1)
            self.assertEqual(only["available_quantity"], "2.000")
            self.assertEqual(only["earliest_expiry"], "2026-11-26")
            self.assertTrue(only["near_expiry"])
            self.assertEqual(only["sources"], ["csv_import"])
            for ids in ([self.expired_id], [self.rice_id, self.rice_id], [True], []):
                rejected = self.client.post("/api/ai/recipes", json={"ingredient_ids": ids})
                self.assertIn(rejected.status_code, (400, 409))
            factory.assert_not_called()
            result = self.client.post("/api/ai/recipes", json={"ingredient_ids": [self.rice_id]})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["recipe"]["steps"], ["Cook the rice."])
        self.assertEqual(result.json["inventory_inputs"], [only])
        self.assertIn("2.000 kg available; earliest expiry 2026-11-26; prioritize near-expiry stock",
                      model.generate_content.call_args.args[0])
        self.assertNotIn("Expired stock", model.generate_content.call_args.args[0])

    def test_invalid_recipe_response_returns_502(self):
        model = Mock()
        model.generate_content.return_value.text = '{"name":"Stew","ingredients":[],"steps":["Cook."]}'
        with patch("own_recipe_generation.recipe_model", return_value=model):
            response = self.client.post("/api/ai/recipes", json={"ingredient_ids": [self.rice_id]})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(set(response.json), {"error"})

    def test_menu_reads_snapshots_without_generation_and_archives_new_versions(self):
        model = Mock()
        menu = {"dish": {"name": "AI rice menu", "ingredients": ["Rice"], "description": "Rice dish."}}
        model.generate_content.return_value.text = json.dumps(menu)
        with patch("recipe_generator.recipe_model", return_value=model) as factory:
            first = self.client.get("/api/ai/menu")
            self.assertEqual(first.json["saved_snapshot"]["date"], "2025-06-20")
            self.assertIsNone(first.json["latest_snapshot"])
            factory.assert_not_called()
            post = self.client.post("/api/ai/menu")
            self.assertEqual(post.status_code, 200, post.json)
            self.assertEqual(post.json["latest_snapshot"]["date"], "2026-11-24")
            self.assertEqual(model.generate_content.call_count, 2)
            for call in model.generate_content.call_args_list:
                self.assertIn("Rice (2.000 kg available", call.args[0])
                self.assertNotIn("Expired stock", call.args[0])
            model.generate_content.side_effect = [
                SimpleNamespace(text=json.dumps(menu)), SimpleNamespace(text="unusable response"),
            ]
            self.assertEqual(self.client.post("/api/ai/menu").status_code, 502)
            self.assertEqual(self.client.get("/api/ai/menu").json["latest_snapshot"], post.json["latest_snapshot"])
            model.generate_content.side_effect = None
            second = self.client.post("/api/ai/menu")
            self.assertEqual(second.status_code, 200)
            stored = self.client.get("/api/ai/menu").json
            self.assertEqual(stored["latest_snapshot"], second.json["latest_snapshot"])
            self.assertEqual(stored["previous_snapshots"], [post.json["latest_snapshot"]])
        self.assertEqual(self.saved.read_bytes(), self.saved_bytes)
        self.assertEqual(len(list((self.directory / "menus").glob("*.json"))), 2)

    def test_credentials_and_loopback_guards(self):
        payload = {"ingredient_ids": [self.rice_id]}
        for endpoint in ("/api/ai/menu", "/api/ai/recipes"):
            response = self.client.post(endpoint, json=payload)
            self.assertEqual(response.status_code, 503)
            self.assertIn("GEMINI_API_KEY", response.json["error"])
        response = self.upload("heatmap")
        self.assertEqual(response.status_code, 503)
        self.assertIn("GEMINI_API_KEY", response.json["error"])
        self.assertEqual(self.upload("waste").status_code, 503)
        self.assertIn("unavailable in this edition", self.upload("waste").json["error"])
        self.assertEqual(self.client.get("/api/ai/menu", headers={"Origin": "https://remote.example"}).status_code, 403)
        for endpoint in ("/api/ai/menu", "/generate-recipe", "/waste_analysis", "/kitchen_heatmap", "/live_spoilage_status"):
            self.assertEqual(self.client.post(endpoint, environ_overrides={"REMOTE_ADDR": "192.0.2.1"}).status_code, 403)
        self.assertEqual(self.client.get("/today_special").status_code, 200)

    def test_heatmap_reuses_existing_inference_and_labels_simulated_estimates(self):
        cv = Mock()
        cv.imread.return_value = np.zeros((30, 30, 3), dtype=np.uint8)
        cv.cvtColor.side_effect = lambda pixels, code: pixels
        cv.addWeighted.return_value = cv.imread.return_value
        cv.imwrite.side_effect = lambda path, image: Path(path).write_bytes(JPEG) > 0
        image_module = Mock()
        image_module.open.return_value = MagicMock()
        gemini = Mock()
        gemini.generate_content.side_effect = [
            SimpleNamespace(text='{"areas":[{"name":"Prep","waste_level":"Medium","x_percent":0.5,"y_percent":0.5}]}'),
        ]
        with patch("dashboard_ai_api.GeminiModel", return_value=gemini), patch(
            "dashboard_ai_api.optional_import", side_effect=lambda module: cv if module == "cv2" else image_module
        ), patch("dashboard_ai_api.validate_image"):
            heat = self.upload("heatmap")
        self.assertEqual(heat.status_code, 200, heat.json)
        self.assertEqual(heat.json["basis"], "simulated_from_gemini_station_levels")
        self.assertEqual(heat.json["stations"][0]["waste_kg"], 5.0)
        for url in (heat.json["heatmap_url"], heat.json["distribution_url"]):
            with self.client.get(url, headers={"Origin": ORIGIN}) as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "image/jpeg")
            self.assertEqual(self.client.get(url.rsplit("/", 1)[0] + "/source.jpg").status_code, 404)
        self.assertEqual(gemini.generate_content.call_count, 1)
