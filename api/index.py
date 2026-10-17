"""Vercel's Python entrypoint. Local development continues to use server.py."""

import os
import re
import threading

from hosted_state import decode_state, encode_state
from server import Handler, new_state
from core import AppError


class handler(Handler):
    max_body = 4_400_000
    max_pdf_bytes = 3_000_000

    def valid_host(self):
        host = self.headers.get("Host", "").lower()
        allowed = {os.environ.get("VERCEL_URL", "").lower(),
                   os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "").lower()}
        if host and host in allowed:
            return True
        # Supports Vercel's generated preview/branch URLs and `vercel dev`.
        return bool(re.fullmatch(r"[a-z0-9-]+\.vercel\.app", host)) or host in ("localhost:3000", "127.0.0.1:3000")

    def expected_origin(self):
        host = self.headers.get("Host", "")
        scheme = "http" if host.startswith(("localhost:", "127.0.0.1:")) else "https"
        return f"{scheme}://{host}"

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
