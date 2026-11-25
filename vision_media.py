"""Upload validation and private media storage shared by the dashboard APIs."""

import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from flask import abort, current_app, request


IMAGE_LIMIT = 10 * 1024**2
VIDEO_LIMIT = 50 * 1024**2
MAX_IMAGE_PIXELS = 24_000_000
IMAGE_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".bmp": "BMP"}
VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
}
ANNOTATION = "source_annotated.jpg"
MEDIA_TYPES = {
    ANNOTATION: "image/jpeg",
    **{f"source{ext}": f"image/{kind.lower()}" for ext, kind in IMAGE_FORMATS.items()},
    "waste_annotated.jpg": "image/jpeg",
    "kitchen_heatmap.jpg": "image/jpeg",
    "kitchen_distribution.jpg": "image/jpeg",
    **{f"video{ext}": mime for ext, mime in VIDEO_TYPES.items()},
}


def has_signature(data, extension):
    if extension in {".jpg", ".jpeg"}:
        return data.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".bmp":
        return data.startswith(b"BM")
    if extension == ".avi":
        return data.startswith(b"RIFF") and data[8:12] == b"AVI "
    if extension == ".mkv":
        return data.startswith(b"\x1a\x45\xdf\xa3")
    return data[4:8] == b"ftyp" or (extension == ".mov" and data[4:8] in {b"moov", b"mdat", b"wide", b"free"})


def validated_upload(field, formats, limit):
    # Allow multipart headers in addition to the exact file limit.
    request.max_content_length = limit + 64 * 1024
    request.max_form_parts = 4
    if request.mimetype != "multipart/form-data":
        abort(415, description="Send the upload as multipart/form-data.")
    files = request.files.getlist(field)
    if len(files) != 1 or len(request.files) != 1 or not files[0].filename:
        abort(400, description=f"Upload one {field} file.")
    upload = files[0]
    extension = Path(upload.filename).suffix.lower()
    if extension not in formats:
        abort(415, description=f"Unsupported {field} type. Choose {', '.join(formats)}.")
    size = upload.stream.seek(0, os.SEEK_END)
    upload.stream.seek(0)
    if size > limit:
        abort(413)
    if size == 0:
        abort(400, description="The uploaded file is empty.")
    header = upload.stream.read(12)
    upload.stream.seek(0)
    if not has_signature(header, extension):
        abort(415, description="The file contents do not match its extension.")
    return upload, extension


def validate_image(stream, image_module, extension):
    try:
        with image_module.open(stream, formats=[IMAGE_FORMATS[extension]]) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                abort(413, description="The image exceeds the 24 megapixel limit.")
            image.load()
    except image_module.DecompressionBombError:
        abort(413, description="The image exceeds the 24 megapixel limit.")
    except (OSError, ValueError, SyntaxError):
        abort(400, description="Could not decode the uploaded image.")
    stream.seek(0)


def media_root():
    return Path(current_app.config["VISION_MEDIA_DIR"]).resolve()


def confined_file(root, name):
    """Return a direct regular file without following a symlink from a served path."""
    path = root / name
    try:
        if path.parent == root and path.resolve() == path and path.is_file():
            return path
    except (OSError, RuntimeError):
        pass
    abort(404)


@contextmanager
def media_job():
    job_id = uuid4()
    directory = media_root() / job_id.hex
    directory.mkdir(parents=True)
    try:
        yield job_id, directory
    except Exception:
        shutil.rmtree(directory)
        raise
    else:
        if not any(directory.iterdir()):
            directory.rmdir()
