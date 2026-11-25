"""Run HTTP smoke checks against a disposable local Compose project."""

import json
import os
import re
import subprocess
from http.cookiejar import CookieJar
from pathlib import Path
from secrets import token_hex, token_urlsafe
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
CREATE_ACCOUNT = """
import json, os, sys
from app import app
os.umask(0o077)
username, role, password = json.load(sys.stdin)
result = app.test_cli_runner().invoke(args=['create-user', username, '--role', role],
                                      input=password + '\\n' + password + '\\n')
if result.exit_code:
    raise SystemExit('Could not create smoke-test account')
"""


def verify(compose, site):
    admin = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
    viewer = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

    def call(path, status=200, *, browser=admin, method="GET", data=None, headers=None):
        request = Request(site + path, data=data, method=method, headers=headers or {})
        try:
            response = browser.open(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            assert response.status == status, (path, response.status, status)
            body = response.read()
            return json.loads(body) if response.headers.get_content_type() == "application/json" else body

    def post(path, status, payload, *, browser=admin, **headers):
        return call(path, status, browser=browser, method="POST", data=json.dumps(payload).encode(), headers={
            "Content-Type": "application/json", "Origin": site, **headers,
        })

    def account(username, role, browser):
        password = token_urlsafe(24)
        compose("exec", "-T", "backend", "python", "-c", CREATE_ACCOUNT,
                input=json.dumps([username, role, password]))
        login = post("/api/auth/login", 200, {"username": username, "password": password}, browser=browser,
                     **{"X-EcoKitchen-Login": "1"})
        assert login["user"]["role"] == role
        return {"X-CSRF-Token": login["csrf_token"]}

    assert call("/api/health") == {"status": "ok"}
    home = call("/")
    assert b"EcoKitchen Dashboard" in home
    bundle_path = re.search(rb'src="(/assets/[^"<>]+\.js)"', home)
    assert bundle_path and b"/api/auth/login" in call(bundle_path[1].decode())
    assert b"EcoKitchen" in call("/legacy/")
    call("/api/inventory", 401)
    call("/api/auth/session", 403, headers={"Origin": "https://example.test"})
    benchmark = call("/api/forecasting/retail-benchmark")
    assert benchmark["wape_improvement_pp"] == 8.1863897492
    csrf = account("smoke-admin", "admin", admin)
    readonly = account("smoke-viewer", "viewer", viewer)
    item = {"name": "Smoke-test carrots", "category": "Produce", "unit": "kg", "minimum_stock": "1"}
    post("/api/inventory/ingredients", 403, item)
    ingredient = post("/api/inventory/ingredients", 201, item, **csrf)
    post("/api/inventory", 201, {"ingredient_id": ingredient["id"], "quantity": "2", "expiry_date": None}, **csrf)
    assert call("/api/inventory", browser=viewer)["total"] == 1
    post("/api/inventory/ingredients", 403, item, browser=viewer, **readonly)
    call("/api/vision/media/00000000-0000-0000-0000-000000000000/source.jpg", 403, browser=viewer)

    for route, explanation in (("image", "unavailable in this edition"), ("spoilage", "GEMINI_API_KEY")):
        photo = (b'--ecokitchen\r\nContent-Disposition: form-data; name="image"; filename="photo.jpg"\r\n'
                 b'Content-Type: image/jpeg\r\n\r\n\xff\xd8\xfffixture\r\n--ecokitchen--\r\n')
        unavailable = call("/api/vision/" + route, 503, method="POST", data=photo, headers={
            "Origin": site, "Content-Type": "multipart/form-data; boundary=ecokitchen", **csrf,
        })
        assert explanation in unavailable["error"]

    compose("stop")
    compose("up", "--detach", "--wait", "--wait-timeout", "120")
    assert call("/api/auth/session")["user"]["role"] == "admin"
    assert call("/api/inventory")["total"] == 1
    post("/api/auth/logout", 204, {}, **csrf)
    call("/api/inventory", 401)
    print("Compose HTTP smoke passed: health, sign-in, roles, CSRF, benchmark, optional AI, and restart persistence.")


def main():
    env = {**os.environ, "ECOKITCHEN_PORT": os.getenv("ECOKITCHEN_SMOKE_PORT", "18080"),
           "ECOKITCHEN_DOCKER_NETWORK_PREFIX": os.getenv("ECOKITCHEN_SMOKE_NETWORK_PREFIX", "172.30.97"),
           "ECOKITCHEN_SECRET_KEY": token_hex(32), "GEMINI_API_KEY": "", "ECOKITCHEN_INSTALL_AI": "0"}
    project = "ecokitchen-smoke-" + token_hex(6)

    def compose(*args, check=True, **kwargs):
        return subprocess.run(["docker", "compose", "--file", "compose.yaml", "--project-name", project, *args],
                              cwd=ROOT, env=env, check=check, text=True, **kwargs)

    try:
        compose("up", "--build", "--detach", "--wait", "--wait-timeout", "120")
        verify(compose, "http://127.0.0.1:" + env["ECOKITCHEN_PORT"])
    except Exception:
        compose("logs", "--tail", "40", check=False)
        raise
    finally:
        compose("down", "--volumes", check=False)


if __name__ == "__main__":
    main()
