"""Bind the inventory database to Flask without depending on an API blueprint."""

import os

from flask import current_app

from inventory_db import InventoryDatabase
from runtime_config import project_path


SETUP_MESSAGE = "Inventory database is not initialized. Run init-inventory-db or import-inventory."


def configure_inventory_database(app):
    app.config.setdefault("INVENTORY_DB_PATH", os.getenv("INVENTORY_DB_PATH") or "instance/inventory.sqlite3")
    app.extensions["inventory_database"] = InventoryDatabase(project_path(app.config["INVENTORY_DB_PATH"]))


def database() -> InventoryDatabase:
    return current_app.extensions["inventory_database"]
