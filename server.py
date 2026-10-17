"""Local-only SamjhaProof server. Run: .venv/bin/python server.py"""

import base64
import errno
import io
import json
import os
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from pydantic import ValidationError
from sarvamai import SarvamAI
from sarvamai.core.api_error import ApiError

from core import AppError, check_recording, evaluate, extract_pages, prepare, speak, create_outline, prepare_section
from documents import DocumentAPI

ROOT = Path(__file__).resolve().parent
LANGUAGES = {"English": "en-IN", "Hindi": "hi-IN"}
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()
MAX_BODY = 28 * 1024 * 1024
EXPIRY = 3600
MAX_PDF_BYTES = 20 * 1024 * 1024


def provider_error(error, stage):
    """Expose actionable provider messages, never headers or raw response payloads."""
    body = error.body
    if isinstance(body, dict):
        nested = body.get("error", body)
        if isinstance(nested, dict):
            detail = nested.get("message") or nested.get("detail")
        else:
            detail = nested if isinstance(nested, str) else body.get("message")
        if not detail:
            detail = body.get("message") or body.get("detail")
    else:
        detail = None
    if not isinstance(detail, str):
        detail = {400: "The provider rejected a request parameter or document. Check the file's page count and API access.",
                  401: "The API key was rejected.", 402: "This API requires additional credits.",
                  403: "This key does not have access to the requested API.",
                  429: "Rate limit reached. Wait a moment and retry."}.get(error.status_code, "The provider could not complete this operation.")
    key = os.environ.get("SARVAM_API_KEY")
    if key:
        detail = detail.replace(key, "[redacted]")
    detail = re.sub(r"sk_[A-Za-z0-9_.-]+", "[redacted]", detail)
    detail = re.sub(r"(?i)(bearer\s+|api[-_]subscription[-_]key[\s:=]+)\S+", r"\1[redacted]", detail)
    detail = " ".join(detail.split())[:600]
    return f"{stage} failed (Sarvam HTTP {error.status_code}): {detail}"


def load_environment():
    """Load only the named key into the process; never execute .env as shell code."""
    if os.environ.get("SARVAM_API_KEY"):
        return
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text().splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "SARVAM_API_KEY":
                os.environ["SARVAM_API_KEY"] = value.strip().strip("\"'")
                break


def new_state():
    return {"pages": [], "lesson": None, "answers": {}, "language": "English",
            "document_language": "English", "filename": "", "job": None,
            "partial": False, "audio": {}, "mode": "quick", "outline": None, "created": time.time()}


def public(state):
    lesson = state["lesson"]
    if lesson:
        lesson = {**lesson, "questions": [
            {k: v for k, v in q.items() if k != "criteria"} for q in lesson["questions"]]}
    return {k: v for k, v in state.items() if k in
            ("pages", "answers", "language", "document_language", "filename", "job", "partial", "mode", "outline")} | {"lesson": lesson}


def sample_pdf():
    lines = ["Workshop Safety: Three Things to Remember", "",
             "1. Wear safety goggles before entering the workshop.",
             "Keep them on while using any tools.", "",
             "2. If a machine jams, turn it off and unplug it.",
             "Ask the supervisor for help. Never reach into a running machine.", "",
             "3. If the fire alarm sounds, leave by the nearest marked exit.",
             "Meet at the assembly point beside the main gate.",
             "Do not re-enter until the supervisor gives permission."]
    commands = ["BT /F1 13 Tf 48 760 Td 22 TL"]
    for i, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(("T* " if i else "") + f"({escaped}) Tj")
    stream = ("\n".join(commands) + "\nET").encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"]
    result = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(result)
    result += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    result += b"".join(f"{n:010d} 00000 n \n".encode() for n in offsets[1:])
    return result + f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()


class Handler(BaseHTTPRequestHandler):
    max_body = MAX_BODY
    max_pdf_bytes = MAX_PDF_BYTES

    def expected_origin(self):
        return f"http://{self.headers.get('Host')}"

    def log_message(self, *_):
        pass  # No request bodies, credentials, document names, or answers in logs.

    def send(self, data, status=200, content_type="application/json"):
        body = json.dumps(data, ensure_ascii=False).encode() if content_type == "application/json" else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if getattr(self, "new_cookie", None):
            self.send_header("Set-Cookie", f"samjha_session={self.new_cookie}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

    def session(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            pass
        cookie = cookies.get("samjha_session")
        sid = cookie.value if cookie else ""
        with SESSIONS_LOCK:
            now = time.time()
            for old in list(SESSIONS):
                if now - SESSIONS[old][1]["created"] > EXPIRY:
                    del SESSIONS[old]
            if sid not in SESSIONS:
                if len(SESSIONS) >= 20:
                    raise AppError("Too many local sessions. Restart the app to clear them.")
                sid = secrets.token_urlsafe(32)
                self.new_cookie = sid
                SESSIONS[sid] = (threading.Lock(), new_state())
            return SESSIONS[sid]

    def do_GET(self):
        if not self.valid_host():
            return self.send({"error": "Invalid host."}, 403)
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if self.path in assets:
            name, mime = assets[self.path]
            return self.send((ROOT / "static" / name).read_bytes(), content_type=mime)
        if self.path == "/sample.pdf":
            return self.send(sample_pdf(), content_type="application/pdf")
        if self.path == "/api/session":
            try:
                lock, state = self.session()
                with lock:
                    return self.send(public(state) | {"configured": bool(os.environ.get("SARVAM_API_KEY")),
                                                      "max_pdf_bytes": self.max_pdf_bytes})
            except AppError as e:
                return self.send({"error": str(e)}, 400)
        self.send({"error": "Not found."}, 404)

    def do_POST(self):
        self.sarvam_stage = "Sarvam request"
        if not self.valid_host() or self.headers.get("X-SamjhaProof") != "1":
            return self.send({"error": "Please use the local SamjhaProof page."}, 403)
        origin = self.headers.get("Origin")
        if origin and origin != self.expected_origin():
            return self.send({"error": "Invalid origin."}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= self.max_body:
                raise AppError(f"This request is too large. Use a PDF smaller than {self.max_pdf_bytes // 1_000_000} MB.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise AppError("Invalid request.")
            self.request_state_token = data.pop("_state", None)
            lock, state = self.session()
            with lock:
                if self.path == "/api/reset":
                    state.clear()
                    state.update(new_state())
                    return self.send(public(state))
                if self.path == "/api/session":
                    return self.send(public(state) | {"configured": bool(os.environ.get("SARVAM_API_KEY")),
                                                      "max_pdf_bytes": self.max_pdf_bytes})
                if not os.environ.get("SARVAM_API_KEY"):
                    raise AppError("Add SARVAM_API_KEY to .env and restart the app.")
                with httpx.Client(timeout=75) as http_client:
                    client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"], httpx_client=http_client)
                    documents = DocumentAPI(http_client, os.environ["SARVAM_API_KEY"])
                    self.send(self.action(data, state, client, documents))
        except AppError as e:
            self.send({"error": str(e)}, 400)
        except ApiError as e:
            self.send({"error": provider_error(e, self.sarvam_stage)}, 502)
        except (httpx.HTTPError, ConnectionError, TimeoutError):
            self.send({"error": "Could not reach Sarvam. Check your internet connection and retry; your progress is preserved."}, 502)
        except ValidationError as e:
            fields = "; ".join(".".join(str(p) for p in item["loc"]) + " (" + item["type"] + ")"
                               for item in e.errors(include_input=False, include_url=False, include_context=False)[:5])
            safe = provider_error(ApiError(body={"message": "Invalid response fields: " + fields}), self.sarvam_stage)
            self.send({"error": safe.replace(" (Sarvam HTTP None)", "")}, 502)
        except (ValueError, KeyError, TypeError) as e:
            self.send({"error": f"{self.sarvam_stage}: response parsing failed ({type(e).__name__}). Your progress is preserved."}, 400)
        except Exception:
            self.send({"error": "The operation could not finish. Your progress is preserved; please retry."}, 500)

    def action(self, data, state, client, documents=None):
        route = self.path
        doc_api = documents if documents is not None else client.doc_ai
        if route == "/api/document":
            language = data.get("language", "English")
            doc_language = data.get("document_language", "English")
            mode = data.get("mode", "quick")
            if mode not in ("quick", "sections"):
                raise AppError("Choose Quick overview or Study by section.")
            if language not in LANGUAGES or doc_language not in LANGUAGES:
                raise AppError("Choose English or Hindi.")
            raw = base64.b64decode(data.get("file", ""), validate=True)
            if not raw.startswith(b"%PDF-") or not 0 < len(raw) <= self.max_pdf_bytes:
                raise AppError(f"Upload a valid PDF smaller than {self.max_pdf_bytes // 1_000_000} MB, with no more than 10 pages.")
            self.sarvam_stage = "PDF upload to Document AI"
            job = doc_api.digitise(file=[("document.pdf", io.BytesIO(raw), "application/pdf")],
                                         language=LANGUAGES[doc_language],
                                         request_options={"additional_body_parameters": {"output_format": "md"}})
            state.clear()
            state.update(new_state())
            state.update(job=job.job_id, filename=str(data.get("filename", "Document.pdf"))[:180],
                         language=language, document_language=doc_language, mode=mode)
            return public(state)
        if route == "/api/poll":
            if not state["job"]:
                raise AppError("Upload a document first.")
            if state["pages"]:
                return public(state) | {"status": "ready"}
            self.sarvam_stage = "Document AI processing status"
            status = doc_api.get_status(state["job"])
            current = status.status.lower()
            if current in ("failed", "rejected"):
                raise AppError("Document processing failed. Check Document AI access and use a readable PDF of up to 10 pages.")
            if current in ("completed", "partially_completed"):
                self.sarvam_stage = "Fetching extracted document pages"
                state["pages"] = extract_pages(doc_api.get_results(state["job"], format="json"))
                state["partial"] = current == "partially_completed"
                return public(state) | {"status": "ready"}
            return public(state) | {"status": current, "processed": status.usage.pages_processed or 0,
                                    "total": status.usage.pages_total or 0}
        if route == "/api/prepare":
            if not state["pages"]:
                raise AppError("Wait for the document to finish processing.")
            if not state["lesson"]:
                self.sarvam_stage = "Generating teach-back questions"
                if state.get("mode", "quick") == "sections":
                    if not state.get("outline"):
                        self.sarvam_stage = "Mapping document sections"
                        state["outline"] = create_outline(client, state["pages"], state["language"])
                    outline = state["outline"]
                    self.sarvam_stage = "Preparing the first section"
                    first = prepare_section(client, state["pages"], state["language"], outline["sections"][0])
                    state["lesson"] = {"title": outline["title"], "summary": outline["summary"],
                        "questions": [first] + [{"section": s["title"], "ready": False} for s in outline["sections"][1:]]}
                else:
                    state["lesson"] = prepare(client, state["pages"], state["language"])
            return public(state)
        if not state["lesson"]:
            raise AppError("Create a teach-back session first.")
        index = data.get("index", 0)
        if type(index) is not int or not 0 <= index < len(state["lesson"]["questions"]):
            raise AppError("Select a valid question.")
        question = state["lesson"]["questions"][index]
        if route == "/api/question":
            if question.get("ready") is False:
                self.sarvam_stage = "Preparing the selected section"
                question = prepare_section(client, state["pages"], state["language"], state["outline"]["sections"][index])
                state["lesson"]["questions"][index] = question
            return public(state)
        if question.get("ready") is False and not (route == "/api/speak" and data.get("kind") == "summary"):
            raise AppError("Open this section to prepare its question first.")
        if route == "/api/transcribe":
            self.sarvam_stage = "Transcribing your answer"
            raw = base64.b64decode(data.get("audio", ""), validate=True)
            check_recording(raw)
            transcript = client.speech_to_text.transcribe(file=("answer.wav", raw, "audio/wav"),
                model="saaras:v4", mode="transcribe", language_code="unknown").transcript
            if not transcript or not transcript.strip():
                raise AppError("No speech was detected. Try recording again or type your answer.")
            return {"transcript": transcript}
        if route == "/api/evaluate":
            self.sarvam_stage = "Assessing your answer"
            answer = data.get("answer", "")
            if not isinstance(answer, str) or not 1 <= len(answer.strip()) <= 4000:
                raise AppError("Enter an answer of 1–4,000 characters.")
            result = evaluate(client, question, answer.strip(), state["language"])
            previous = state["answers"].get(str(index), {})
            state["answers"][str(index)] = result | {"answer": answer.strip(),
                "attempt": previous.get("attempt", 0) + 1, "first_score": previous.get("first_score", result["score"])}
            state["audio"].pop(f"feedback-{index}", None)
            return public(state)
        if route == "/api/speak":
            kind = data.get("kind", "question")
            if kind not in ("question", "feedback", "summary"):
                raise AppError("Choose question, summary, or feedback audio.")
            if kind == "feedback":
                if str(index) not in state["answers"]:
                    raise AppError("Check your answer before playing feedback.")
                text = state["answers"][str(index)]["correction"]
            elif kind == "summary":
                text = state["lesson"]["summary"]
            else:
                text = question["question"]
            key = f"{kind}-{index}"
            if key not in state["audio"]:
                self.sarvam_stage = "Generating spoken audio"
                state["audio"][key] = speak(client, text, LANGUAGES[state["language"]])
            return {"audio": state["audio"][key], "mime": "audio/mpeg"}
        raise AppError("Unknown operation.")


if __name__ == "__main__":
    load_environment()
    port = int(os.environ.get("SAMJHA_PORT", "8501"))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            raise SystemExit(f"Port {port} is busy. Set SAMJHA_PORT=8502 and try again.") from None
        raise SystemExit("This environment blocks opening a local server. Run .venv/bin/python server.py in your own terminal.") from None
    print(f"SamjhaProof is ready at http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
