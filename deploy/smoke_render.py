"""Build and verify the Render demo in a resource-constrained Linux container."""

import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import time
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path
from secrets import token_hex
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
STARTUP_TIMEOUT = 120
HOST = "ecokitchen-smoke.onrender.com"
CONTAINER_PORT = 10031
MEMORY_LIMIT = 512 * 1024**2
SCORECARD = "forecasting/results/freshretailnet-holdout-v1.json"


def docker(*args, capture=False, check=True, env=None):
    result = subprocess.run(["docker", *args], cwd=ROOT, check=check, stdout=subprocess.PIPE if capture else None,
                            text=True, env=env)
    return result.stdout.strip() if capture else None


def call(site, path, status=200, *, method="GET", headers=None):
    request = Request(site + path, method=method, headers={"Host": HOST, **(headers or {})})
    try:
        result = build_opener(ProxyHandler({})).open(request, timeout=10)
    except HTTPError as error:
        result = error
    with result:
        assert result.status == status, (path, result.status, status)
        body = result.read()
        if result.headers.get_content_type() == "application/json" and body:
            return json.loads(body)
        return body


def published_site(name):
    endpoint = docker("port", name, f"{CONTAINER_PORT}/tcp", capture=True)
    if not re.fullmatch(r"127\.0\.0\.1:\d+", endpoint):
        raise RuntimeError(f"Docker did not publish the expected loopback listener: {endpoint!r}")
    return "http://" + endpoint


def probe_health(site, timeout):
    target = urlsplit(site)
    with closing(HTTPConnection(target.hostname, target.port, timeout=timeout)) as connection:
        connection.request("GET", "/api/health", headers={"Host": HOST})
        response = connection.getresponse()
        if response.status in {502, 503}:
            return f"HTTP {response.status}"
        if response.status != 200:
            raise RuntimeError(f"Unexpected HTTP {response.status} from /api/health.")
        if response.headers.get_content_type() != "application/json":
            raise RuntimeError("Expected JSON from /api/health.")
        if json.loads(response.read(4096)) != {"status": "ok"}:
            raise RuntimeError("Unexpected JSON payload from /api/health.")
        return None


def wait_ready(name, site, started):
    deadline = started + STARTUP_TIMEOUT
    last_issue = "no response"
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            issue = probe_health(site, min(2, remaining))
        except (ConnectionRefusedError, ConnectionResetError, TimeoutError) as error:
            issue = type(error).__name__
        if issue is None:
            return time.monotonic() - started
        last_issue = issue
        state = docker("inspect", "--format", "{{.State.Status}}", name, capture=True)
        if state != "running":
            raise RuntimeError(f"Container stopped during startup ({state}; last probe: {last_issue}).")
        time.sleep(min(0.3, max(0, deadline - time.monotonic())))
    raise RuntimeError(f"/api/health at {site} failed to become ready within {STARTUP_TIMEOUT}s ({last_issue}).")


def check_http(site):
    html = call(site, "/")
    assert b"EcoKitchen Dashboard" in html
    bundle = re.search(rb'src="(/assets/[^"<>]+\.js)"', html)
    stylesheet = re.search(rb'href="(/assets/[^"<>]+\.css)"', html)
    assert bundle and b"Read-only public demo" in call(site, bundle[1].decode())
    assert stylesheet and call(site, stylesheet[1].decode())
    assert b"SIL OPEN FONT LICENSE" in call(site, "/notices/dm-sans.txt").upper()

    viewer = call(site, "/api/auth/session")
    assert viewer == {"user": {"username": "demo-viewer", "role": "viewer"}, "public_demo": True, "csrf_token": None}
    assert call(site, "/api/inventory")["total"] == 10
    assert call(site, "/api/inventory?q=chickpeas")["total"] == 3
    assert call(site, "/api/inventory/ingredients")["total"] == 6
    assert call(site, "/api/inventory/history")["total"] == 11
    alerts = call(site, "/api/inventory/alerts")
    assert alerts["near_expiry"] and alerts["low_stock"]
    recommendations = call(site, "/api/recommendations")
    assert recommendations["near_expiry"] and recommendations["low_stock"]
    assert call(site, "/api/ai/menu")["saved_snapshot"]["fictional_demo"]
    benchmark = call(site, "/api/forecasting/retail-benchmark")
    assert benchmark["evaluation"]["rows"] == 350_000
    assert benchmark["models"]["seasonal_naive"]["metrics"]["wape_pct"] == 41.8314054769
    assert benchmark["models"]["lightgbm"]["metrics"]["wape_pct"] == 33.6450157277
    assert benchmark["wape_improvement_pp"] == 8.1863897492

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        for path in ("/api/inventory", "/api/ai/menu", "/api/auth/login", "/api/vision/image", "/"):
            call(site, path, 405, method=method)
    for path in ("/legacy/", "/login", "/uploads/private.jpg", "/api/vision/media/job/source.jpg",
                 "/api/ai/recipe-ingredients", "/api/admin", "/.env"):
        call(site, path, 404)
    call(site, "/api/inventory", 403, headers={"Host": "wrong.test"})
    call(site, "/api/inventory", 403, headers={"Origin": "https://wrong.test"})
    call(site, "/api/inventory", headers={
        "Origin": "https://" + HOST, "X-Forwarded-Proto": "http", "X-Forwarded-For": "bad",
        "X-Forwarded-Host": "wrong.test", "Forwarded": "proto=http;host=wrong.test",
    })


def verify_limits(name):
    assert docker("exec", name, "cat", "/sys/fs/cgroup/memory.max", capture=True) == str(MEMORY_LIMIT)
    quota, period = map(int, docker("exec", name, "cat", "/sys/fs/cgroup/cpu.max", capture=True).split())
    assert quota * 10 == period, (quota, period)


def peak(name):
    value = int(docker("exec", name, "cat", "/sys/fs/cgroup/memory.peak", capture=True))
    assert 0 < value < MEMORY_LIMIT, value
    assert docker("inspect", "--format", "{{.State.OOMKilled}}", name, capture=True) == "false"
    return value


def main():
    if docker("info", "--format", "{{.CgroupVersion}}", capture=True) != "2":
        raise RuntimeError("The peak measurement requires a Docker host with cgroup v2.")
    name = "ecokitchen-render-smoke-" + token_hex(6)
    env = {**os.environ, "ECOKITCHEN_SECRET_KEY": token_hex(32)}
    docker("build", "--file", "deploy/Dockerfile.render", "--tag", name, ".")
    try:
        started = time.monotonic()
        docker("run", "--detach", "--name", name, "--cpus", "0.1",
               "--memory", "512m", "--memory-swap", "512m",
               "--publish", f"127.0.0.1::{CONTAINER_PORT}", "--env", "ECOKITCHEN_SECRET_KEY",
               "--env", "ECOKITCHEN_PUBLIC_DEMO=1", "--env", "RENDER_EXTERNAL_URL=https://" + HOST,
               "--env", f"PORT={CONTAINER_PORT}", name, env=env)
        site = published_site(name)
        startup = wait_ready(name, site, started)
        verify_limits(name)
        check_http(site)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda path: call(site, path),
                          ["/api/inventory", "/api/recommendations", "/api/forecasting/retail-benchmark"] * 12))
        first_peak = peak(name)

        # Change only this disposable instance from inside the smoke harness, then restart.
        change_fixture = (
            "import glob, sqlite3; matches=glob.glob('/tmp/ecokitchen-render-*/inventory.sqlite3'); "
            "assert len(matches) == 1; db=sqlite3.connect(matches[0]); "
            "db.execute(\"UPDATE ingredients SET name='Restart sentinel' WHERE id=1\"); db.commit(); db.close()"
        )
        docker("exec", name, "python", "-c", change_fixture)
        assert call(site, "/api/inventory/ingredients?q=Restart%20sentinel")["total"] == 1
        restarting = time.monotonic()
        docker("restart", name)
        new_site = published_site(name)
        print(f"Docker loopback listener before restart: {site}; after restart: {new_site}.", flush=True)
        site = new_site
        restart = wait_ready(name, site, restarting)
        verify_limits(name)
        assert call(site, "/api/inventory/ingredients?q=Restart%20sentinel")["total"] == 0
        check_http(site)
        second_peak = peak(name)
        expected_hash = hashlib.sha256((ROOT / SCORECARD).read_bytes()).hexdigest()
        assert docker("exec", name, "sha256sum", SCORECARD, capture=True).split()[0] == expected_hash
        image = int(docker("image", "inspect", "--format", "{{.Size}}", name, capture=True))
        print(f"Render smoke passed at 0.1 CPU / 512 MiB. Image size: {image:,} bytes "
              f"({image / 1024**2:.1f} MiB). Peak cgroup memory: {max(first_peak, second_peak):,} bytes "
              f"({max(first_peak, second_peak) / 1024**2:.1f} MiB). "
              f"Startup: {startup:.2f}s, restart to ready: {restart:.2f}s.")
    except Exception:
        docker("logs", "--tail", "50", name, check=False)
        raise
    finally:
        docker("rm", "--force", name, check=False)
        docker("image", "rm", name, check=False)


if __name__ == "__main__":
    main()
