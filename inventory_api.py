"""Flask routes and commands for the inventory database."""

import csv
import os
from datetime import date
from pathlib import Path

import click
from flask import Blueprint, abort, current_app, jsonify, request, url_for
from flask.cli import with_appcontext
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload

import inventory_service as stock
from inventory_context import SETUP_MESSAGE, configure_inventory_database, database
from inventory_db import Ingredient, InventoryLot, InventoryNotInitialized, StockTransaction
from local_api import DEFAULT_CORS_ORIGINS, WRITE_METHODS, add_local_cors, local_access_error
from runtime_config import DATA_DIR


inventory_api = Blueprint("inventory_api", __name__, url_prefix="/api/inventory")


def register_inventory(app):
    configure_inventory_database(app)
    app.config.setdefault("INVENTORY_CORS_ORIGINS", os.getenv("INVENTORY_CORS_ORIGINS", DEFAULT_CORS_ORIGINS))
    app.register_blueprint(inventory_api)
    app.cli.add_command(init_inventory_db)
    app.cli.add_command(import_inventory)


@inventory_api.before_request
def require_local_inventory_writes():
    method = request.headers.get("Access-Control-Request-Method") if request.method == "OPTIONS" else request.method
    if method in WRITE_METHODS:
        error = local_access_error()
        if error:
            raise stock.InventoryError(error, 403)


inventory_api.after_request(add_local_cors)


def view_inventory():
    """Return database rows once configured, otherwise use the existing CSV view."""
    db = database()
    if not db.path.exists():
        return None
    try:
        with db.session() as session:
            return stock.inventory_page_data(session, date.today())
    except InventoryNotInitialized:
        abort(503, description=SETUP_MESSAGE)
    except SQLAlchemyError:
        current_app.logger.exception("Could not read inventory overview")
        abort(503, description="Inventory database is temporarily unavailable.")


@inventory_api.errorhandler(stock.InventoryError)
def inventory_error(error):
    return jsonify(error=str(error)), error.status


@inventory_api.errorhandler(InventoryNotInitialized)
def missing_database(_):
    return jsonify(error=SETUP_MESSAGE), 503


@inventory_api.errorhandler(IntegrityError)
def integrity_error(_):
    return jsonify(error="The change conflicts with an existing inventory record."), 409


@inventory_api.errorhandler(SQLAlchemyError)
def unavailable_database(_):
    current_app.logger.warning("Inventory database operation failed", exc_info=True)
    return jsonify(error="Inventory database is temporarily unavailable."), 503


def payload(fields, required=()):
    if not request.is_json:
        raise stock.InventoryError("Send a JSON request with Content-Type application/json.", 415)
    return stock.validate_fields(request.get_json(silent=True), fields, required)


def query_number(name, default, minimum, maximum):
    value = request.args.get(name, default)
    return stock.whole_number(value, name, minimum, maximum, allow_text=True)


def with_pagination(statement):
    return statement.limit(query_number("limit", 100, 1, 500)).offset(
        query_number("offset", 0, 0, 1_000_000_000)
    )


def page_data(session, query, serialize):
    page = with_pagination(query)
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return jsonify(items=[serialize(row) for row in session.scalars(page)], total=total)


def ingredient_search():
    term = request.args.get("q", "").strip()
    if len(term) > 150:
        raise stock.InventoryError("Search must be at most 150 characters.")
    if term:
        return or_(
            Ingredient.name.icontains(term, autoescape=True),
            Ingredient.category.icontains(term, autoescape=True),
            Ingredient.unit.icontains(term, autoescape=True),
        )
    return None


@inventory_api.get("/ingredients")
def list_ingredients():
    query = stock.active_ingredients().order_by(Ingredient.id)
    search = ingredient_search()
    if search is not None:
        query = query.where(search)
    with database().session() as session:
        return page_data(session, query, stock.ingredient_data)


@inventory_api.post("/ingredients")
def add_ingredient():
    values = payload(stock.INGREDIENT_FIELDS, stock.TEXT_LIMITS)
    with database().session(write=True) as session:
        item = stock.ingredient_data(stock.create_ingredient(session, values))
    return jsonify(item), 201, {"Location": url_for(".ingredient", item_id=item["id"])}


@inventory_api.route("/ingredients/<int:item_id>", methods=["GET", "PATCH", "DELETE"])
def ingredient(item_id):
    stock.whole_number(item_id, "ingredient_id", 1, stock.ID_LIMIT)
    values = payload(stock.INGREDIENT_FIELDS) if request.method == "PATCH" else None
    with database().session(write=request.method in {"PATCH", "DELETE"}) as session:
        item = stock.require_active(session, Ingredient, item_id)
        if request.method == "DELETE":
            stock.archive_ingredient(session, item)
            return "", 204
        if values is not None:
            stock.update_ingredient(session, item, values)
        return jsonify(stock.ingredient_data(item))


@inventory_api.get("")
def list_lots():
    query = stock.active_lots().order_by(InventoryLot.id)
    if "ingredient_id" in request.args:
        ingredient_id = query_number("ingredient_id", None, 1, stock.ID_LIMIT)
        query = query.where(InventoryLot.ingredient_id == ingredient_id)
    search = ingredient_search()
    if search is not None:
        query = query.join(InventoryLot.ingredient).where(search)
    source = request.args.get("source")
    if source == "csv_import":
        query = query.where(InventoryLot.import_key.is_not(None))
    elif source == "manual":
        query = query.where(InventoryLot.import_key.is_(None))
    elif source is not None:
        raise stock.InventoryError("source must be csv_import or manual.")
    today = date.today()
    with database().session() as session:
        return page_data(session, query, lambda lot: stock.lot_data(lot, today))


@inventory_api.post("")
def add_lot():
    values = payload(stock.LOT_FIELDS | {"ingredient_id"}, {"ingredient_id", "quantity"})
    with database().session(write=True) as session:
        item = stock.require_active(session, Ingredient, values.pop("ingredient_id"))
        lot = stock.lot_data(stock.create_lot(session, item, values), date.today())
    return jsonify(lot), 201, {"Location": url_for(".lot", lot_id=lot["id"])}


@inventory_api.route("/<int:lot_id>", methods=["GET", "PATCH", "DELETE"])
def lot(lot_id):
    stock.whole_number(lot_id, "lot_id", 1, stock.ID_LIMIT)
    values = payload(stock.LOT_FIELDS) if request.method == "PATCH" else None
    with database().session(write=request.method in {"PATCH", "DELETE"}) as session:
        item = stock.require_active(session, InventoryLot, lot_id)
        if request.method == "DELETE":
            stock.archive_lot(session, item)
            return "", 204
        if values is not None:
            stock.update_lot(session, item, values)
        return jsonify(stock.lot_data(item, date.today()))


@inventory_api.get("/alerts")
def list_alerts():
    days = query_number("days", 7, 0, 365)
    with database().session() as session:
        return jsonify(stock.alerts(session, date.today(), days))


@inventory_api.get("/history")
def list_history():
    query = select(StockTransaction).options(joinedload(StockTransaction.lot).joinedload(InventoryLot.ingredient))
    if "lot_id" in request.args:
        lot_id = query_number("lot_id", None, 1, stock.ID_LIMIT)
        query = query.where(StockTransaction.lot_id == lot_id)
    with database().session() as session:
        return page_data(session, query.order_by(StockTransaction.id.desc()), stock.transaction_data)


@click.command("init-inventory-db")
@with_appcontext
def init_inventory_db():
    """Create the inventory database tables."""
    try:
        database().initialize()
    except (OSError, SQLAlchemyError) as exc:
        raise click.ClickException("Could not create the inventory database. Check INVENTORY_DB_PATH.") from exc
    click.echo(f"Inventory database initialized at {database().path}")


@click.command("import-inventory")
@click.option("--csv", "csv_path", type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
              default=DATA_DIR / "fictional-ingredients.csv", show_default="data/fictional-ingredients.csv")
@with_appcontext
def import_inventory(csv_path):
    """Import the restaurant stock CSV without overwriting existing lots."""
    try:
        rows, duplicate_rows, invalid_expiries = stock.read_import_rows(csv_path)
        db = database()
        db.initialize()
        with db.session(write=True) as session:
            added, skipped = stock.import_inventory(session, rows, duplicate_rows)
    except (stock.InventoryError, csv.Error, UnicodeError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    except (SQLAlchemyError, InventoryNotInitialized) as exc:
        raise click.ClickException("Inventory import failed. Check the database path and schema.") from exc
    click.echo(f"Imported {added} lot(s). Skipped {skipped} existing or duplicate row(s).")
    for line_number, source_value in invalid_expiries:
        click.echo(f"CSV line {line_number} has an invalid expiry value: {source_value!r}.", err=True)
