import base64
import io
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from sarvamai.core.api_error import ApiError
from sarvamai import SarvamAI

from core import (AppError, check_recording, evaluate, extract_pages,
                  json_completion, prepare, speak, validate_evaluation, validate_lesson)
from server import Handler, SESSIONS, new_state, public, sample_pdf, provider_error
from documents import DocumentAPI


PAGES = [{"page": 1, "text": "Wear safety goggles before entering. Turn off the machine. Use the marked exit."}]


def lesson_data():
    return {"title": "Workshop safety", "summary": "Three essential safety instructions.", "questions": [
        {"question": "What should you wear before entering?", "explanation": "Wear safety goggles before entering.",
         "page": 1, "quote": "Wear safety goggles before entering.", "criteria": ["Wear safety goggles", "Before entering"]},
        {"question": "What should you do with the machine?", "explanation": "Turn off the machine.",
         "page": 1, "quote": "Turn off the machine.", "criteria": ["Turn off the machine"]},
        {"question": "Which exit should you use?", "explanation": "Use the marked exit.",
         "page": 1, "quote": "Use the marked exit.", "criteria": ["Use the marked exit"]}]}


def assessment_data():
    return {"results": [
        {"criterion_index": 0, "status": "understood", "evidence": "safety goggles", "feedback": "Correct equipment."},
        {"criterion_index": 1, "status": "missed", "evidence": "", "feedback": "Also mention when to wear them."}],
        "correction": "Put on safety goggles before entering."}


def completion(data):
    import json
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)), finish_reason="stop")])


class GroundingTests(unittest.TestCase):
    def test_exact_page_citations_and_whitespace(self):
        data = lesson_data()
        data["questions"][0]["quote"] = "Wear safety goggles\nbefore entering."
        self.assertEqual(validate_lesson(data, PAGES)["title"], "Workshop safety")

    def test_fabricated_quote_rejected(self):
        data = lesson_data()
        data["questions"][0]["quote"] = "Wear red gloves before entering."
        with self.assertRaises(AppError): validate_lesson(data, PAGES)

    def test_wrong_page_rejected(self):
        data = lesson_data(); data["questions"][0]["page"] = 2
        with self.assertRaises(AppError): validate_lesson(data, PAGES)

    def test_repeated_question_rejected(self):
        data = lesson_data(); data["questions"][1]["question"] = data["questions"][0]["question"]
        with self.assertRaises(AppError): validate_lesson(data, PAGES)

    def test_score_is_computed_from_criteria(self):
        result = validate_evaluation(assessment_data(), lesson_data()["questions"][0], "I should wear safety goggles.")
        self.assertEqual(result["score"], 50)
        self.assertEqual(result["results"][0]["criterion"], "Wear safety goggles")

    def test_missing_or_duplicate_criteria_rejected(self):
        for indexes in ([0], [0, 0]):
            data = assessment_data(); data["results"] = [data["results"][i] for i in indexes]
            with self.assertRaises(AppError):
                validate_evaluation(data, lesson_data()["questions"][0], "safety goggles")

    def test_invented_answer_evidence_rejected(self):
        with self.assertRaises(AppError):
            validate_evaluation(assessment_data(), lesson_data()["questions"][0], "yes")

    def test_extract_markdown_pages(self):
        pages = extract_pages({"documents": [{"pages": [{"page_number": 3, "content": "An actual page"}]}]})
        self.assertEqual(pages, [{"page": 3, "text": "An actual page"}])

    def test_extract_structured_blocks(self):
        self.assertEqual(extract_pages({"documents": [{"pages": [{"page_number": 1, "blocks": [{"text": "Hello"}]}]}]})[0]["text"], "Hello")

    def test_empty_and_oversized_extraction_rejected(self):
        for pages in ([], [{"content": ""}], [{"content": "x" * 65001}]):
            with self.assertRaises(AppError): extract_pages({"documents": [{"pages": pages}]})

    def test_json_parse_and_truncation_errors(self):
        client = Mock(); client.chat.completions.return_value = completion({"ok": True})
        self.assertEqual(json_completion(client, "System", {}), {"ok": True})
        client.chat.completions.return_value.choices[0].finish_reason = "length"
        with self.assertRaises(AppError): json_completion(client, "System", {})
        client.chat.completions.return_value.choices[0].finish_reason = "stop"
        client.chat.completions.return_value.choices[0].message.content = "bad json"
        with self.assertRaises(AppError): json_completion(client, "System", {})

    def test_question_and_evaluation_sdk_contract(self):
        client = Mock(); client.chat.completions.return_value = completion(lesson_data())
        lesson = prepare(client, PAGES, "Hindi")
        self.assertEqual(len(lesson["questions"]), 3)
        kwargs = client.chat.completions.call_args.kwargs
        self.assertEqual(kwargs["model"], "sarvam-105b")
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        client.chat.completions.return_value = completion(assessment_data())
        self.assertEqual(evaluate(client, lesson["questions"][0], "safety goggles", "Hindi")["score"], 50)

    def test_tts_voice_contract(self):
        client = Mock(); client.text_to_speech.convert.return_value.audios = [base64.b64encode(b"audio").decode()]
        self.assertEqual(base64.b64decode(speak(client, "Hello", "en-IN")), b"audio")
        self.assertEqual(client.text_to_speech.convert.call_args.kwargs["speaker"], "ritu_en_edtech")

    def test_recording_duration_and_invalid_audio(self):
        def wav(seconds):
            out = io.BytesIO()
            with wave.open(out, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(b"\0\0" * int(16000 * seconds))
            return out.getvalue()
        check_recording(wav(1)); check_recording(wav(30))
        for raw in (b"invalid", wav(.1), wav(31)):
            with self.assertRaises(AppError): check_recording(raw)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.handler = object.__new__(Handler)
        self.client = Mock(); self.state = new_state()

    def action(self, path, data=None):
        self.handler.path = f"/api/{path}"
        return self.handler.action(data or {}, self.state, self.client)

    def test_full_document_to_report_flow(self):
        self.client.doc_ai.digitise.return_value.job_id = "unit-test-document-job"
        result = self.action("document", {"file": base64.b64encode(sample_pdf()).decode(), "filename": "Safety.pdf", "language": "Hindi"})
        self.assertEqual(result["filename"], "Safety.pdf")
        self.client.doc_ai.get_status.return_value = SimpleNamespace(status="completed")
        self.client.doc_ai.get_results.return_value = {"documents": [{"pages": [{"page_number": 1, "content": PAGES[0]["text"]}]}]}
        self.assertEqual(self.action("poll")["status"], "ready")
        self.client.chat.completions.return_value = completion(lesson_data())
        result = self.action("prepare")
        self.assertNotIn("criteria", result["lesson"]["questions"][0])
        # Repeated prepare/poll requests reuse existing results instead of charging again.
        self.action("prepare"); self.action("poll")
        self.assertEqual(self.client.chat.completions.call_count, 1)
        self.assertEqual(self.client.doc_ai.get_results.call_count, 1)
        self.client.chat.completions.return_value = completion(assessment_data())
        result = self.action("evaluate", {"index": 0, "answer": "Wear safety goggles"})
        self.assertEqual(result["answers"]["0"]["score"], 50)
        result = self.action("evaluate", {"index": 0, "answer": "Wear safety goggles"})
        self.assertEqual(result["answers"]["0"]["attempt"], 2)
        self.assertEqual(result["answers"]["0"]["first_score"], 50)

    def test_partial_ocr_flagged(self):
        self.state["job"] = "unit-test-job"
        self.client.doc_ai.get_status.return_value = SimpleNamespace(status="partially_completed")
        self.client.doc_ai.get_results.return_value = {"documents": [{"pages": [{"content": "Some readable text."}]}]}
        self.assertTrue(self.action("poll")["partial"])

    def test_failed_job_not_graded(self):
        self.state["job"] = "unit-test-job"
        self.client.doc_ai.get_status.return_value = SimpleNamespace(status="failed")
        with self.assertRaises(AppError): self.action("poll")
        self.client.doc_ai.get_results.assert_not_called()

    def test_pdf_validation_happens_before_api(self):
        for data in (b"not a PDF", b"%PDF-" + b"x" * (20 * 1024 * 1024)):
            with self.assertRaises(AppError): self.action("document", {"file": base64.b64encode(data).decode()})
        self.client.doc_ai.digitise.assert_not_called()

    def test_session_state_is_independent(self):
        a, b = new_state(), new_state()
        a["answers"]["0"] = {"score": 50}
        self.assertEqual(b["answers"], {})

    def test_audio_cache_and_invalid_kind(self):
        self.state["lesson"] = lesson_data()
        self.client.text_to_speech.convert.return_value.audios = [base64.b64encode(b"sound").decode()]
        self.action("speak", {"index": 0, "kind": "question"}); self.action("speak", {"index": 0, "kind": "question"})
        self.assertEqual(self.client.text_to_speech.convert.call_count, 1)
        with self.assertRaises(AppError): self.action("speak", {"kind": "invalid"})
        with self.assertRaises(AppError): self.action("speak", {"kind": "feedback"})
        self.assertNotIn("audio", public(self.state))

    def test_invalid_question_and_blank_answer(self):
        self.state["lesson"] = lesson_data()
        for data in ({"index": -1, "answer": "ok"}, {"index": True, "answer": "ok"}, {"index": 0, "answer": "  "}):
            with self.assertRaises(AppError): self.action("evaluate", data)
        self.client.chat.completions.assert_not_called()

    def test_sample_pdf_offsets_are_valid(self):
        pdf = sample_pdf()
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        xref = int(pdf.split(b"startxref\n")[1].splitlines()[0])
        self.assertEqual(pdf[xref:xref + 4], b"xref")
        self.assertIn(b"/Count 1", pdf)

    def test_real_sdk_sends_document_format_in_multipart(self):
        captured = []
        def transport(request):
            body = request.read()
            self.assertIn(b'name="output_format"', body)
            self.assertIn(b'\r\n\r\nmd\r\n', body)
            self.assertIn(b'name="file"; filename="document.pdf"', body)
            self.assertIn(b'name="language"', body)
            self.assertIn(b'\r\n\r\nen-IN\r\n', body)
            captured.append(request.url.path)
            return httpx.Response(201, json={"job_id": "test-document-job", "status": "pending", "run_id": "test-run"})
        with httpx.Client(transport=httpx.MockTransport(transport)) as http_client:
            client = SarvamAI(api_subscription_key="", httpx_client=http_client)
            self.handler.path = "/api/document"
            self.handler.action({"file": base64.b64encode(sample_pdf()).decode()}, self.state, client)
        self.assertEqual(captured, ["/doc-ai/v1/job/digitise"])

    def test_actionable_provider_error_has_stage_and_reason(self):
        failure = ApiError(status_code=400, body={"error": {"message": "output_format must be md or html"}})
        message = provider_error(failure, "PDF upload")
        self.assertIn("PDF upload", message)
        self.assertIn("output_format must be md or html", message)
        self.assertNotIn("credits", message)


class HttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.handler = object.__new__(Handler)
        self.handler.headers = {"Host": "localhost:8501", "X-SamjhaProof": "1"}
        self.handler.server = SimpleNamespace(server_port=8501)
        self.handler.send = Mock()

    def tearDown(self):
        SESSIONS.clear()

    def post(self, path, body=b"{}"):
        self.handler.path = f"/api/{path}"
        self.handler.headers["Content-Length"] = str(len(body))
        self.handler.rfile = io.BytesIO(body)
        self.handler.do_POST()
        return self.handler.send.call_args

    def test_cross_origin_write_rejected(self):
        self.handler.headers["Origin"] = "https://another-site.example"
        self.assertEqual(self.post("reset").args[1], 403)

    def test_custom_header_required(self):
        del self.handler.headers["X-SamjhaProof"]
        self.assertEqual(self.post("reset").args[1], 403)

    def test_dns_rebinding_host_rejected(self):
        self.handler.headers["Host"] = "another-site.example:8501"
        self.assertEqual(self.post("reset").args[1], 403)

    def test_invalid_json_handled(self):
        self.assertEqual(self.post("reset", b"[").args[1], 400)

    def test_env_file_never_served(self):
        self.handler.path = "/.env"
        self.handler.do_GET()
        self.assertEqual(self.handler.send.call_args.args[1], 404)

    def test_cookie_isolates_sessions_and_reset_clears(self):
        _, first = self.handler.session()
        sid = self.handler.new_cookie
        first["filename"] = "private.pdf"
        self.handler.headers["Cookie"] = f"samjha_session={sid}"
        self.assertIs(self.handler.session()[1], first)
        other = object.__new__(Handler); other.headers = {}
        self.assertEqual(other.session()[1]["filename"], "")
        self.post("reset")
        self.assertEqual(first["filename"], "")

    def test_raw_provider_errors_not_exposed(self):
        for failure in (httpx.ConnectError("private request details"),
                        ApiError(status_code=403, body={"details": "private request details"})):
            self.handler.session = Mock(side_effect=failure)
            call = self.post("reset")
            self.assertEqual(call.args[1], 502)
            self.assertNotIn("private request details", str(call.args[0]))

    def test_response_security_headers(self):
        self.handler.send = Handler.send.__get__(self.handler)
        self.handler.request_version = "HTTP/1.1"
        self.handler.requestline = "GET / HTTP/1.1"
        self.handler.wfile = io.BytesIO()
        self.handler.new_cookie = "session-id"
        self.handler.send({"ok": True})
        response = self.handler.wfile.getvalue().decode()
        self.assertIn("HttpOnly; SameSite=Strict", response)
        self.assertIn("Cache-Control: no-store", response)
        self.assertIn("frame-ancestors 'none'", response)


class DocumentAdapterTests(unittest.TestCase):
    def test_full_flow_without_sdk_only_metadata(self):
        calls = []
        def transport(request):
            calls.append(request.url.path)
            if request.method == "POST":
                raw = request.read()
                self.assertIn(b'name="output_format"', raw)
                self.assertIn(b'\r\n\r\nmd\r\n', raw)
                # Valid job response without run_id.
                return httpx.Response(201, json={"job_id": "document-job", "status": "pending"})
            if request.url.path.endswith("/status"):
                # No timestamps, pipeline, or usage required for a completed job.
                return httpx.Response(200, json={"data": {"status": "completed"}})
            self.assertEqual(request.url.params["format"], "json")
            # No SDK union discriminator or unused metadata required.
            return httpx.Response(200, json={"documents": [{"pages": [{"page_number": 1, "content": PAGES[0]["text"]}]}]})
        with httpx.Client(transport=httpx.MockTransport(transport)) as http:
            documents = DocumentAPI(http, "")
            handler = object.__new__(Handler)
            state = new_state(); client = Mock()
            handler.path = "/api/document"
            handler.action({"file": base64.b64encode(sample_pdf()).decode()}, state, client, documents)
            handler.path = "/api/poll"
            result = handler.action({}, state, client, documents)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(state["pages"], PAGES)
        self.assertEqual(len(calls), 3)

    def test_invalid_document_data_is_not_silently_accepted(self):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))) as http:
            documents = DocumentAPI(http, "")
            with self.assertRaisesRegex(AppError, "job_id"):
                documents.digitise(file=[("doc.pdf", b"%PDF-", "application/pdf")], language="en-IN")
            with self.assertRaisesRegex(AppError, "status field"):
                documents.get_status("document-job")

    def test_api_rejection_preserves_provider_reason(self):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(400, json={"error": {"message": "invalid output_format"}}))) as http:
            with self.assertRaises(ApiError) as result:
                DocumentAPI(http, "").get_status("document-job")
            self.assertIn("invalid output_format", provider_error(result.exception, "Status"))

    def test_results_ask_for_json_instead_of_parsing_markdown(self):
        def transport(request):
            if request.url.params.get("format") == "md":
                return httpx.Response(200, text="# Readable markdown", headers={"content-type": "text/markdown"})
            self.assertEqual(request.url.params["format"], "json")
            self.assertEqual(request.headers["Accept"], "application/json")
            return httpx.Response(200, json={"pages": [{"page_number": 2, "blocks": [{"text": "An actual page."}]}]})
        with httpx.Client(transport=httpx.MockTransport(transport)) as http:
            result = DocumentAPI(http, "").get_results("job")
            self.assertEqual(extract_pages(result), [{"page": 2, "text": "An actual page."}])

    def test_html_gateway_error_preserves_status_not_fake_json_failure(self):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(502, text="<html>Gateway error</html>", headers={"content-type": "text/html"}))) as http:
            with self.assertRaises(ApiError) as result:
                DocumentAPI(http, "").get_status("job")
            self.assertEqual(result.exception.status_code, 502)
            self.assertIn("text/html", result.exception.body["message"])
            self.assertNotIn("<html>", result.exception.body["message"])


if __name__ == "__main__":
    unittest.main()
