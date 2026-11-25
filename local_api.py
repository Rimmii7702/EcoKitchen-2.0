"""Loopback access and CORS for the local dashboard APIs."""

from ipaddress import ip_address
from urllib.parse import urlsplit

from flask import abort, current_app, request

from reverse_proxy import PROXY_MARKER, is_direct_probe

DEFAULT_CORS_ORIGINS = ",".join(
    f"http://{host}:{port}"
    for host in ("127.0.0.1", "localhost", "[::1]") for port in (5173, 4173)
)
WRITE_METHODS = {"POST", "PATCH", "DELETE"}

def is_loopback_ip(value):
    try:
        return ip_address(value).is_loopback
    except ValueError:
        return False


def is_loopback_origin(value):
    try:
        origin = urlsplit(value)
        hostname = origin.hostname
        if origin.scheme not in {"http", "https"} or origin.path or origin.query or origin.fragment:
            return False
        if not hostname or (hostname != "localhost" and not is_loopback_ip(hostname)):
            return False
        host = f"[{hostname}]" if ":" in hostname else hostname
        if origin.port is not None:
            host += f":{origin.port}"
        return origin.netloc.lower() == host
    except (TypeError, ValueError):
        return False


def allowed_origin(origin):
    configured = current_app.config["INVENTORY_CORS_ORIGINS"].split(",")
    return origin in {value.strip() for value in configured} and (
        current_app.config.get("AUTH_HTTPS") or is_loopback_origin(origin)
    )


def local_access_error():
    origin = request.headers.get("Origin")
    server = request.host_url.rstrip("/")
    if proxy_origin := current_app.config.get("AUTH_PROXY_ORIGIN"):
        if not is_direct_probe(request) and (not request.environ.get(PROXY_MARKER) or server != proxy_origin):
            return "This request must use the configured reverse proxy and origin."
    elif current_app.config.get("AUTH_HTTPS"):
        if not request.is_secure or server not in current_app.config["AUTH_HTTPS_HOSTS"]:
            return "This request requires a configured HTTPS origin."
    elif not is_loopback_ip(request.remote_addr) or not is_loopback_origin(server):
        return "This feature is only available on the local machine."
    if origin is not None and origin != server and not allowed_origin(origin):
        return "This browser origin cannot access the local API."
    return None


def require_local_request():
    if error := local_access_error():
        abort(403, description=error)


def add_local_cors(response):
    response.vary.add("Origin")
    origin = request.headers.get("Origin")
    if allowed_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        if request.method == "OPTIONS":
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token, X-EcoKitchen-Login"
    return response
