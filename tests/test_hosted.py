import io
import json
import secrets
import time
import unittest
from unittest.mock import patch

from api.index import handler
from core import AppError
from hosted_state import decode_state, encode_state
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
        self.state["pages"] = [{"page": 1, "text": "a" * 410000}]
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
        request.headers = {"Host": "samjhaproof-example.vercel.app"}
        self.assertTrue(request.valid_host())
        self.assertEqual(request.expected_origin(), "https://samjhaproof-example.vercel.app")
        request.headers = {"Host": "attacker.example"}
        self.assertFalse(request.valid_host())

    def test_runtime_exports_never_select_local_handler(self):
        from api.index import Handler
        self.assertIs(Handler, handler)

    def test_vercel_bridge_internal_host_and_forwarded_public_host(self):
        request = object.__new__(handler)
        request.headers = {"Host": "127.0.0.1:37841", "X-Forwarded-Host": "sarvam-jh22cz6ye-sethidkms-8397.vercel.app",
                           "Origin": "https://sarvam-jh22cz6ye-sethidkms-8397.vercel.app"}
        self.assertTrue(request.valid_host())
        self.assertEqual(request.expected_origin(), request.headers["Origin"])

    def test_vercel_bridge_without_forwarded_host(self):
        request = object.__new__(handler)
        public = "sarvam-jh22cz6ye-sethidkms-8397.vercel.app"
        request.headers = {"Host": "localhost:39172", "Origin": f"https://{public}"}
        with patch.dict("os.environ", {"VERCEL_URL": public}):
            self.assertTrue(request.valid_host())
            self.assertEqual(request.expected_origin(), f"https://{public}")
            request.headers["Origin"] = "https://attacker.example"
            self.assertNotEqual(request.expected_origin(), request.headers["Origin"])

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


if __name__ == "__main__":
    unittest.main()
