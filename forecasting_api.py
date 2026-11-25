"""Local forecasts and clearly labeled synthetic scenarios."""

import csv
import json
import os
from functools import lru_cache

from flask import Blueprint, abort, current_app, jsonify, request
from werkzeug.exceptions import HTTPException

from forecasting.data import audit_sales_sample
from forecasting.demand import LAGS, METHODS, predict_demand
from forecasting.retail_benchmark import project_holdout
from forecasting.waste import CATEGORIES, SCENARIO_FIELDS, predict_waste, scenario_values
from local_api import add_local_cors, require_local_request
from runtime_config import ROOT, project_path


forecasting_api = Blueprint("forecasting_api", __name__, url_prefix="/api/forecasting")
RESULTS = ROOT / "forecasting" / "results"
DEMAND_MISSING = "No trained demand model is available. Train on complete, verified weekly sales first."
WASTE_MISSING = "The synthetic Random Forest artifact is unavailable. Run the local training command to enable simulation."


def register_forecasting(app):
    for setting, run in (
        ("FORECAST_DEMAND_ARTIFACT", "demand-v1"),
        ("FORECAST_WASTE_SIM_ARTIFACT", "waste-synthetic-v1"),
    ):
        app.config.setdefault(setting, os.getenv(setting) or f"instance/forecasting/{run}/model.json")
    app.register_blueprint(forecasting_api)


forecasting_api.before_request(require_local_request)
forecasting_api.after_request(add_local_cors)


@forecasting_api.errorhandler(HTTPException)
def request_error(error):
    return jsonify(error=error.description), error.code


def invalid_constant(value):
    raise ValueError(f"Invalid JSON number: {value}")


@lru_cache(maxsize=6)
def decode_file(path, _mtime, _size):
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid_constant)


def read_json(path):
    try:
        stat = path.stat()
        data = decode_file(path, stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@forecasting_api.get("/retail-benchmark")
def retail_benchmark():
    scorecard = read_json(RESULTS / "freshretailnet-holdout-v1.json")
    if scorecard is None:
        abort(503, description="The published retail benchmark scorecard is missing or unreadable.")
    try:
        data = project_holdout(scorecard)
    except (TypeError, KeyError, ValueError, OverflowError):
        abort(503, description="The published retail benchmark scorecard could not be verified.")
    return jsonify(data)


def artifact_for(task):
    demand = task == "demand"
    key = "FORECAST_DEMAND_ARTIFACT" if demand else "FORECAST_WASTE_SIM_ARTIFACT"
    artifact = read_json(project_path(current_app.config[key]))
    if artifact is None:
        return None
    report = artifact.get("report")
    if not isinstance(report, dict) or report.get("schema_version") != 1 or report.get("task") != task:
        return None
    source_kind = "operator_verified_weekly_sales" if demand else "synthetic"
    source = report.get("source")
    if not isinstance(source, dict) or source.get("kind") != source_kind:
        return None
    if demand:
        if (
            report.get("selected") not in METHODS
            or not isinstance(artifact.get("history"), list)
            or not artifact["history"]
            or not isinstance(artifact.get("window"), list)
            or len(artifact["window"]) != LAGS
        ):
            return None
    elif (
        report.get("selected") != "random_forest" or artifact.get("categories") != sorted(CATEGORIES)
        or not isinstance(artifact.get("trees"), list) or not artifact["trees"]
    ):
        return None
    return artifact


@forecasting_api.get("")
def dashboard():
    try:
        demo = audit_sales_sample()
    except (OSError, ValueError, csv.Error):
        demo = read_json(RESULTS / "demand-audit-v1.json") or {"history": [], "source": None, "coverage": None}
    demand = artifact_for("demand")
    simulation = artifact_for("waste_simulation")
    waste_report = simulation["report"] if simulation else read_json(RESULTS / "waste-synthetic-v1.json")
    return jsonify(
        demand={
            "available": demand is not None,
            "reason": None if demand else demo.get("reason", DEMAND_MISSING),
            "history": demand["history"] if demand else demo["history"],
            "source": demand["report"]["source"] if demand else demo["source"],
            "coverage": None if demand else demo["coverage"],
            "report": demand["report"] if demand else None,
        },
        waste={
            "available": False,
            "reason": "No measured waste targets are included in the project. The available model is a synthetic scenario experiment.",
            "simulation_available": simulation is not None,
            "simulation_reason": None if simulation else WASTE_MISSING,
            "fields": SCENARIO_FIELDS,
            "report": waste_report,
        },
    )


def json_payload():
    request.max_content_length = 8192
    if not request.is_json:
        abort(415, description="Send a JSON request.")
    return request.get_json(silent=True)


@forecasting_api.post("/demand/predict")
def demand_prediction():
    payload = json_payload()
    if (
        not isinstance(payload, dict) or set(payload) != {"horizon_weeks"}
        or type(payload["horizon_weeks"]) is not int or payload["horizon_weeks"] != 1
    ):
        abort(400, description="Set horizon_weeks to the integer 1.")
    artifact = artifact_for("demand")
    if artifact is None:
        abort(503, description=DEMAND_MISSING)
    try:
        result = predict_demand(artifact)
    except (TypeError, KeyError, ValueError, OverflowError):
        abort(503, description=DEMAND_MISSING)
    return jsonify(**result, source_kind=artifact["report"]["source"]["kind"])


@forecasting_api.post("/waste-simulation/predict")
def waste_scenario():
    try:
        payload = scenario_values(json_payload())
    except ValueError as exc:
        abort(400, description=str(exc))
    artifact = artifact_for("waste_simulation")
    if artifact is None:
        abort(503, description=WASTE_MISSING)
    try:
        result = predict_waste(artifact, payload)
    except (TypeError, KeyError, ValueError, IndexError, ZeroDivisionError, OverflowError):
        abort(503, description=WASTE_MISSING)
    return jsonify(
        simulated_waste_units=result, source_kind="synthetic",
        model=artifact["report"]["selected"],
    )
