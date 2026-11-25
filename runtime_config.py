"""Local paths and on-demand access to optional inference dependencies."""

import json
import os
import re
from functools import lru_cache
from importlib import import_module
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
load_dotenv(ROOT / ".env")


class FeatureUnavailable(Exception):
    """The feature lacks a required model, credential, or dependency."""


class InferenceError(Exception):
    """Inference failed or returned data that the application cannot use."""


def project_path(path):
    return (ROOT / Path(path)).resolve()


def optional_import(module):
    try:
        return import_module(module)
    except (ImportError, OSError, RuntimeError) as exc:
        raise FeatureUnavailable(
            f"{module} could not be loaded. Check the AI dependencies in requirements-ai.txt."
        ) from exc


def recommendations_path():
    return project_path(os.getenv("RECOMMENDATIONS_FILE", "instance/recommendations.local.json"))


@lru_cache(maxsize=1)
def _gemini_client(api_key):
    genai = optional_import("google.genai")
    try:
        return genai.Client(api_key=api_key, vertexai=False)
    except Exception as exc:
        raise FeatureUnavailable("The Gemini client could not be initialized. Check GEMINI_API_KEY.") from exc


class GeminiModel:
    def __init__(self, *, model_setting="GEMINI_MODEL", default_model="gemini-3.8-flash", api_key=None):
        key = (api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")).strip()
        if not key:
            raise FeatureUnavailable("Set GEMINI_API_KEY to enable Gemini features.")
        self.name = os.getenv(model_setting, default_model).strip()
        if not self.name:
            raise FeatureUnavailable(f"Set {model_setting} to an available Gemini model.")
        self.client = _gemini_client(key)

    def generate_content(self, contents):
        try:
            response = self.client.models.generate_content(model=self.name, contents=contents)
            if response.text and response.text.strip():
                return response
        except Exception as exc:
            if getattr(exc, "code", None) in {401, 403, 404}:
                raise FeatureUnavailable("Check the Gemini key and access to the configured model.") from exc
            raise InferenceError("Gemini could not complete the request.") from exc
        raise InferenceError("Gemini returned an empty response.")


def recipe_model(api_key=None):
    return GeminiModel(
        model_setting="GEMINI_RECIPE_MODEL", default_model="gemini-3.5-flash-lite", api_key=api_key
    )


def parse_gemini_json(text):
    text = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    try:
        data = json.loads(fenced.group(1) if fenced else text)
    except ValueError as exc:
        raise InferenceError("Gemini returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise InferenceError("Gemini returned an unexpected response.")
    return data
