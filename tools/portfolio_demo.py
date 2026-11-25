"""Prepare and serve a fictional, isolated inventory for local portfolio demos."""

import os
import re
from datetime import date, timedelta
from decimal import Decimal

from flask import abort, request

from inventory_db import InventoryDatabase
from inventory_service import create_ingredient, create_lot, update_lot
from runtime_config import DATA_DIR, ROOT


def demo_directory():
    name = os.environ.get("ECOKITCHEN_DEMO_NAME", "portfolio-demo")
    if not re.fullmatch(r"portfolio-demo(?:-[a-z0-9]+)*", name):
        raise SystemExit("Use portfolio-demo or portfolio-demo followed by a lowercase suffix.")
    directory = ROOT / "instance" / name
    if directory.resolve() != directory:
        raise SystemExit("The demo directory must be inside instance without links.")
    return directory


def seed():
    directory = demo_directory()
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise SystemExit("This demo already exists. Choose a new ECOKITCHEN_DEMO_NAME to seed fresh data.") from exc

    seed_inventory(directory)
    (directory / "menu-snapshot.json").write_bytes((DATA_DIR / "fictional-menu.json").read_bytes())
    print(f"Created fictional inventory in instance/{directory.name}.")


def seed_inventory(directory):
    today = date.today()
    db = InventoryDatabase(directory / "inventory.sqlite3")
    db.initialize()
    inventory = (
        ("Demo chickpeas", "Pantry", "kg", "12", (("3", 1, "2.40"), ("4", 5, "2.40"), ("2", -3, "2.40"))),
        ("Demo baby spinach", "Produce", "kg", "4", (("2.5", 2, "5.20"), ("2", 6, "5.20"))),
        ("Demo arborio rice", "Pantry", "kg", "5", (("9", 19, "3.10"),)),
        ("Demo plain yogurt", "Dairy", "kg", "5", (("2", 3, None), ("1", None, None))),
        ("Demo cherry tomatoes", "Produce", "kg", "3", (("4.5", 4, "4.10"),)),
        ("Demo lemons", "Produce", "kg", "2", (("2.5", 9, "2.90"),)),
    )
    try:
        with db.session(write=True) as session:
            for name, category, unit, minimum, lots in inventory:
                ingredient = create_ingredient(session, {
                    "name": name, "category": category, "unit": unit, "minimum_stock": Decimal(minimum),
                })
                for quantity, days, cost in lots:
                    lot = create_lot(session, ingredient, {
                        "quantity": Decimal(quantity),
                        "expiry_date": None if days is None else today + timedelta(days=days),
                        "cost_per_unit": None if cost is None else Decimal(cost),
                    })
                    if name == "Demo arborio rice":
                        update_lot(session, lot, {"quantity": Decimal("8")})
    finally:
        db.engine.dispose()


def create_app():
    directory = demo_directory()
    inventory = directory / "inventory.sqlite3"
    if not inventory.is_file() or inventory.resolve() != inventory:
        raise SystemExit("Seed the isolated inventory with python -m tools.portfolio_demo first.")
    for name in ("auth.sqlite3", "menus", "media"):
        path = directory / name
        if path.resolve() != path:
            raise SystemExit("Demo state must stay inside the isolated directory.")

    os.environ.update(
        INVENTORY_DB_PATH=str(inventory),
        ECOKITCHEN_AUTH_DB=str(directory / "auth.sqlite3"),
        ECOKITCHEN_AUTH_MODE="loopback",
        ECOKITCHEN_TRUSTED_PROXY="",
        ECOKITCHEN_PROXY_ORIGIN="",
        ECOKITCHEN_SECRET_KEY="",
        INVENTORY_CORS_ORIGINS="http://127.0.0.1:4173",
        GEMINI_API_KEY="",
        RECOMMENDATIONS_FILE=str(directory / "menu-snapshot.json"),
        FORECAST_DEMAND_ARTIFACT=str(directory / "missing-demand.json"),
        FORECAST_WASTE_SIM_ARTIFACT=str(directory / "missing-waste.json"),
    )
    from app import app

    if app.extensions["inventory_database"].path != inventory:
        raise SystemExit("Flask already loaded a different inventory configuration.")
    app.config.update(AI_MENU_DIR=directory / "menus", VISION_MEDIA_DIR=directory / "media")

    @app.before_request
    def dashboard_api_only():
        if not request.path.startswith("/api/"):
            abort(404)

    return app


if __name__ == "__main__":
    seed()
