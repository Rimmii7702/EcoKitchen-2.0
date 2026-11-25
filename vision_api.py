"""Local JSON endpoints for the existing vision workflows."""

from pathlib import Path

from flask import Blueprint, abort, jsonify, request, send_file
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from local_api import add_local_cors, require_local_request
from runtime_config import FeatureUnavailable
from vision_media import (
    IMAGE_FORMATS, IMAGE_LIMIT, MEDIA_TYPES, VIDEO_LIMIT, VIDEO_TYPES,
    media_root, validate_image, validated_upload,
)
from spoilage import analyze_spoilage, prepare_spoilage_analyzer


vision_api = Blueprint("vision_api", __name__, url_prefix="/api/vision")


def register_vision(app):
    app.config.setdefault("VISION_MEDIA_DIR", Path(app.instance_path) / "vision")
    app.register_blueprint(vision_api)


vision_api.before_request(require_local_request)
vision_api.after_request(add_local_cors)


@vision_api.errorhandler(HTTPException)
def request_error(error):
    if error.code == 413 and error.description == RequestEntityTooLarge.description and request.method == "POST":
        limit = VIDEO_LIMIT if request.endpoint == "vision_api.detect_video" else IMAGE_LIMIT
        return jsonify(error=f"Upload one file up to {limit // 1024**2} MiB."), 413
    return jsonify(error=error.description), error.code


@vision_api.post("/image")
def detect_image():
    validated_upload("image", IMAGE_FORMATS, IMAGE_LIMIT)
    raise FeatureUnavailable("Object detection is unavailable in this edition.")


@vision_api.post("/video")
def detect_video():
    validated_upload("video", VIDEO_TYPES, VIDEO_LIMIT)
    raise FeatureUnavailable("Video object detection is unavailable in this edition.")


@vision_api.post("/spoilage")
def detect_spoilage():
    upload, extension = validated_upload("image", IMAGE_FORMATS, IMAGE_LIMIT)
    model, image_module = prepare_spoilage_analyzer()
    validate_image(upload.stream, image_module, extension)
    try:
        return jsonify(analysis=analyze_spoilage(upload.stream, model, image_module))
    except ValueError as exc:
        abort(400, description=str(exc))


@vision_api.get("/media/<uuid:job_id>/<asset>")
def media(job_id, asset):
    if asset not in MEDIA_TYPES:
        abort(404)
    file = media_root() / job_id.hex / asset
    try:
        if file.resolve() != file or not file.is_file():
            abort(404)
    except (OSError, RuntimeError):
        abort(404)
    response = send_file(
        file, mimetype=MEDIA_TYPES[asset], conditional=True,
        as_attachment=request.args.get("download") == "1",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
