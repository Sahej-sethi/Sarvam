"""Vercel's Python entrypoint. Local development continues to use server.py."""

import os
import re
import threading
from urllib.parse import urlsplit

from hosted_state import decode_state, encode_state
from server import Handler as _LocalHandler, new_state
from core import AppError


class handler(_LocalHandler):
    max_body = 4_400_000
    max_pdf_bytes = 3_000_000

    def configured_hosts(self):
        return {value.lower().rstrip(".") for value in (
            os.environ.get("VERCEL_URL", ""),
            os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "")) if value}

    def public_host(self, host):
        host = host.lower().removesuffix(":443").rstrip(".")
        return bool(host and (host in self.configured_hosts() or
                    re.fullmatch(r"[a-z0-9-]+\.vercel\.app", host)))

    def valid_host(self):
        host = self.headers.get("Host", "").lower()
        forwarded = self.headers.get("X-Forwarded-Host", "").split(",", 1)[0].strip()
        # Vercel's Python bridge can use a loopback Host on a dynamically assigned
        # port. It is not the browser's public origin and must not be compared to it.
        internal = bool(re.fullmatch(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?", host))
        return self.public_host(host) or internal or self.public_host(forwarded)

    def expected_origin(self):
        forwarded = self.headers.get("X-Forwarded-Host", "").split(",", 1)[0].strip()
        host = forwarded if self.public_host(forwarded) else self.headers.get("Host", "")
        if self.public_host(host):
            return f"https://{host}"
        # Some bridge versions preserve Origin but replace Host without forwarding it.
        origin = self.headers.get("Origin", "")
        parsed = urlsplit(origin)
        if parsed.scheme == "https" and parsed.netloc in self.configured_hosts():
            return f"https://{parsed.netloc}"
        return f"http://{host}"

    def session(self):
        token = getattr(self, "request_state_token", None)
        key = os.environ.get("SARVAM_API_KEY", "")
        self.active_state = decode_state(token, key) if token else new_state()
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


# Both entrypoint spellings must resolve to the hosted handler, never its local base.
Handler = handler
