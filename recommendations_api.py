"""Local-only API for advisory inventory decisions."""

from datetime import date

from flask import Blueprint, abort, current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException

from inventory_context import SETUP_MESSAGE, database
from inventory_db import Ingredient, InventoryNotInitialized
from inventory_service import ID_LIMIT, InventoryError, amount, require_active, validate_fields, whole_number
from local_api import add_local_cors, require_local_request
from recommendations_service import available_lots, recommendations, simulate, stock_lots


recommendations_api = Blueprint("recommendations_api", __name__, url_prefix="/api/recommendations")


recommendations_api.before_request(require_local_request)
recommendations_api.after_request(add_local_cors)


@recommendations_api.errorhandler(HTTPException)
def request_error(error):
    return jsonify(error=error.description), error.code


@recommendations_api.errorhandler(InventoryError)
def inventory_error(error):
    return jsonify(error=str(error)), error.status


@recommendations_api.errorhandler(InventoryNotInitialized)
def missing_database(_):
    return jsonify(error=SETUP_MESSAGE), 503


@recommendations_api.errorhandler(SQLAlchemyError)
def unavailable_database(_):
    current_app.logger.warning("Could not read inventory for Smart Recommendations", exc_info=True)
    return jsonify(error="The inventory database is temporarily unavailable."), 503


@recommendations_api.get("")
def overview():
    with database().session() as session:
        return jsonify(recommendations(session, date.today()))


@recommendations_api.post("/simulate")
def scenario():
    request.max_content_length = 4096
    if not request.is_json:
        abort(415, description="Send a JSON object for the simulation.")
    payload = request.get_json(silent=True)
    required = {"ingredient_id", "consume_quantity"}
    validate_fields(payload, required | {"assumed_unit_cost"}, required)
    ingredient_id = whole_number(payload["ingredient_id"], "ingredient_id", 1, ID_LIMIT)
    consume = amount(payload["consume_quantity"], "consume_quantity")
    assumed_cost = amount(payload["assumed_unit_cost"], "assumed_unit_cost") if "assumed_unit_cost" in payload else None
    with database().session() as session:
        ingredient = require_active(session, Ingredient, ingredient_id)
        today = date.today()
        eligible = available_lots(session.scalars(stock_lots(ingredient_id)), today)
        return jsonify(simulate(ingredient, eligible, today, consume, assumed_cost))
