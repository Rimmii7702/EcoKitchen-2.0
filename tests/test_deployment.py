import os
import sqlite3
import unittest
from shutil import rmtree
from unittest.mock import patch
from uuid import uuid4

from flask import Flask, url_for
from sqlalchemy.exc import DatabaseError
from werkzeug.security import generate_password_hash

from app import app
from auth_store import AuthStore
from deploy import start_backend
from inventory_api import register_inventory
from inventory_db import InventoryDatabase
from reverse_proxy import HEALTH_PATH
from runtime_config import ROOT
from security import register_security


PROXY = "172.30.96.2"
HOST = "127.0.0.1:8080"
ORIGIN = "http://" + HOST
SECRET = "fixture-secret-only-for-deployment-tests" * 2
PASSWORD = "fixture password only for deployment tests"


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "instance" / f"test-deploy-{uuid4().hex}"
        self.root.mkdir(parents=True)
        self.addCleanup(rmtree, self.root)
        self.inventory = InventoryDatabase(self.root / "inventory.sqlite3")
        self.accounts = AuthStore(self.root / "auth.sqlite3")
        self.addCleanup(self.inventory.engine.dispose)

    def make_app(self, origin=ORIGIN, **overrides):
        proxied = Flask(__name__)
        proxied.config.update(TESTING=True, INVENTORY_DB_PATH=self.inventory.path, AUTH_DB_PATH=self.accounts.path,
                              INVENTORY_CORS_ORIGINS="")
        register_inventory(proxied)
        settings = dict(ECOKITCHEN_AUTH_MODE="loopback", ECOKITCHEN_SECRET_KEY=SECRET,
                        ECOKITCHEN_TRUSTED_PROXY=PROXY, ECOKITCHEN_PROXY_ORIGIN=origin)
        with patch.dict(os.environ, {**settings, **overrides}):
            register_security(proxied)
        proxied.add_url_rule(HEALTH_PATH, "health", lambda: {"status": "ok"})
        return proxied

    def call(self, client, path, *, remote=PROXY, origin=ORIGIN, client_ip="192.0.2.3", headers=None, **kwargs):
        host = origin.split("://", 1)[1]
        return client.open(path, base_url="http://" + host, environ_overrides={"REMOTE_ADDR": remote}, headers={
            "X-Forwarded-Proto": origin.split(":", 1)[0], "X-Forwarded-For": client_ip, **(headers or {}),
        }, **kwargs)

    def test_forwarded_headers_require_exact_proxy_peer_host_and_single_client_ip(self):
        client = self.make_app().test_client()
        path = "/api/auth/session"
        self.assertEqual(self.call(client, path).status_code, 200)
        for overrides in ({"remote": "172.30.96.5"}, {"remote": "127.0.0.1"}, {"origin": "http://localhost:8080"},
                          {"client_ip": "192.0.2.3, 127.0.0.1"}, {"client_ip": ""},
                          {"headers": {"Origin": "http://localhost:8080"}},
                          {"headers": {"X-Forwarded-Proto": "https"}}):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.call(client, path, **overrides).status_code, 403)
        self.assertEqual(client.get(HEALTH_PATH, base_url="http://127.0.0.1:8000").status_code, 200)
        self.assertEqual(client.get(path, base_url="http://127.0.0.1:8000").status_code, 403)
        self.assertEqual(client.post(HEALTH_PATH, base_url="http://127.0.0.1:8000").status_code, 403)

    def test_shared_key_revocation_and_rate_limit_across_workers(self):
        self.accounts.create_user("admin-probe", generate_password_hash(PASSWORD), "admin")
        first, second = (self.make_app().test_client() for _ in range(2))
        headers = {"Origin": ORIGIN, "X-EcoKitchen-Login": "1"}
        payload = {"username": "admin-probe", "password": PASSWORD}
        login = self.call(first, "/api/auth/login", method="POST", headers=headers, json=payload)
        self.assertEqual(login.status_code, 200)
        cookie = first.get_cookie("ecokitchen_session", domain="127.0.0.1")
        second.set_cookie(cookie.key, cookie.value, domain="127.0.0.1")
        self.assertEqual(self.call(second, "/api/auth/session").json["user"]["role"], "admin")
        rejected = self.call(second, "/api/auth/logout", method="POST", headers=headers)
        self.assertEqual(rejected.status_code, 403)
        logout = self.call(second, "/api/auth/logout", method="POST", headers={
            **headers, "X-CSRF-Token": login.json["csrf_token"],
        })
        self.assertEqual(logout.status_code, 204)
        self.assertIsNone(self.call(first, "/api/auth/session").json["user"])
        for index in range(7):
            failure = self.call((first, second)[index % 2], "/api/auth/login", method="POST", headers=headers,
                                json={**payload, "password": "incorrect"})
            self.assertEqual(failure.status_code, 401)
        self.assertEqual(self.call(second, "/api/auth/login", method="POST", headers=headers,
                                   json=payload).status_code, 429)
        self.assertEqual(self.call(second, "/api/auth/login", method="POST", headers=headers,
                                   json=payload, client_ip="192.0.2.4").status_code, 200)

    def test_proxy_config_fails_closed_and_https_keeps_secure_cookies(self):
        for overrides in ({"ECOKITCHEN_TRUSTED_PROXY": "172.30.96.0/28"}, {"ECOKITCHEN_SECRET_KEY": ""},
                          {"ECOKITCHEN_PROXY_ORIGIN": "http://example.com"}):
            with self.subTest(overrides=overrides), self.assertRaises(RuntimeError):
                self.make_app(**overrides)
        site = "https://kitchen.example"
        client = self.make_app(site, ECOKITCHEN_AUTH_MODE="https", ECOKITCHEN_HTTPS_HOSTS=site).test_client()
        self.accounts.create_user("admin-probe", generate_password_hash(PASSWORD), "admin")
        response = self.call(client, "/api/auth/login", origin=site, method="POST", headers={"X-EcoKitchen-Login": "1"},
                             json={"username": "admin-probe", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Secure;", response.headers["Set-Cookie"])
        self.assertIn("max-age=", response.headers["Strict-Transport-Security"])

    def test_startup_initializes_only_missing_inventory_and_preserves_existing_state(self):
        snapshot = self.root / "recommendations.json"
        with patch.dict(app.extensions, {"inventory_database": self.inventory, "auth_store": self.accounts}), \
                patch.object(start_backend, "recommendations_path", return_value=snapshot):
            self.assertEqual(app.test_client().get(HEALTH_PATH).status_code, 503)
            start_backend.prepare()
            self.assertEqual(app.test_client().get(HEALTH_PATH).json, {"status": "ok"})
            self.assertFalse(self.accounts.path.exists())
            self.accounts.create_user("admin-probe", generate_password_hash(PASSWORD), "admin")
            snapshot.write_text("operator snapshot", encoding="utf-8")
            paths = (self.inventory.path, self.accounts.path, snapshot)
            previous = [path.read_bytes() for path in paths]
            start_backend.prepare()
            self.assertEqual([path.read_bytes() for path in paths], previous)
            with app.test_request_context():
                self.assertEqual(url_for("index"), "/")
            self.assertEqual(app.test_client().get("/legacy/").status_code, 200)

    def test_startup_refuses_a_preexisting_invalid_database(self):
        self.inventory.path.write_bytes(b"not a database")
        with patch.dict(app.extensions, {"inventory_database": self.inventory}), self.assertRaises(DatabaseError) as error:
            start_backend.prepare()
        self.assertIsInstance(error.exception.orig, sqlite3.DatabaseError)
        self.assertEqual(self.inventory.path.read_bytes(), b"not a database")
