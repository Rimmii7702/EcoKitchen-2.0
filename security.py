"""Application-wide access policy and Flask session/bootstrap endpoints."""

import os
import re
import sqlite3
from datetime import timedelta
from functools import lru_cache
from ipaddress import ip_address
from secrets import compare_digest, token_hex
from time import time
from urllib.parse import urlsplit

import click
from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for
from flask.cli import with_appcontext
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import RequestEntityTooLarge

from auth_store import AuthStore
from local_api import add_local_cors, is_loopback_origin, local_access_error
from reverse_proxy import install_reverse_proxy
from runtime_config import project_path


auth = Blueprint("auth", __name__)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC = {"index", "health", "auth.login_page", "auth.login", "auth.session_status", "forecasting_api.retail_benchmark"}
ADMIN_READS = {
    "uploads", "inventory_results", "results_image", "show_results", "video_feed", "vision_api.media",
    "inverntory", "waste", "kitchen_heatmap", "live_spoilage_status", "recipe",
}
LEGACY_JSON = {"ingredients", "get_recipe", "upload_image", "upload_video"}
NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{2,63}", re.ASCII)


def https_origin(value):
    try:
        origin = urlsplit(value)
        return bool(origin.scheme == "https" and origin.hostname and origin.port != 0 and not origin.username
                    and not origin.password and not origin.path and not origin.query and not origin.fragment
                    and not any(char.isspace() or char in "*\\<>" for char in value))
    except ValueError:
        return False


def register_security(app):
    mode = os.getenv("ECOKITCHEN_AUTH_MODE", "loopback")
    key = os.getenv("ECOKITCHEN_SECRET_KEY", "").strip()
    hosts = tuple(part.strip() for part in os.getenv("ECOKITCHEN_HTTPS_HOSTS", "").split(",") if part.strip())
    origins = tuple(part.strip() for part in app.config.get("INVENTORY_CORS_ORIGINS", "").split(",") if part.strip())
    if mode not in {"loopback", "https"}:
        raise RuntimeError("ECOKITCHEN_AUTH_MODE must be loopback or https.")
    if mode == "https" and (len(key) < 32 or not hosts or not all(map(https_origin, (*hosts, *origins)))):
        raise RuntimeError("HTTPS requires a strong ECOKITCHEN_SECRET_KEY, HTTPS API hosts and HTTPS-only CORS origins.")
    if mode == "loopback" and not all(map(is_loopback_origin, origins)):
        raise RuntimeError("Loopback mode requires loopback CORS origins.")
    if key and len(key) < 32:
        raise RuntimeError("ECOKITCHEN_SECRET_KEY must be at least 32 characters when provided.")

    proxy = os.getenv("ECOKITCHEN_TRUSTED_PROXY", "").strip()
    proxy_origin = os.getenv("ECOKITCHEN_PROXY_ORIGIN", "").strip()
    if proxy or proxy_origin:
        try:
            proxy = str(ip_address(proxy))
        except ValueError as exc:
            raise RuntimeError("ECOKITCHEN_TRUSTED_PROXY must be one exact IP address.") from exc
        valid_origin = proxy_origin in hosts if mode == "https" else (
            proxy_origin.startswith("http://") and is_loopback_origin(proxy_origin)
        )
        if len(key) < 32 or not valid_origin:
            raise RuntimeError("Proxy mode requires a stable ECOKITCHEN_SECRET_KEY and a configured proxy origin.")
        install_reverse_proxy(app, proxy, proxy_origin)

    store = project_path(app.config.get("AUTH_DB_PATH") or os.getenv("ECOKITCHEN_AUTH_DB") or "instance/auth.sqlite3")
    inventory = project_path(app.config.get("INVENTORY_DB_PATH", "instance/inventory.sqlite3"))
    public_paths = [project_path(path) for path in (app.static_folder, "uploads", "dashboard/dist") if path]
    if store == inventory or any(store.is_relative_to(path) for path in public_paths):
        raise RuntimeError("The authentication database must be separate from inventory and outside served directories.")
    app.extensions["auth_store"] = AuthStore(store)
    app.config.update(
        SECRET_KEY=key or token_hex(32), AUTH_HTTPS=mode == "https", AUTH_HTTPS_HOSTS=hosts,
        AUTH_PROXY_ORIGIN=proxy_origin,
        SESSION_COOKIE_NAME="ecokitchen_session", SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=mode == "https", SESSION_COOKIE_DOMAIN=None,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8), SESSION_REFRESH_EACH_REQUEST=False,
        MAX_CONTENT_LENGTH=1024**2, MAX_FORM_PARTS=8, AUTH_LOGIN_LIMIT=8, AUTH_LOGIN_WINDOW=300,
    )
    app.register_blueprint(auth)
    app.before_request(authorize)
    app.after_request(security_headers)
    app.errorhandler(sqlite3.Error)(unavailable_store)
    app.cli.add_command(create_user)
    app.context_processor(lambda: {"csrf_token": lambda: current_identity()["csrf"] if current_identity() else ""})


def unavailable_store(_error):
    return jsonify(error="Authentication storage is unavailable. Check local setup."), 503


@auth.errorhandler(RequestEntityTooLarge)
def too_large(_error):
    return jsonify(error="The request exceeds the permitted upload or form size."), 413


def store():
    return current_app.extensions["auth_store"]


def current_identity():
    if "identity" not in g:
        token = session.get("sid")
        g.identity = store().session(token, int(time())) if token else None
        if token and g.identity is None:
            session.clear()
    return g.identity


def public_static():
    filename = (request.view_args or {}).get("filename", "")
    parts = filename.split("/")
    return (len(parts) == 2 and parts[0] in {"css", "js"} and parts[1].endswith("." + parts[0])
            and re.fullmatch(r"[a-zA-Z0-9_.-]+", parts[1]))


def authorize():
    if error := local_access_error():
        return jsonify(error=error), 403
    endpoint = request.endpoint
    if endpoint is None or request.method == "OPTIONS" or endpoint in PUBLIC or (endpoint == "static" and public_static()):
        return None
    identity = current_identity()
    if identity is None:
        if request.method in {"GET", "HEAD"} and not request.path.startswith("/api/") and endpoint not in LEGACY_JSON:
            return redirect(url_for("auth.login_page", next=request.path))
        return jsonify(error="Sign in to EcoKitchen."), 401
    unsafe = request.method not in SAFE_METHODS
    if (endpoint in ADMIN_READS or endpoint == "static" or (unsafe and endpoint != "auth.logout")) and identity["role"] != "admin":
        return jsonify(error="This action requires an administrator."), 403
    if unsafe:
        token = request.headers.get("X-CSRF-Token")
        if token is None and endpoint in {"waste", "kitchen_heatmap"}:
            from vision_media import IMAGE_LIMIT
            request.max_content_length = IMAGE_LIMIT + 64 * 1024
            request.max_form_parts = 4
            token = request.form.get("csrf_token")
        if not token or not compare_digest(token.encode("utf-8"), identity["csrf"].encode("utf-8")):
            return jsonify(error="Invalid CSRF token. Refresh the page and try again."), 403
    return None


def security_headers(response):
    if request.path.startswith("/api/"):
        add_local_cors(response)
    if request.endpoint != "static" or not public_static():
        response.headers["Cache-Control"] = "private, no-store"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if current_app.config["AUTH_HTTPS"] and request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=86400")
    return response


@auth.get("/login")
def login_page():
    path = request.args.get("next", "/")
    if not path.startswith("/") or path.startswith("//") or "\\" in path or any(ord(c) < 32 for c in path):
        path = "/"
    if current_identity():
        return redirect(path)
    return render_template("login.html", next_path=path)


def identity_response(identity):
    return jsonify(user={"username": identity["username"], "role": identity["role"]} if identity else None,
                   csrf_token=identity["csrf"] if identity else None)


@auth.get("/api/auth/session")
def session_status():
    return identity_response(current_identity())


@lru_cache(maxsize=1)
def dummy_hash():
    return generate_password_hash(token_hex(32))


@auth.post("/api/auth/login")
def login():
    request.max_content_length = 4096
    if not request.is_json or request.headers.get("X-EcoKitchen-Login") != "1":
        return jsonify(error="Send a JSON login request with the login header."), 415
    payload = request.get_json(silent=True)
    username = payload.get("username") if isinstance(payload, dict) else None
    password = payload.get("password") if isinstance(payload, dict) else None
    now = int(time())
    retry = store().attempt(request.remote_addr or "unknown", now, current_app.config["AUTH_LOGIN_WINDOW"],
                            current_app.config["AUTH_LOGIN_LIMIT"])
    if retry:
        return jsonify(error="Too many sign-in attempts. Try again later."), 429, {"Retry-After": str(retry)}
    username = username.strip().lower() if isinstance(username, str) else ""
    user = store().user(username) if NAME.fullmatch(username) else None
    fallback = dummy_hash()
    valid = False
    if isinstance(password, str) and len(password) <= 256:
        try:
            valid = check_password_hash(user["password_hash"] if user else fallback, password)
        except UnicodeError:
            pass
    if not valid or user is None:
        return jsonify(error="Invalid username or password."), 401
    lifetime = int(current_app.permanent_session_lifetime.total_seconds())
    token, csrf = store().issue(user["id"], now, lifetime, session.get("sid"))
    session.clear()
    session.permanent = True
    session["sid"] = token
    return identity_response({"username": user["username"], "role": user["role"], "csrf": csrf})


@auth.post("/api/auth/logout")
def logout():
    store().revoke(session["sid"])
    session.clear()
    return "", 204


@click.command("create-user")
@click.argument("username")
@click.option("--role", type=click.Choice(("admin", "viewer")), default="admin", show_default=True)
@with_appcontext
def create_user(username, role):
    """Create a local account with an interactively entered password."""
    username = username.strip().lower()
    if not NAME.fullmatch(username):
        raise click.ClickException("Use 3–64 letters, digits, dots, hyphens or underscores.")
    password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    if not 12 <= len(password) <= 256:
        raise click.ClickException("Use a password between 12 and 256 characters.")
    try:
        store().create_user(username, generate_password_hash(password), role)
    except sqlite3.IntegrityError as error:
        raise click.ClickException("That username already exists.") from error
    except sqlite3.Error as error:
        raise click.ClickException("Could not open authentication storage.") from error
    click.echo(f"Created {role} account {username}.")
