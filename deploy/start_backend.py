"""Initialize a fresh volume once, then replace this process with Gunicorn."""

import os
import sqlite3
from pathlib import Path
from shutil import copyfileobj

from sqlalchemy.exc import SQLAlchemyError

from app import app
from inventory_db import InventoryNotInitialized
from runtime_config import ROOT, recommendations_path


def prepare():
    inventory = app.extensions["inventory_database"]
    if not inventory.path.exists():
        inventory.initialize()
    with inventory.session() as session:
        session.connection().exec_driver_sql("SELECT 1")

    accounts = app.extensions["auth_store"]
    if accounts.path.exists():
        with accounts.connection() as connection:
            for table in ("users", "sessions", "login_limits"):
                connection.execute(f"SELECT 1 FROM {table} LIMIT 1")

    snapshot = recommendations_path()
    bundled = ROOT / "data" / "fictional-menu.json"
    if snapshot != bundled and not snapshot.exists():
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        with bundled.open("rb") as source, snapshot.open("xb") as destination:
            copyfileobj(source, destination)


if __name__ == "__main__":
    os.umask(0o077)
    try:
        prepare()
    except (OSError, InventoryNotInitialized, SQLAlchemyError, sqlite3.Error):
        raise SystemExit("EcoKitchen startup failed. Check the runtime volume permissions and database schemas.") from None
    os.execvp("gunicorn", ["gunicorn", "--config", str(Path(__file__).with_name("gunicorn.conf.py")), "app:app"])
