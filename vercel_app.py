"""WSGI adapter for Vercel; no loopback HTTP bridge or listening socket."""

import io
import os
import threading
from email.message import Message
from http import HTTPStatus

from core import AppError
from hosted_state import decode_state, encode_state
import server as local_server


class HostedRequest(local_server.Handler):
    max_body = 4_400_000
    max_pdf_bytes = 3_000_000

    def valid_host(self):
        # Vercel's ingress selects the deployment/domain. The localhost-only
        # check belongs to the local listener, not a managed WSGI invocation.
        return True

    def expected_origin(self):
        return self.public_origin

    def session(self):
        token = getattr(self, "request_state_token", None)
        key = os.environ.get("SARVAM_API_KEY", "")
        self.active_state = decode_state(token, key) if token else local_server.new_state()
        return threading.Lock(), self.active_state

    def send(self, data, status=200, content_type="application/json"):
        state = getattr(self, "active_state", None)
        key = os.environ.get("SARVAM_API_KEY", "")
        if content_type == "application/json" and isinstance(data, dict) and state is not None and key:
            try:
                data = data | {"_state": encode_state(state, key)}
            except AppError as e:
                data, status = {"error": str(e)}, 400
        return super().send(data, status, content_type)

    def do_GET(self):
        if self.path == "/api/health":
            return self.send({"status": "ok", "runtime": "vercel-wsgi-v1",
                              "configured": bool(os.environ.get("SARVAM_API_KEY"))})
        return super().do_GET()


def app(environ, start_response):
    """Vercel calls this WSGI callable directly for each incoming request."""
    request = object.__new__(HostedRequest)
    request.path = environ.get("PATH_INFO", "/") or "/"
    request.headers = Message()
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            request.headers[key[5:].replace("_", "-")] = str(value)
    for name in ("CONTENT_LENGTH", "CONTENT_TYPE"):
        if environ.get(name):
            request.headers[name.replace("_", "-")] = str(environ[name])
    host = (request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or
            environ.get("SERVER_NAME", "localhost")).split(",", 1)[0].strip()
    scheme = (request.headers.get("X-Forwarded-Proto") or environ.get("wsgi.url_scheme", "https")).split(",", 1)[0].strip()
    request.public_origin = f"{scheme}://{host}"
    request.rfile = environ.get("wsgi.input", io.BytesIO())
    request.wfile = io.BytesIO()
    response = {"status": 200, "headers": [("X-Samjha-Runtime", "vercel-wsgi-v1")]}
    request.send_response = lambda code, message=None: response.update(status=code)
    request.send_header = lambda key, value: response["headers"].append((key, str(value)))
    request.end_headers = lambda: None
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method in ("GET", "HEAD"):
        request.do_GET()
    elif method == "POST":
        request.do_POST()
    else:
        request.send({"error": "Method not allowed."}, 405)
    status = response["status"]
    start_response(f"{status} {HTTPStatus(status).phrase}", response["headers"])
    return [] if method == "HEAD" else [request.wfile.getvalue()]
