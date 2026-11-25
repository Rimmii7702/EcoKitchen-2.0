"""Accept a single forwarding hop from an explicitly configured reverse proxy."""

from ipaddress import ip_address


PROBE_HOST = "127.0.0.1:8000"
HEALTH_PATH = "/api/health"
PROXY_MARKER = "ecokitchen.trusted_proxy"


def install_reverse_proxy(app, address, origin):
    upstream = app.wsgi_app
    scheme, host = origin.split("://", 1)

    def dispatch(environ, start_response):
        if environ.get("REMOTE_ADDR") == address and environ.get("HTTP_HOST") == host:
            if environ.get("HTTP_X_FORWARDED_PROTO") == scheme:
                try:
                    client = ip_address(environ.get("HTTP_X_FORWARDED_FOR", ""))
                except ValueError:
                    pass
                else:
                    environ[PROXY_MARKER] = True
                    environ["REMOTE_ADDR"] = str(client)
                    environ["wsgi.url_scheme"] = scheme
        return upstream(environ, start_response)

    app.wsgi_app = dispatch


def is_direct_probe(request):
    return (
        request.path == HEALTH_PATH and request.method in {"GET", "HEAD"}
        and request.remote_addr == "127.0.0.1" and request.host == PROBE_HOST
        and request.headers.get("Origin") is None
    )
