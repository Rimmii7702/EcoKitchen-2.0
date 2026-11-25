"""Passwordless, explicitly constructed app for an allowlisted fictional demo."""

import json
import re
from pathlib import Path
from secrets import token_urlsafe

from flask import Flask, jsonify, request

from auth_store import AuthStore
from forecasting.retail_api import benchmark_response
from inventory_api import (
    inventory_error, list_alerts, list_history, list_ingredients, list_lots,
    missing_database, unavailable_database,
)
from inventory_db import InventoryDatabase, InventoryNotInitialized
from inventory_service import InventoryError
from local_api import local_access_error
from menu_snapshot import public_snapshot
from recommendations_api import overview
from reverse_proxy import HEALTH_PATH, PROXY_MARKER, install_reverse_proxy
from runtime_config import DATA_DIR, ROOT
from security import security_headers
from sqlalchemy.exc import SQLAlchemyError
from tools.portfolio_demo import seed_inventory
from werkzeug.exceptions import HTTPException


VIEWER = "demo-viewer"
HOST = re.compile(r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.onrender\.com", re.ASCII)
SCORECARD = ROOT / "forecasting/results/freshretailnet-holdout-v1.json"


def validate_settings(origin, secret):
    if not HOST.fullmatch(origin) or len(secret.strip()) < 32:
        raise ValueError("The demo requires its onrender.com HTTPS URL and a strong runtime secret.")


def prepare_state(directory):
    seed_inventory(directory)
    AuthStore(directory / "auth.sqlite3").create_user(VIEWER, "!" + token_urlsafe(32), "viewer")


def create_app(directory, origin, secret):
    validate_settings(origin, secret)
    app = Flask(__name__, static_folder=None, instance_path=str(directory))
    app.config.update(
        SECRET_KEY=secret, AUTH_HTTPS=True, AUTH_PROXY_ORIGIN=origin, INVENTORY_CORS_ORIGINS="",
        MAX_CONTENT_LENGTH=4096,
    )
    install_reverse_proxy(app, "127.0.0.1", origin)
    inventory = InventoryDatabase(directory / "inventory.sqlite3", read_only=True)
    app.extensions["inventory_database"] = inventory
    with (DATA_DIR / "fictional-menu.json").open(encoding="utf-8") as file:
        menu = public_snapshot(json.load(file))
    if not menu or menu.get("fictional_demo") is not True:
        raise RuntimeError("The demo requires the fictional saved menu.")

    account = AuthStore(directory / "auth.sqlite3").user(VIEWER)
    if not account or account["role"] != "viewer":
        raise RuntimeError("The seeded viewer account is unavailable.")

    @app.before_request
    def require_demo_read():
        if not request.environ.get(PROXY_MARKER) or local_access_error():
            return jsonify(error="This demo requires its configured HTTPS origin."), 403
        if request.method not in {"GET", "HEAD"}:
            return jsonify(error="The public demo is read-only."), 405, {"Allow": "GET, HEAD"}

    app.after_request(security_headers)
    app.register_error_handler(InventoryError, inventory_error)
    app.register_error_handler(InventoryNotInitialized, missing_database)
    app.register_error_handler(SQLAlchemyError, unavailable_database)

    @app.errorhandler(HTTPException)
    def request_error(error):
        return jsonify(error=error.description), error.code

    @app.get(HEALTH_PATH)
    def health():
        with inventory.session() as session:
            session.connection().exec_driver_sql("SELECT 1")
        return jsonify(status="ok")

    @app.get("/api/auth/session")
    def viewer():
        return jsonify(user={"username": VIEWER, "role": "viewer"}, csrf_token=None, public_demo=True)

    @app.get("/api/ai/menu")
    def saved_menu():
        return jsonify(saved_snapshot=menu, latest_snapshot=None, previous_snapshots=[])

    @app.get("/api/forecasting/retail-benchmark")
    def retail_benchmark():
        return benchmark_response(SCORECARD)

    for path, handler in (
        ("/api/inventory", list_lots),
        ("/api/inventory/ingredients", list_ingredients),
        ("/api/inventory/alerts", list_alerts),
        ("/api/inventory/history", list_history),
        ("/api/recommendations", overview),
    ):
        app.add_url_rule(path, view_func=handler)
    return app


def application():
    """Gunicorn factory for the dedicated image, with state supplied by the launcher."""
    import os

    if os.environ.get("ECOKITCHEN_PUBLIC_DEMO") != "1":
        raise RuntimeError("The dedicated demo launcher must explicitly enable public demo mode.")
    return create_app(
        Path(os.environ["ECOKITCHEN_DEMO_STATE"]), os.environ["RENDER_EXTERNAL_URL"],
        os.environ["ECOKITCHEN_SECRET_KEY"],
    )
