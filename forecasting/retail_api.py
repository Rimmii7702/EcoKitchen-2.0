"""Expose the validated, versioned retail report without loading models."""

import json
from functools import lru_cache

from flask import abort, jsonify

from .retail_benchmark import project_holdout


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


def benchmark_response(path):
    scorecard = read_json(path)
    if scorecard is None:
        abort(503, description="The published retail benchmark scorecard is missing or unreadable.")
    try:
        data = project_holdout(scorecard)
    except (TypeError, KeyError, ValueError, OverflowError):
        abort(503, description="The published retail benchmark scorecard could not be verified.")
    return jsonify(data)
