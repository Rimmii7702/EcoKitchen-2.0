import unittest
from uuid import uuid4

from flask import Flask

from app import app
from inventory_context import configure_inventory_database, database
from runtime_config import ROOT


class ApplicationBoundaryTests(unittest.TestCase):
    def test_database_binding_does_not_require_inventory_routes_or_create_a_file(self):
        consumer = Flask(__name__)
        relative = f"instance/test-inventory-binding-{uuid4().hex}.sqlite3"
        consumer.config["INVENTORY_DB_PATH"] = relative
        configure_inventory_database(consumer)
        with consumer.app_context():
            store = database()
            self.addCleanup(store.engine.dispose)
            self.assertIs(store, consumer.extensions["inventory_database"])
            self.assertEqual(store.path, ROOT / relative)
            self.assertFalse(store.path.exists())
        self.assertNotIn("inventory_api", consumer.blueprints)

    def test_shared_local_guard_preserves_blueprint_errors_and_preflights(self):
        client = app.test_client()
        origin = "http://127.0.0.1:4173"
        endpoints = (
            "/api/ai/menu",
            "/api/forecasting/retail-benchmark",
            "/api/recommendations",
            "/api/vision/media/00000000-0000-0000-0000-000000000001/source_annotated.jpg",
        )
        for path in endpoints:
            with self.subTest(path=path):
                preflight = client.options(path, headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
                self.assertEqual(preflight.status_code, 200)
                self.assertEqual(preflight.headers["Access-Control-Allow-Origin"], origin)
                for request_options in (
                    {"headers": {"Origin": "https://external.example"}},
                    {"environ_overrides": {"REMOTE_ADDR": "192.0.2.1"}},
                    {"base_url": "http://external.example"},
                ):
                    denied = client.get(path, **request_options)
                    self.assertEqual(denied.status_code, 403)
                    self.assertEqual(set(denied.json), {"error"})
                    self.assertNotIn("Access-Control-Allow-Origin", denied.headers)
