import io
import os
import re
import unittest
from contextlib import closing
from time import time
from unittest.mock import Mock, patch

from flask import Flask
from PIL import Image
from werkzeug.security import check_password_hash
from werkzeug.test import EnvironBuilder

from app import app
from auth_fixture import PASSWORD, provision, sign_in
from security import register_security
from vision_media import IMAGE_LIMIT


ORIGIN = "http://127.0.0.1:4173"
LOGIN = "/api/auth/login"
HEADERS = {"X-EcoKitchen-Login": "1", "Origin": ORIGIN}


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.store = provision(self, app)
        self.client = app.test_client()

    def login(self, username="test-admin", password=PASSWORD, headers=HEADERS):
        return self.client.post(LOGIN, json={"username": username, "password": password}, headers=headers)

    def test_login_cookie_and_generic_rejection(self):
        first = self.login("test-admin", "bad")
        second = self.login("nonexistent", "bad")
        self.assertEqual((first.status_code, first.json), (second.status_code, second.json))
        self.assertEqual(first.status_code, 401)
        result = self.login()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["user"], {"username": "test-admin", "role": "admin"})
        self.assertTrue(result.json["csrf_token"])
        cookie = result.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertNotIn(result.json["csrf_token"], cookie)
        self.assertEqual(result.headers["Access-Control-Allow-Credentials"], "true")
        self.assertTrue(check_password_hash(self.store.user("test-admin")["password_hash"], PASSWORD))

    def test_protected_endpoints_and_public_retail_benchmark(self):
        for path in ("/api/inventory", "/api/inventory/history", "/api/ai/menu", "/api/todays_special",
                     "/api/recommendations", "/api/forecasting"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        for path in ("/api/ai/recipes", "/upload_image", "/upload_video", "/live_spoilage_status",
                     "/waste_analysis", "/kitchen_heatmap", "/generate-recipe"):
            self.assertEqual(self.client.post(path).status_code, 401, path)
        self.assertEqual(self.client.get("/inventory-page").status_code, 302)
        self.assertEqual(self.client.get("/uploads/local-fixture.jpg").status_code, 302)
        self.assertEqual(self.client.get("/static/inventory_results/local-fixture_result.json").status_code, 302)
        self.assertEqual(self.client.get("/api/forecasting/retail-benchmark").status_code, 200)

    def test_viewer_can_read_but_cannot_run_inference_write_or_serve_media(self):
        viewer = sign_in(app, "viewer")
        self.assertEqual(viewer.get("/api/ai/menu").status_code, 200)
        self.assertEqual(viewer.get("/api/forecasting/retail-benchmark").status_code, 200)
        for path in ("/api/inventory", "/api/ai/menu", "/api/recommendations/simulate", "/upload_image"):
            self.assertEqual(viewer.post(path, json={}).status_code, 403, path)
        self.assertEqual(viewer.get("/uploads/local-fixture.jpg").status_code, 403)
        self.assertEqual(viewer.get("/static/inventory_results/local-fixture_result.json").status_code, 403)
        self.assertEqual(viewer.post("/api/auth/logout").status_code, 204)

    def test_csrf_logout_revocation_and_expiry(self):
        result = self.login()
        token = result.json["csrf_token"]
        cookie = self.client.get_cookie("ecokitchen_session").value
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 403)
        self.assertEqual(self.client.post("/api/inventory", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code, 403)
        with patch("security.time", return_value=time() + 8 * 3600 + 1):
            expiring = app.test_client()
            expiring.set_cookie("ecokitchen_session", cookie)
            self.assertEqual(expiring.get("/api/ai/menu").status_code, 401)
        self.assertEqual(self.client.post("/api/auth/logout", headers={"X-CSRF-Token": token}).status_code, 204)
        replay = app.test_client()
        replay.set_cookie("ecokitchen_session", cookie)
        self.assertEqual(replay.get("/api/ai/menu").status_code, 401)

    def test_origin_and_options(self):
        denied = self.login(headers={**HEADERS, "Origin": "http://attacker.example"})
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn("Access-Control-Allow-Credentials", denied.headers)
        self.assertEqual(self.client.post(LOGIN, json={}).status_code, 415)
        preflight = self.client.options(LOGIN, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(preflight.headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertIn("X-CSRF-Token", preflight.headers["Access-Control-Allow-Headers"])

    def test_login_throttle_and_cli_provisioning(self):
        with patch.dict(app.config, {"AUTH_LOGIN_LIMIT": 2}):
            self.assertEqual(self.login(password="bad").status_code, 401)
            self.assertEqual(self.login("other-user", "bad").status_code, 401)
            blocked = self.login()
            self.assertEqual(blocked.status_code, 429)
            self.assertGreater(int(blocked.headers["Retry-After"]), 0)
        runner = app.test_cli_runner()
        result = runner.invoke(args=["create-user", "cli-viewer", "--role", "viewer"], input=f"{PASSWORD}\n{PASSWORD}\n")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.store.user("cli-viewer")["role"], "viewer")
        self.assertNotIn(PASSWORD, result.output)

    def test_legacy_upload_limits_and_private_fixture_results(self):
        admin = sign_in(app)
        invalid = admin.post("/upload_image", data={"image": (io.BytesIO(b"not an image"), "bad.jpg")})
        self.assertEqual(invalid.status_code, 415)
        with closing(EnvironBuilder(
            path="/upload_image", method="POST", headers={"X-CSRF-Token": admin.csrf},
            data={"image": (io.BytesIO(b"x" * (IMAGE_LIMIT + 1)), "big.jpg")},
        )) as builder:
            environ = builder.get_environ()
            with closing(environ["wsgi.input"]):
                oversized = admin.open(app.request_class(environ), buffered=True)
        self.assertEqual(oversized.status_code, 413)

        fixture = self.store.path.parent / "legacy"
        fixture.mkdir()
        image = fixture / "fixture.jpg"
        Image.new("RGB", (12, 12), "#66a878").save(image)
        (fixture / "fixture_result.json").write_text('{"image":"fixture.jpg","items":[]}', encoding="utf-8")
        with patch("app.INVENTORY_RESULTS", fixture), patch("app.UPLOAD_DIR", fixture):
            self.assertEqual(admin.get("/results/fixture_result.json").status_code, 200)
            with admin.get("/uploads/fixture.jpg") as media:
                self.assertEqual((media.status_code, media.mimetype), (200, "image/jpeg"))
                self.assertEqual(media.data, image.read_bytes())
            self.assertEqual(admin.get("/uploads/../.env").status_code, 404)
            viewer = sign_in(app, "viewer")
            self.assertEqual(viewer.get("/results/fixture_result.json").status_code, 403)
            self.assertEqual(viewer.get("/uploads/fixture.jpg").status_code, 403)


class ConfigurationTests(unittest.TestCase):
    def test_https_refuses_missing_key_http_origin_and_wildcard(self):
        for key, origin in (("", "https://ui.example"), ("a" * 40, "http://ui.example"), ("a" * 40, "*")):
            with self.subTest(origin=origin), patch.dict(os.environ, {
                "ECOKITCHEN_AUTH_MODE": "https", "ECOKITCHEN_SECRET_KEY": key,
                "ECOKITCHEN_HTTPS_HOSTS": "https://api.example",
            }):
                test_app = Flask(__name__)
                test_app.config["INVENTORY_CORS_ORIGINS"] = origin
                with self.assertRaisesRegex(RuntimeError, "HTTPS requires"):
                    register_security(test_app)

    def test_https_cookie_and_plain_http_rejection(self):
        test_app = Flask(__name__)
        test_app.config["INVENTORY_CORS_ORIGINS"] = "https://ui.example"
        with patch.dict(os.environ, {
            "ECOKITCHEN_AUTH_MODE": "https", "ECOKITCHEN_SECRET_KEY": "s" * 48,
            "ECOKITCHEN_HTTPS_HOSTS": "https://api.example",
        }):
            register_security(test_app)
        provision(self, test_app)
        client = test_app.test_client()
        self.assertEqual(client.get("/api/auth/session", base_url="http://api.example").status_code, 403)
        result = client.post(LOGIN, base_url="https://api.example", json={"username": "test-admin", "password": PASSWORD},
                             headers={"X-EcoKitchen-Login": "1", "Origin": "https://ui.example"})
        self.assertEqual(result.status_code, 200)
        self.assertIn("Secure;", result.headers["Set-Cookie"])
