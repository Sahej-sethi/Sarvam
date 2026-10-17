"""Signed, bounded session snapshots for serverless requests; no database required."""

import base64
import hashlib
import hmac
import json
import time
import zlib

from core import AppError
from server import EXPIRY, new_state

MAX_STATE_BYTES = 400_000
MAX_TOKEN_CHARS = 540_000


def signing_key(api_key):
    return hmac.digest(api_key.encode(), b"samjhaproof:session:v1", "sha256")


def encode_state(state, api_key):
    payload = {k: v for k, v in state.items() if k != "audio"}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > MAX_STATE_BYTES:
        raise AppError("This session is too large. Download your report and start a shorter document.")
    packed = base64.urlsafe_b64encode(zlib.compress(raw)).decode().rstrip("=")
    signature = hmac.new(signing_key(api_key), packed.encode(), hashlib.sha256).hexdigest()
    return packed + "." + signature


def decode_state(token, api_key):
    try:
        if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS:
            raise ValueError()
        packed, signature = token.rsplit(".", 1)
        expected = hmac.new(signing_key(api_key), packed.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        compressed = base64.urlsafe_b64decode(packed + "=" * (-len(packed) % 4))
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(compressed, MAX_STATE_BYTES + 1)
        if len(raw) > MAX_STATE_BYTES or not decompressor.eof or decompressor.unused_data:
            raise ValueError()
        data = json.loads(raw)
        created = data["created"]
        if not isinstance(data, dict) or not isinstance(created, (int, float)) or not 0 <= time.time() - created < EXPIRY:
            raise ValueError()
        state = new_state()
        state.update(data)
        state["audio"] = {}
        return state
    except (ValueError, TypeError, KeyError, zlib.error):
        raise AppError("This session expired or could not be verified. Refresh the page to start again.") from None
