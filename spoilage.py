"""Gemini-only spoilage photo analysis."""

from math import isfinite

from runtime_config import GeminiModel, InferenceError, optional_import


def prepare_spoilage_analyzer():
    return GeminiModel(), optional_import("PIL.Image")


def analyze_spoilage(source, model, image_module):
    prompt = (
        "You're a food inspection assistant. Analyze the food in this image and respond with the following in plain text:\n"
        "- Item name (e.g., apple, bread, tomato, etc.)\n"
        "- Spoilage status: Fresh, Soon-to-rot, or Rotten\n"
        "- Confidence (in percentage)\n"
        "- Suggested action: Safe to use, Use soon, or Discard now\n"
        "Keep your response short and in plain language. Example:\n"
        "Item: Tomato\nStatus: Rotten\nConfidence: 92%\nAction: Discard now"
    )
    try:
        with image_module.open(source) as img:
            response = model.generate_content([prompt, img])
    except (OSError, ValueError):
        raise ValueError("Could not read the uploaded image.") from None

    analysis = []
    item = {}
    fields = {"Item": "item", "Status": "status", "Confidence": "confidence", "Action": "action"}
    for line in response.text.splitlines():
        label, _, value = line.strip().partition(":")
        if label == "Item":
            item = {}
        if label in fields and value.strip():
            item[fields[label]] = value.strip().removesuffix("%") if label == "Confidence" else value.strip()
        if label == "Action" and item.keys() == set(fields.values()):
            analysis.append(item.copy())
    if not analysis:
        raise InferenceError("Gemini did not return a usable spoilage analysis.")
    return analysis
