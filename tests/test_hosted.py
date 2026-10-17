import io
import json
import secrets
import time
import unittest
from unittest.mock import patch

from vercel_app import HostedRequest as handler, app
from core import AppError
from hosted_state import decode_state, encode_state, MAX_STATE_BYTES
from server import new_state


class HostedStateTests(unittest.TestCase):
    def setUp(self):
        self.key = secrets.token_urlsafe(32)
        self.state = new_state()
        self.state["pages"] = [{"page": 2, "text": "महत्वपूर्ण निर्देश: सुरक्षित रहें।"}]
        self.state["job"] = "document-job"

    def test_round_trip_without_audio_or_credentials(self):
        self.state["audio"]["question-0"] = "large-audio-data"
        token = encode_state(self.state, self.key)
        restored = decode_state(token, self.key)
        self.assertEqual(restored["pages"], self.state["pages"])
        self.assertEqual(restored["job"], "document-job")
        self.assertEqual(restored["audio"], {})
        self.assertNotIn(self.key, token)

    def test_tampering_wrong_key_and_expiry_are_rejected(self):
        token = encode_state(self.state, self.key)
        for value, key in (("a" + token[1:], self.key), (token, "different-key"), (None, self.key)):
            with self.assertRaises(AppError):
                decode_state(value, key)
        self.state["created"] = time.time() - 3700
        with self.assertRaises(AppError):
            decode_state(encode_state(self.state, self.key), self.key)

    def test_size_bounds(self):
        self.state["pages"] = [{"page": 1, "text": "a" * (MAX_STATE_BYTES + 1)}]
        with self.assertRaises(AppError):
            encode_state(self.state, self.key)
        with self.assertRaises(AppError):
            decode_state("a" * 550000, self.key)

    def test_separate_handler_instances_resume_progress(self):
        first = object.__new__(handler)
        second = object.__new__(handler)
        with patch.dict("os.environ", {"SARVAM_API_KEY": self.key}):
            _, state = first.session()
            state["filename"] = "Training.pdf"
            token = encode_state(state, self.key)
            second.request_state_token = token
            _, resumed = second.session()
            self.assertEqual(resumed["filename"], "Training.pdf")
            self.assertIsNot(resumed, state)

    def test_https_origin_and_host_rules(self):
        request = object.__new__(handler)
        request.public_origin = "https://sarvam-seven.vercel.app"
        self.assertTrue(request.valid_host())
        self.assertEqual(request.expected_origin(), "https://sarvam-seven.vercel.app")

    def test_runtime_exports_never_select_local_handler(self):
        import api.index as entry
        self.assertIs(entry.app, app)
        self.assertNotIn("Handler", vars(entry))
        self.assertNotIn("handler", vars(entry))

    def test_vercel_bridge_internal_host_and_forwarded_public_host(self):
        status, headers, body = invoke("/", host="127.0.0.1:37841",
                                      headers={"X-Forwarded-Host": "sarvam-seven.vercel.app"})
        self.assertEqual(status, "200 OK")
        self.assertIn(b"Beyond", body)

    def test_vercel_bridge_without_forwarded_host(self):
        for host in ("localhost:39172", "0.0.0.0:8000", "sarvam-seven.vercel.app"):
            self.assertEqual(invoke("/", host=host)[0], "200 OK")

    def test_hosted_response_includes_signed_state(self):
        request = object.__new__(handler)
        request.request_version = "HTTP/1.1"
        request.requestline = "POST /api/poll HTTP/1.1"
        request.wfile = io.BytesIO()
        request.active_state = self.state
        with patch.dict("os.environ", {"SARVAM_API_KEY": self.key}):
            request.send({"status": "ready"})
        raw = request.wfile.getvalue()
        data = json.loads(raw.split(b"\r\n\r\n", 1)[1])
        self.assertEqual(decode_state(data["_state"], self.key)["job"], "document-job")
        self.assertNotIn(self.key.encode(), raw)
        self.assertEqual(request.max_pdf_bytes, 3_000_000)


def invoke(path, method="GET", data=None, host="sarvam-seven.vercel.app", headers=None):
    body = json.dumps(data).encode() if data is not None else b""
    environ = {"PATH_INFO": path, "REQUEST_METHOD": method, "HTTP_HOST": host,
               "wsgi.url_scheme": "https", "wsgi.input": io.BytesIO(body),
               "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": "application/json"}
    for key, value in (headers or {}).items():
        environ["HTTP_" + key.upper().replace("-", "_")] = value
    captured = {}
    def start_response(status, response_headers):
        captured.update(status=status, headers=dict(response_headers))
    result = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], result


class WsgiRouteTests(unittest.TestCase):
    def test_home_assets_sample_and_health(self):
        for path, content_type in (("/", "text/html"), ("/app.js", "text/javascript"),
                                   ("/style.css", "text/css"), ("/sample.pdf", "application/pdf"),
                                   ("/api/health", "application/json")):
            status, headers, body = invoke(path)
            self.assertEqual(status, "200 OK", path)
            self.assertTrue(headers["Content-Type"].startswith(content_type))
            self.assertEqual(headers["X-Samjha-Runtime"], "vercel-wsgi-v1")
            self.assertNotIn(b'"Invalid host."', body)

    def test_signed_session_roundtrip_through_wsgi(self):
        with patch.dict("os.environ", {"SARVAM_API_KEY": secrets.token_urlsafe(32)}):
            status, _, body = invoke("/api/session")
            self.assertEqual(status, "200 OK")
            state = json.loads(body)
            status, _, body = invoke("/api/session", "POST", {"_state": state["_state"]},
                headers={"Origin": "https://sarvam-seven.vercel.app", "X-SamjhaProof": "1"})
            self.assertEqual(status, "200 OK")
            self.assertTrue(json.loads(body)["configured"])

    def test_cross_origin_write_remains_blocked(self):
        status, _, body = invoke("/api/reset", "POST", {},
            headers={"Origin": "https://attacker.example", "X-SamjhaProof": "1"})
        self.assertEqual(status, "403 Forbidden")
        self.assertEqual(json.loads(body)["error"], "Invalid origin.")

    def test_secrets_and_unknown_routes_not_served(self):
        self.assertEqual(invoke("/.env")[0], "404 Not Found")
        self.assertEqual(invoke("/api/unknown")[0], "404 Not Found")

    def test_head_and_unsupported_methods(self):
        status, _, body = invoke("/", "HEAD")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, b"")
        self.assertEqual(invoke("/", "DELETE")[0], "405 Method Not Allowed")


if __name__ == "__main__":
    unittest.main()
