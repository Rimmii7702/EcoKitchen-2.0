import hashlib
import os
import shutil
from secrets import token_hex
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.exc import OperationalError

from auth_store import AuthStore
from deploy.render_demo import SCORECARD, VIEWER, application, create_app, prepare_state
from runtime_config import ROOT


ORIGIN = "https://ecokitchen-smoke.onrender.com"
FORWARDED = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "192.0.2.20"}


class RenderDemoTests(unittest.TestCase):
    def state_directory(self):
        directory = ROOT / "instance" / ("test-render-" + uuid4().hex)
        directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, directory)
        return directory

    def setUp(self):
        self.directory = self.state_directory()
        prepare_state(self.directory)
        self.app = create_app(self.directory, ORIGIN, token_hex(32))
        self.inventory = self.app.extensions["inventory_database"]
        self.addCleanup(self.inventory.engine.dispose)
        self.client = self.app.test_client()

    def call(self, path, method="GET", *, headers=None, **kwargs):
        return self.client.open(
            path, method=method, base_url=ORIGIN.replace("https", "http", 1),
            headers={**FORWARDED, **(headers or {})}, **kwargs,
        )

    def test_startup_seeds_only_fictional_inventory_and_viewer(self):
        self.assertEqual(self.call("/api/health").json, {"status": "ok"})
        with AuthStore(self.directory / "auth.sqlite3").connection() as accounts:
            items = accounts.execute("SELECT username, role FROM users").fetchall()
            self.assertEqual([tuple(account) for account in items], [(VIEWER, "viewer")])
            self.assertEqual(accounts.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
        response = self.call("/api/auth/session")
        self.assertEqual(response.json, {
            "public_demo": True, "user": {"username": VIEWER, "role": "viewer"}, "csrf_token": None,
        })
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertEqual(response.headers["Strict-Transport-Security"], "max-age=86400")

    def test_new_instance_recreates_fictional_state(self):
        new_directory = self.state_directory()
        prepare_state(new_directory)
        new_app = create_app(new_directory, ORIGIN, token_hex(32))
        self.addCleanup(new_app.extensions["inventory_database"].engine.dispose)
        second_account = AuthStore(new_directory / "auth.sqlite3").user(VIEWER)
        original_account = AuthStore(self.directory / "auth.sqlite3").user(VIEWER)
        self.assertEqual(second_account["role"], "viewer")
        self.assertNotEqual(second_account["password_hash"], original_account["password_hash"])
        self.assertEqual(
            new_app.test_client().get("/api/inventory", base_url=ORIGIN, headers=FORWARDED).json,
            self.call("/api/inventory").json,
        )

    def test_all_supported_operations_read_existing_data(self):
        lots = self.call("/api/inventory").json
        self.assertEqual(lots["total"], 10)
        self.assertTrue(all(lot["ingredient"]["name"].startswith("Demo ") for lot in lots["items"]))
        self.assertEqual(self.call("/api/inventory?q=chickpeas").json["total"], 3)
        self.assertEqual(self.call("/api/inventory/ingredients").json["total"], 6)
        self.assertEqual(self.call("/api/inventory/history").json["total"], 11)
        self.assertEqual(self.call("/api/inventory?limit=0").status_code, 400)
        self.assertEqual(self.call("/api/inventory/alerts").status_code, 200)
        recommendations = self.call("/api/recommendations").json
        self.assertTrue(recommendations["near_expiry"])
        self.assertTrue(recommendations["low_stock"])
        self.assertTrue(self.call("/api/ai/menu").json["saved_snapshot"]["fictional_demo"])

    def test_rejects_mutations_and_private_paths(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            for path in ("/api/inventory", "/api/inventory/ingredients/1", "/api/auth/login",
                         "/api/auth/logout", "/api/recommendations/simulate", "/api/vision/image"):
                with self.subTest(method=method, path=path):
                    self.assertEqual(self.call(path, method, json={}).status_code, 405)
        for path in ("/login", "/legacy/", "/uploads/photo.jpg", "/api/vision/media/job/source.jpg",
                     "/api/ai/recipe-ingredients", "/api/admin", "/static/anything", "/.env"):
            self.assertEqual(self.call(path).status_code, 404, path)
        self.assertEqual(self.call("/api/inventory").json["total"], 10)

    def test_sqlite_readonly_even_if_a_handler_attempts_to_write(self):
        with self.assertRaises(PermissionError), self.inventory.session(write=True):
            self.fail("Write session was granted")
        with self.assertRaisesRegex(OperationalError, "readonly"):
            with self.inventory.engine.begin() as connection:
                connection.exec_driver_sql("DELETE FROM inventory_lots")

    def test_origin_and_proxy_controls(self):
        self.assertEqual(self.call("/api/inventory", headers={"Origin": ORIGIN}).status_code, 200)
        self.assertEqual(self.call("/api/inventory", headers={"Origin": "https://elsewhere.test"}).status_code, 403)
        self.assertEqual(self.call("/api/inventory", headers={"X-Forwarded-Proto": "http"}).status_code, 403)
        self.assertEqual(self.call("/api/inventory", headers={"Host": "untrusted.test"}).status_code, 403)
        self.assertEqual(self.call("/api/inventory", environ_base={"REMOTE_ADDR": "192.0.2.22"}).status_code, 403)
        self.assertEqual(self.client.get("/api/health", base_url="http://127.0.0.1:8000").status_code, 403)

    def test_benchmark_is_the_exact_published_scorecard(self):
        self.assertEqual(hashlib.sha256(SCORECARD.read_bytes()).hexdigest(),
                         "3e698b7c117140c6ed9f2598530beaaaabf5274ef123eebef75057df67bbd061")
        result = self.call("/api/forecasting/retail-benchmark").json
        self.assertEqual(result["evaluation"]["rows"], 350_000)
        self.assertEqual(result["models"]["lightgbm"]["metrics"]["wape_pct"], 33.6450157277)
        self.assertEqual(result["models"]["seasonal_naive"]["metrics"]["wape_pct"], 41.8314054769)
        self.assertEqual(result["wape_improvement_pp"], 8.1863897492)

    def test_dedicated_factory_requires_opt_in(self):
        with patch.dict(os.environ, {"ECOKITCHEN_PUBLIC_DEMO": ""}):
            with self.assertRaisesRegex(RuntimeError, "explicitly enable"):
                application()
        with self.assertRaises(ValueError):
            create_app(self.directory, "https://other.test", token_hex(32))


class RenderReadinessTests(unittest.TestCase):
    site = "http://127.0.0.1:12000"

    def test_retries_only_transient_startup_failures(self):
        from deploy.smoke_render import wait_ready
        from time import monotonic

        with (
            patch("deploy.smoke_render.probe_health", side_effect=[
                ConnectionRefusedError(), ConnectionResetError(), "HTTP 502", None,
            ]) as probe,
            patch("deploy.smoke_render.docker", return_value="running"),
            patch("deploy.smoke_render.time.sleep"),
        ):
            self.assertGreaterEqual(wait_ready("demo", self.site, monotonic()), 0)
        self.assertEqual(probe.call_count, 4)

    def test_startup_fails_when_container_exits(self):
        from deploy.smoke_render import wait_ready
        from time import monotonic

        with (
            patch("deploy.smoke_render.probe_health", side_effect=ConnectionRefusedError),
            patch("deploy.smoke_render.docker", return_value="exited"),
            self.assertRaisesRegex(RuntimeError, "Container stopped during startup"),
        ):
            wait_ready("demo", self.site, monotonic())

    def test_health_requires_exact_200_json_response(self):
        from deploy.smoke_render import HOST, probe_health

        with patch("deploy.smoke_render.HTTPConnection") as factory:
            connection = factory.return_value
            response = connection.getresponse.return_value
            response.headers.get_content_type.return_value = "application/json"
            response.status = 200
            response.read.return_value = b'{"status":"ok"}'
            self.assertIsNone(probe_health(self.site, 2))
            connection.request.assert_called_once_with("GET", "/api/health", headers={"Host": HOST})

            for status in (302, 401, 404, 500):
                with self.subTest(status=status):
                    response.status = status
                    with self.assertRaisesRegex(RuntimeError, str(status)):
                        probe_health(self.site, 2)

            response.status = 200
            response.read.return_value = b'{"status":"unavailable"}'
            with self.assertRaisesRegex(RuntimeError, "Unexpected JSON"):
                probe_health(self.site, 2)

    def test_unavailable_health_must_end_in_failure_at_deadline(self):
        from deploy.smoke_render import wait_ready
        from time import monotonic

        with patch("deploy.smoke_render.STARTUP_TIMEOUT", 0), self.assertRaisesRegex(RuntimeError, "failed to become ready"):
            wait_ready("demo", self.site, monotonic())
