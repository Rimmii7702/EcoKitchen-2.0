import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from auth_fixture import authenticated_client

from app import app
import runtime_config


class AppSmokeTests(unittest.TestCase):
    def setUp(self):
        self.client = authenticated_client(self, app)
        settings = patch.dict(os.environ, {
            'GEMINI_API_KEY': '',
            'RECOMMENDATIONS_FILE': 'data/fictional-menu.json',
        })
        settings.start()
        self.addCleanup(settings.stop)

    def upload(self, route, field='image', filename='test.jpg'):
        payload = b'\xff\xd8\xff\xe0photo' if field == 'image' else b'\0\0\0\x18ftypisomclip'
        return self.client.post(route, data={field: (io.BytesIO(payload), filename)})

    def test_startup_from_another_directory_with_ai_imports_blocked(self):
        script = '''
import os
import sys

class BlockOptionalImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'ultralytics', 'torch', 'cv2', 'PIL', 'google', 'matplotlib'}:
            raise AssertionError('Optional import at startup: ' + fullname)

sys.meta_path.insert(0, BlockOptionalImports())
sys.path.insert(0, sys.argv[1])
os.environ['GEMINI_API_KEY'] = 'offline-test-key'
os.environ['ECOKITCHEN_AUTH_DB'] = sys.argv[2]
from app import app
with app.test_client() as client:
    assert client.get('/').status_code == 200
    login = client.post('/api/auth/login', json={'username': 'test-admin', 'password': 'Offline-test-password-only'},
                        headers={'X-EcoKitchen-Login': '1'})
    assert login.status_code == 200
    assert client.get('/ingredients').status_code == 200
    assert client.get('/api/todays_special').status_code == 200
'''
        result = subprocess.run(
            [sys.executable, '-c', script, str(runtime_config.ROOT), str(app.extensions["auth_store"].path)],
            cwd=runtime_config.ROOT / 'tests', capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_homepage_and_linked_pages_render(self):
        homepage = self.client.get('/')
        self.assertEqual(homepage.status_code, 200)
        for route in ('/Inventory_track', '/waste_analysis', '/kitchen_heatmap',
                      '/live_spoilage_status', '/recipe', '/today_special'):
            with self.subTest(route=route):
                self.assertIn(f'href="{route}"', homepage.get_data(as_text=True))
                self.assertEqual(self.client.get(route).status_code, 200)

    def test_fictional_catalog_inventory_and_saved_menus(self):
        catalog = self.client.get('/ingredients')
        self.assertEqual(catalog.status_code, 200)
        self.assertIn('Demo lentils', catalog.json['Pantry'])
        self.assertEqual(self.client.get('/inventory-page').status_code, 200)
        specials = self.client.get('/api/todays_special')
        self.assertEqual(specials.status_code, 200)
        self.assertTrue(specials.json['fictional_demo'])
        self.assertEqual(specials.json['todays_special']['demo_soup']['name'], 'Fictional demo lentil soup')
        two_week = self.client.get('/api/menu/twoweek')
        self.assertEqual(two_week.status_code, 200)
        self.assertEqual(two_week.json['demo_rice']['name'], 'Fictional demo herb rice')
        self.assertEqual(self.client.get('/results_image').status_code, 404)
        self.assertEqual(self.client.get('/uploads/unbundled.jpg').status_code, 404)

    def test_withheld_workflows_return_unavailable_without_loading_models(self):
        with patch('runtime_config.import_module') as optional_import:
            for route, field, filename in (
                ('/upload_image', 'image', 'test.jpg'),
                ('/upload_video', 'video', 'test.mp4'),
                ('/waste_analysis', 'image', 'test.jpg'),
            ):
                with self.subTest(route=route):
                    response = self.upload(route, field, filename)
                    self.assertEqual(response.status_code, 503)
                    self.assertIn('unavailable in this edition', response.get_data(as_text=True))
            optional_import.assert_not_called()

    def test_missing_gemini_key(self):
        for route in ('/kitchen_heatmap', '/live_spoilage_status', '/generate-recipe'):
            with self.subTest(route=route):
                response = (self.client.post(route, json={'ingredients': ['Rice']})
                            if route == '/generate-recipe' else self.upload(route))
                self.assertEqual(response.status_code, 503)
                self.assertIn('GEMINI_API_KEY', response.get_data(as_text=True))
                if response.is_json:
                    self.assertEqual(set(response.json), {'error'})

    def test_missing_gemini_sdk(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'offline-test-key'}), patch(
            'runtime_config.import_module', side_effect=ImportError
        ):
            response = self.client.post('/generate-recipe', json={'ingredients': ['Rice']})
            self.assertEqual(response.status_code, 503)
            self.assertIn('google.genai', response.json['error'])

    def test_gemini_uses_explicit_key_and_configured_model(self):
        sdk = Mock()
        generate = sdk.Client.return_value.models.generate_content
        generate.return_value.text = 'Offline test recipe'
        with patch.dict(os.environ, {
            'GEMINI_API_KEY': 'offline-test-key', 'GEMINI_RECIPE_MODEL': 'test-model',
        }), patch('runtime_config.optional_import', return_value=sdk):
            self.addCleanup(runtime_config._gemini_client.cache_clear)
            response = self.client.post('/generate-recipe', json={'ingredients': ['Rice']})
        self.assertEqual(response.json, {'recipe': 'Offline test recipe'})
        sdk.Client.assert_called_once_with(api_key='offline-test-key', vertexai=False)
        self.assertEqual(generate.call_args.kwargs['model'], 'test-model')
        self.assertIn('Rice', generate.call_args.kwargs['contents'])

    def test_menu_refresh_without_key_preserves_snapshot(self):
        before = json.dumps({'date': '2025-06-20'}).encode()
        with tempfile.NamedTemporaryFile(dir=runtime_config.ROOT, delete=False) as fixture:
            fixture.write(before)
            snapshot = Path(fixture.name)
        self.addCleanup(snapshot.unlink)
        with patch.dict(os.environ, {'RECOMMENDATIONS_FILE': str(snapshot)}):
            result = app.test_cli_runner().invoke(args=['refresh-recommendations'])
        self.assertEqual(result.exit_code, 1)
        self.assertIn('GEMINI_API_KEY', result.output)
        self.assertEqual(snapshot.read_bytes(), before)
