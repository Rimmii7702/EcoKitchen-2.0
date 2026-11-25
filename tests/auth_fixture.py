"""Provision isolated accounts and authenticate existing API regression tests."""

from functools import lru_cache
from shutil import rmtree
from unittest.mock import patch
from uuid import uuid4

from flask.testing import FlaskClient
from werkzeug.security import generate_password_hash

from auth_store import AuthStore
from runtime_config import ROOT
from security import register_security


PASSWORD = "Offline-test-password-only"


@lru_cache(maxsize=1)
def password_hash():
    return generate_password_hash(PASSWORD)


def provision(test, app):
    if "auth" not in app.blueprints:
        register_security(app)
    directory = ROOT / "instance" / f"test-auth-{uuid4().hex}"
    directory.mkdir(parents=True)
    test.addCleanup(rmtree, directory)
    store = AuthStore(directory / "accounts.sqlite3")
    store.create_user("test-admin", password_hash(), "admin")
    store.create_user("test-viewer", password_hash(), "viewer")
    test.enterContext(patch.dict(app.extensions, {"auth_store": store}))
    return store


class CsrfClient(FlaskClient):
    csrf = None

    def open(self, *args, **kwargs):
        if self.csrf and kwargs.get("method", "GET").upper() not in {"GET", "HEAD", "OPTIONS"}:
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("X-CSRF-Token", self.csrf)
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


def sign_in(app, role="admin"):
    client = CsrfClient(app, app.response_class)
    response = client.post("/api/auth/login", json={"username": f"test-{role}", "password": PASSWORD},
                           headers={"X-EcoKitchen-Login": "1"})
    assert response.status_code == 200, response.get_data(as_text=True)
    client.csrf = response.json["csrf_token"]
    return client


def authenticated_client(test, app):
    provision(test, app)
    return sign_in(app)
