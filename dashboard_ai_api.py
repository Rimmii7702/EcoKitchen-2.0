"""Loopback-only dashboard endpoints for recipes, menus and waste analysis."""

from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, abort, current_app, jsonify, request, url_for
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from heat_model import process_kitchen_image
from inventory_context import SETUP_MESSAGE
from inventory_db import InventoryNotInitialized
from local_api import add_local_cors, require_local_request
from main_application import RecipeRecommendationSystem, read_recommendations, write_recommendations
from own_recipe_generation import generate_recipe
from recipe_generator import RecipeRecommender
from recipe_inventory import (
    MAX_RECIPE_INGREDIENTS, NEAR_EXPIRY_DAYS, describe_inputs, inventory_lots,
    menu_inventory_frame, recipe_inputs,
)
from runtime_config import FeatureUnavailable, GeminiModel, optional_import
from vision_media import IMAGE_FORMATS, IMAGE_LIMIT, media_job, validate_image, validated_upload


ai_api = Blueprint("ai_api", __name__, url_prefix="/api/ai")


def register_ai(app):
    app.config.setdefault("AI_MENU_DIR", Path(app.instance_path) / "ai-menus")
    app.register_blueprint(ai_api)


ai_api.before_request(require_local_request)
ai_api.after_request(add_local_cors)


@ai_api.errorhandler(HTTPException)
def request_error(error):
    if error.code == 413 and error.description == RequestEntityTooLarge.description:
        message = "Upload one image up to 10 MiB." if request.endpoint in {
            "ai_api.waste", "ai_api.heatmap",
        } else "The request exceeds the permitted size."
        return jsonify(error=message), 413
    return jsonify(error=error.description), error.code


@ai_api.errorhandler(InventoryNotInitialized)
def missing_database(_):
    return jsonify(error=SETUP_MESSAGE), 503


@ai_api.errorhandler(SQLAlchemyError)
def unavailable_database(_):
    current_app.logger.warning("Could not read inventory for recipe recommendations", exc_info=True)
    return jsonify(error="The inventory database is temporarily unavailable."), 503


@ai_api.get("/recipe-ingredients")
def available_ingredients():
    today = date.today()
    return jsonify(
        items=recipe_inputs(inventory_lots(today), today), as_of=today.isoformat(),
        near_expiry_days=NEAR_EXPIRY_DAYS, selection_limit=MAX_RECIPE_INGREDIENTS,
    )


@ai_api.post("/recipes")
def recipes():
    request.max_content_length = 16 * 1024
    if not request.is_json:
        abort(415, description="Send ingredient_ids in a JSON request.")
    payload = request.get_json(silent=True)
    ids = payload.get("ingredient_ids") if isinstance(payload, dict) else None
    if (
        not isinstance(ids, list) or not 1 <= len(ids) <= MAX_RECIPE_INGREDIENTS
        or any(type(value) is not int or value <= 0 for value in ids) or len(set(ids)) != len(ids)
    ):
        abort(400, description=f"Select 1 to {MAX_RECIPE_INGREDIENTS} distinct inventory ingredients.")
    near_expiry_only = payload.get("near_expiry_only", False)
    if type(near_expiry_only) is not bool:
        abort(400, description="near_expiry_only must be a boolean.")

    today = date.today()
    items = {item["id"]: item for item in recipe_inputs(inventory_lots(today), today)}
    if any(value not in items for value in ids):
        abort(409, description="One or more selected ingredients are no longer available. Refresh the inventory list.")
    selected = [items[value] for value in ids]
    if near_expiry_only and any(not item["near_expiry"] for item in selected):
        abort(409, description="The near-expiry stock has changed. Refresh Smart Recommendations.")
    result = generate_recipe(describe_inputs(selected), structured=True)
    return jsonify(recipe=result, inventory_inputs=selected)


def menu_directory():
    return Path(current_app.config["AI_MENU_DIR"])


def snapshot_date(value, parse):
    if isinstance(value, str):
        try:
            return parse(value).isoformat()
        except ValueError:
            return None
    return None


def public_snapshot(snapshot):
    if not snapshot:
        return None
    result = {"date": snapshot_date(snapshot.get("date"), date.fromisoformat)}
    if snapshot.get("fictional_demo") is True:
        result["fictional_demo"] = True
    generated = snapshot_date(snapshot.get("generated_at"), datetime.fromisoformat)
    if generated:
        result["generated_at"] = generated
    for group in ("todays_special", "two_week_menu"):
        dishes = snapshot.get(group)
        result[group] = {}
        if isinstance(dishes, dict):
            for key, dish in dishes.items():
                if not isinstance(dish, dict) or not isinstance(dish.get("name"), str):
                    continue
                result[group][key] = {
                    field: dish[field] for field in ("name", "description", "recipe") if isinstance(dish.get(field), str)
                }
                ingredients = dish.get("ingredients")
                if isinstance(ingredients, list):
                    result[group][key]["ingredients"] = [item for item in ingredients if isinstance(item, str)]
    return result if result["todays_special"] or result["two_week_menu"] else None


@ai_api.get("/menu")
def saved_menu():
    snapshots = []
    for path in sorted(menu_directory().glob("menu-*.json"), reverse=True):
        snapshot = public_snapshot(read_recommendations(path))
        if snapshot:
            snapshots.append({**snapshot, "id": path.stem})
    return jsonify(
        saved_snapshot=public_snapshot(read_recommendations()),
        latest_snapshot=snapshots[0] if snapshots else None,
        previous_snapshots=snapshots[1:],
    )


@ai_api.post("/menu")
def generate_menu():
    recommender = RecipeRecommender()
    today = date.today()
    lots = inventory_lots(today)
    if not lots:
        abort(409, description="Add available stock before generating a menu.")
    system = RecipeRecommendationSystem()
    system.today = today
    system.load_data(menu_inventory_frame(lots, today))
    items = recipe_inputs(lots, today)
    result = system.get_recommendations(
        recommender=recommender, available=", ".join(describe_inputs(items[:30])),
    )
    snapshot = public_snapshot(result)
    generated = datetime.now(timezone.utc)
    snapshot["generated_at"] = generated.isoformat()
    identifier = f"menu-{generated.strftime('%Y%m%dT%H%M%S%f')}-{uuid4().hex}"
    try:
        write_recommendations(menu_directory() / f"{identifier}.json", snapshot)
    except OSError:
        current_app.logger.exception("Could not save the dashboard menu")
        abort(500, description="Could not save the generated menu.")
    return jsonify(latest_snapshot={**snapshot, "id": identifier})


def photo():
    upload, extension = validated_upload("image", IMAGE_FORMATS, IMAGE_LIMIT)
    gemini = GeminiModel()
    image_module = optional_import("PIL.Image")
    validate_image(upload.stream, image_module, extension)
    return upload, extension, gemini, image_module


def generated_media(job_id, path):
    return url_for("vision_api.media", job_id=job_id, asset=path.name) if path.is_file() else None


@ai_api.post("/waste")
def waste():
    validated_upload("image", IMAGE_FORMATS, IMAGE_LIMIT)
    raise FeatureUnavailable("Annotated waste-photo analysis is unavailable in this edition.")


@ai_api.post("/heatmap")
def heatmap():
    upload, extension, gemini, image_module = photo()
    cv2 = optional_import("cv2")
    with media_job() as (job_id, directory):
        source = directory / f"source{extension}"
        heatmap = directory / "kitchen_heatmap.jpg"
        distribution = directory / "kitchen_distribution.jpg"
        upload.save(source)
        try:
            result = process_kitchen_image(source, heatmap, distribution, gemini, cv2, image_module)
        except ValueError as exc:
            abort(400, description=str(exc))
        source.unlink()
        return jsonify(
            stations=result["waste_data"], simulated_total_kg=float(result["total_waste"]),
            basis="simulated_from_gemini_station_levels",
            heatmap_url=generated_media(job_id, heatmap),
            distribution_url=generated_media(job_id, distribution),
        )
