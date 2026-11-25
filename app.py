import json
from pathlib import Path
from uuid import UUID

import click
from flask import Flask, abort, jsonify, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException, NotFound
from sqlalchemy.exc import SQLAlchemyError

from dashboard_ai_api import register_ai
from forecasting_api import register_forecasting
from heat_model import process_kitchen_image
from inventary import load_inventory_data
from inventory_api import register_inventory, view_inventory
from inventory_context import database
from inventory_db import InventoryNotInitialized
from recommendations_api import recommendations_api
from reverse_proxy import HEALTH_PATH
from main_application import RecipeRecommendationSystem, read_recommendations
from own_recipe_generation import generate_recipe, load_ingredients_by_category
from runtime_config import (
    ROOT, FeatureUnavailable, GeminiModel, InferenceError,
    optional_import,
)
from vision_api import register_vision
from vision_media import (
    IMAGE_FORMATS, IMAGE_LIMIT, VIDEO_LIMIT, VIDEO_TYPES, confined_file,
    media_job, media_root, validate_image, validated_upload,
)
from spoilage import analyze_spoilage, prepare_spoilage_analyzer
from security import register_security


STATIC_DIR = ROOT / "static"
UPLOAD_DIR = ROOT / "uploads"
INVENTORY_RESULTS = STATIC_DIR / "inventory_results"

app = Flask(__name__, static_folder=str(STATIC_DIR), template_folder=str(ROOT / "templates"))
register_inventory(app)
register_vision(app)
register_ai(app)
register_forecasting(app)
app.register_blueprint(recommendations_api)
register_security(app)


@app.get(HEALTH_PATH)
def health():
    try:
        with database().session() as inventory:
            inventory.connection().exec_driver_sql("SELECT 1")
    except (InventoryNotInitialized, SQLAlchemyError):
        return jsonify(status="unavailable"), 503
    return jsonify(status="ok")


@app.errorhandler(FeatureUnavailable)
@app.errorhandler(InferenceError)
def inference_failure(error):
    status = 503 if isinstance(error, FeatureUnavailable) else 502
    if request.endpoint in {"waste", "kitchen_heatmap"}:
        template = "waste.html" if request.endpoint == "waste" else "kitchen_heatmap.html"
        return render_template(template, error=str(error)), status
    return jsonify(error=str(error)), status


@app.errorhandler(HTTPException)
def legacy_http_error(error):
    if request.endpoint in {"waste", "kitchen_heatmap"}:
        template = "waste.html" if request.endpoint == "waste" else "kitchen_heatmap.html"
        return render_template(template, error=error.description), error.code
    if request.endpoint in {"upload_image", "upload_video", "live_spoilage_status"}:
        return jsonify(error=error.description), error.code
    if error.code == 413 and request.path.startswith("/api/"):
        return jsonify(error="The request exceeds the permitted upload or form size."), 413
    return error


def legacy_job_id(filename, suffix):
    if not filename.endswith(suffix):
        return None
    name = filename.removesuffix(suffix)
    try:
        value = UUID(hex=name)
        return value if value.hex == name else None
    except ValueError:
        return None


def result_file(filename):
    if secure_filename(filename) != filename or Path(filename).suffix != ".json":
        abort(404)
    job = legacy_job_id(filename, "_result.json")
    if job is not None:
        return confined_file(media_root() / job.hex, "result.json")
    return confined_file(INVENTORY_RESULTS, filename)


def original_upload(filename):
    if secure_filename(filename) != filename:
        abort(404)
    extension = Path(filename).suffix.lower()
    if extension not in {*IMAGE_FORMATS, *VIDEO_TYPES}:
        abort(404)
    job = legacy_job_id(filename, extension)
    if job is not None:
        name = ("video" if extension in VIDEO_TYPES else "source") + extension
        return confined_file(media_root() / job.hex, name)
    return confined_file(UPLOAD_DIR, filename)


@app.route("/legacy/")
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/Inventory_track")
def inverntory():
    return render_template("Inventory_track.html")


@app.route("/uploads/<path:filename>")
def uploads(filename):
    return send_file(original_upload(filename))


@app.route("/inventory_results/<path:filename>")
def inventory_results(filename):
    return send_file(result_file(filename))


@app.route("/results_image")
def results_image():
    return show_results(request.args.get(
        "json_file", "demo_result.json"
    ))


@app.route("/upload_image", methods=["POST"])
def upload_image():
    validated_upload("image", IMAGE_FORMATS, IMAGE_LIMIT)
    raise FeatureUnavailable("Object detection is unavailable in this edition.")


@app.route("/upload_video", methods=["POST"])
def upload_video():
    validated_upload("video", VIDEO_TYPES, VIDEO_LIMIT)
    raise FeatureUnavailable("Video object detection is unavailable in this edition.")


@app.route("/video_feed/<filename>")
def video_feed(filename):
    abort(404)


@app.route("/results/<json_file>")
def show_results(json_file):
    if secure_filename(json_file) != json_file or Path(json_file).suffix != ".json":
        return render_template("results_error.html", error="Results file not found."), 404
    try:
        path = result_file(json_file)
        result = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotFound):
        return render_template("results_error.html", error="Results file not found."), 404
    except (OSError, ValueError):
        return render_template("results_error.html", error="Could not read the saved results."), 500

    if isinstance(result, dict):
        if isinstance(result.get("image"), str):
            if not isinstance(result.get("items"), list):
                result["items"] = []
            if result.get("total_items") is None:
                result["total_items"] = len(result["items"])
            return render_template("results_image.html", result=result)
        if isinstance(result.get("video"), str):
            filename = result["video"]
            try:
                original_upload(filename)
                result["video_url"] = url_for("uploads", filename=filename)
            except NotFound:
                pass
            if not isinstance(result.get("change_log"), list):
                result["change_log"] = []
            return render_template("results_video.html", result=result)
    return render_template("results_error.html", error="Unexpected saved result format."), 500


@app.route("/live_spoilage_status", methods=["GET", "POST"])
def live_spoilage_status():
    if request.method == "GET":
        return render_template("live_spoilage_status.html", analysis=None)

    upload, extension = validated_upload("image", IMAGE_FORMATS, IMAGE_LIMIT)
    model, image = prepare_spoilage_analyzer()
    validate_image(upload.stream, image, extension)
    with media_job() as (job, directory):
        path = directory / f"source{extension}"
        upload.save(path)
        try:
            analysis = analyze_spoilage(path, model, image)
        except ValueError as exc:
            abort(400, description=str(exc))
        return jsonify(analysis=analysis, uploaded_image=f"{job.hex}{extension}")


@app.route("/waste_analysis", methods=["GET", "POST"])
def waste():
    if request.method == "GET":
        return render_template("waste.html")

    validated_upload("image", IMAGE_FORMATS, IMAGE_LIMIT)
    raise FeatureUnavailable("Annotated waste-photo analysis is unavailable in this edition.")


@app.route("/kitchen_heatmap", methods=["GET", "POST"])
def kitchen_heatmap():
    if request.method == "GET":
        return render_template("kitchen_heatmap.html")

    upload, extension = validated_upload("image", IMAGE_FORMATS, IMAGE_LIMIT)
    gemini = GeminiModel()
    cv2 = optional_import("cv2")
    image = optional_import("PIL.Image")
    validate_image(upload.stream, image, extension)
    with media_job() as (job, directory):
        path = directory / f"source{extension}"
        heatmap = directory / "kitchen_heatmap.jpg"
        graph = directory / "kitchen_distribution.jpg"
        upload.save(path)
        try:
            result = process_kitchen_image(path, heatmap, graph, gemini, cv2, image)
        except ValueError as exc:
            abort(400, description=str(exc))
        result["heatmap_path"] = url_for("vision_api.media", job_id=job, asset=heatmap.name)
        result["dist_graph_path"] = url_for("vision_api.media", job_id=job, asset=graph.name)
        return render_template("kitchen_heatmap.html", upload_path=url_for("vision_api.media", job_id=job, asset=path.name),
                               result=result)


@app.route("/recipe")
def recipe():
    return render_template("own_recipe_generation.html")


@app.route("/ingredients")
def ingredients():
    return jsonify(load_ingredients_by_category())


@app.route("/generate-recipe", methods=["POST"])
def get_recipe():
    data = request.get_json(silent=True)
    selected = data.get("ingredients") if isinstance(data, dict) else None
    if not isinstance(selected, list) or not selected or not all(isinstance(item, str) and item.strip() for item in selected):
        return jsonify(error="Select at least one ingredient."), 400
    return jsonify(recipe=generate_recipe(selected))


@app.route("/inventory-page")
def inventory_page():
    inventory = view_inventory()
    return render_template("inventory.html", inventory=load_inventory_data() if inventory is None else inventory)


@app.route("/today_special")
def today_special():
    snapshot = read_recommendations()
    return render_template("todays_special.html", snapshot_date=snapshot.get("date"),
                           fictional_demo=snapshot.get("fictional_demo") is True)


@app.route("/api/todays_special")
def get_todays_special():
    recommendations = read_recommendations()
    menu = recommendations.get("todays_special")
    if not menu:
        return jsonify(message="No saved specials. Generate recommendations to populate this menu."), 404
    return jsonify(todays_special=menu, ingredients=recommendations.get("expiring_ingredients", []),
                   fictional_demo=recommendations.get("fictional_demo") is True)


@app.route("/recipe_detail")
def recipe_detail():
    menu = read_recommendations().get("todays_special", {})
    dish = menu.get(request.args.get("dish")) if isinstance(menu, dict) else None
    return render_template("recipe_detail.html", dish=dish)


@app.route("/api/menu/twoweek")
def get_two_week_menu():
    menu = read_recommendations().get("two_week_menu")
    if not menu:
        return jsonify(message="No saved two-week menu. Generate recommendations to populate it."), 404
    return jsonify(menu)


@app.cli.command("refresh-recommendations")
def refresh_recommendations():
    """Generate and save fresh menu recommendations with Gemini."""
    try:
        system = RecipeRecommendationSystem()
        system.load_data()
        path = system.save_recommendations()
    except (FeatureUnavailable, InferenceError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Recommendations saved to {path}")
