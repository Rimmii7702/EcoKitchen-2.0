import io
import shutil
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from werkzeug.test import EnvironBuilder

from auth_fixture import authenticated_client

from app import app
from runtime_config import ROOT


JPEG = b"\xff\xd8\xff\xe0test-photo"
MP4 = b"\x00\x00\x00\x18ftypisomtest-clip"
API = "/api/vision"
ORIGIN = "http://127.0.0.1:5173"


def pillow_mock():
    module = Mock()
    module.DecompressionBombError = type("DecompressionBombError", (Exception,), {})
    image = Mock(width=100, height=100)
    module.open.return_value.__enter__ = Mock(return_value=image)
    module.open.return_value.__exit__ = Mock(return_value=False)
    return module


class VisionApiTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "instance" / ("test-vision-" + uuid4().hex)
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        config = patch.dict(app.config, {"VISION_MEDIA_DIR": self.root, "INVENTORY_CORS_ORIGINS": ORIGIN})
        config.start()
        self.addCleanup(config.stop)
        self.client = authenticated_client(self, app)

    def upload(self, mode="image", payload=JPEG, filename="photo.jpg", **options):
        field = "video" if mode == "video" else "image"
        with closing(EnvironBuilder(
            path=f"{API}/{mode}", method="POST", data={field: (io.BytesIO(payload), filename)}, **options,
        )) as builder:
            env = builder.get_environ()
            env["HTTP_X_CSRF_TOKEN"] = self.client.csrf
            with closing(env["wsgi.input"]):
                return self.client.open(app.request_class(env), buffered=True)

    def test_spoilage_reports_model_output_without_persisting_image(self):
        model = Mock()
        model.generate_content.return_value.text = "Item: Pear\nStatus: Soon-to-rot\nConfidence: 82%\nAction: Use soon"
        with patch("vision_api.prepare_spoilage_analyzer", return_value=(model, pillow_mock())):
            response = self.upload("spoilage")
            model.generate_content.return_value.text = "Unexpected provider output."
            invalid = self.upload("spoilage")
        self.assertEqual(response.json, {"analysis": [{
            "item": "Pear", "status": "Soon-to-rot", "confidence": "82", "action": "Use soon",
        }]})
        self.assertEqual(invalid.status_code, 502)
        self.assertEqual(set(invalid.json), {"error"})
        self.assertFalse(self.root.exists())

    def test_validation_prevents_invalid_files_and_nonlocal_requests(self):
        with patch("vision_api.prepare_spoilage_analyzer") as scanner, patch("vision_api.IMAGE_LIMIT", 1024**2):
            self.assertEqual(self.upload("spoilage", payload=b"not a JPEG").status_code, 415)
            self.assertEqual(self.upload("spoilage", filename="image.svg").status_code, 415)
            self.assertEqual(self.upload("spoilage", payload=b"").status_code, 400)
            self.assertEqual(self.upload("spoilage", payload=JPEG + b"x" * 1024**2).status_code, 413)
            self.assertEqual(self.upload("spoilage", payload=JPEG + b"x" * (1024**2 + 65536)).status_code, 413)
            self.assertEqual(self.upload("spoilage", headers={"Origin": "https://remote.example"}).status_code, 403)
            self.assertEqual(self.upload("spoilage", environ_overrides={"REMOTE_ADDR": "192.0.2.1"}).status_code, 403)
            scanner.assert_not_called()
        self.assertFalse(self.root.exists())
        cors = self.client.options(API + "/spoilage", headers={
            "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
        })
        self.assertEqual(cors.status_code, 200)
        self.assertEqual(cors.headers["Access-Control-Allow-Origin"], ORIGIN)

    def test_image_pixel_limit_rejects_before_inference(self):
        model = Mock()
        image_module = pillow_mock()
        image = image_module.open.return_value.__enter__.return_value
        image.width = image.height = 25000
        with patch("vision_api.prepare_spoilage_analyzer", return_value=(model, image_module)):
            response = self.upload("spoilage")
        self.assertEqual(response.status_code, 413)
        self.assertIn("24 megapixel", response.json["error"])
        model.generate_content.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_withheld_object_detection_and_missing_gemini(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            for mode, filename, payload in (("image", "fixture.jpg", JPEG), ("video", "fixture.mp4", MP4)):
                reply = self.upload(mode, payload, filename)
                self.assertEqual(reply.status_code, 503)
                self.assertIn("unavailable in this edition", reply.json["error"])
            reply = self.upload("spoilage")
        self.assertEqual(reply.status_code, 503)
        self.assertIn("GEMINI_API_KEY", reply.json["error"])
        self.assertFalse(self.root.exists())
