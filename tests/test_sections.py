import unittest
from copy import deepcopy
from unittest.mock import Mock

from core import AppError, create_outline, prepare, prepare_section
from server import Handler, new_state
from hosted_state import encode_state, decode_state
from test_core import PAGES, completion, lesson_data, assessment_data


def outline_data(count=5):
    return {"title": "Safety handbook", "summary": "Important workshop instructions.",
            "sections": [{"title": f"Topic {i}", "pages": [i], "focus": f"Safety instructions in topic {i}"} for i in range(1, count + 1)]}


def single_question(page=1):
    data = lesson_data()
    data["questions"] = [data["questions"][0]]
    data["questions"][0]["page"] = page
    return data


class SectionStudyTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.pages = [{"page": i, "text": PAGES[0]["text"]} for i in range(1, 6)]
        self.handler = object.__new__(Handler)
        self.state = new_state()
        self.state.update(pages=self.pages, mode="sections")

    def action(self, route, data=None):
        self.handler.path = "/api/" + route
        return self.handler.action(data or {}, self.state, self.client)

    def test_outline_covers_all_pages_and_varies_with_sections(self):
        self.client.chat.completions.return_value = completion(outline_data())
        outline = create_outline(self.client, self.pages, "English")
        self.assertEqual(len(outline["sections"]), 5)

    def test_missing_or_invented_pages_and_duplicate_sections_rejected(self):
        for data in (outline_data(4), outline_data(6)):
            self.client.chat.completions.return_value = completion(data)
            with self.assertRaises(AppError): create_outline(self.client, self.pages, "English")
        data = outline_data(); data["sections"][1]["title"] = data["sections"][0]["title"]
        self.client.chat.completions.return_value = completion(data)
        with self.assertRaises(AppError): create_outline(self.client, self.pages, "English")

    def test_quick_overview_still_requires_exactly_three(self):
        self.client.chat.completions.return_value = completion(lesson_data())
        self.assertEqual(len(prepare(self.client, PAGES, "English")["questions"]), 3)
        self.client.chat.completions.return_value = completion(single_question())
        with self.assertRaises(AppError): prepare(self.client, PAGES, "English")

    def test_section_question_only_uses_its_own_pages(self):
        self.client.chat.completions.return_value = completion(single_question(5))
        question = prepare_section(self.client, self.pages, "Hindi", outline_data()["sections"][4])
        self.assertEqual(question["page"], 5)
        self.assertEqual(question["section"], "Topic 5")
        self.assertTrue(question["ready"])
        import json
        payload = json.loads(self.client.chat.completions.call_args.kwargs["messages"][1]["content"])
        self.assertEqual([p["page"] for p in payload["pages"]], [5])
        self.assertEqual(payload["question_count"], 1)

    def test_lazy_generation_and_evaluation_beyond_third_question(self):
        self.client.chat.completions.side_effect = [completion(outline_data()), completion(single_question())]
        result = self.action("prepare")
        self.assertEqual(len(result["lesson"]["questions"]), 5)
        self.assertFalse(result["lesson"]["questions"][4]["ready"])
        self.assertEqual(self.client.chat.completions.call_count, 2)
        self.client.chat.completions.side_effect = None
        self.client.chat.completions.return_value = completion(single_question(5))
        result = self.action("question", {"index": 4})
        self.assertEqual(result["lesson"]["questions"][4]["page"], 5)
        self.assertNotIn("criteria", result["lesson"]["questions"][4])
        count = self.client.chat.completions.call_count
        self.action("question", {"index": 4})
        self.assertEqual(self.client.chat.completions.call_count, count)
        self.client.chat.completions.return_value = completion(assessment_data())
        result = self.action("evaluate", {"index": 4, "answer": "Wear safety goggles"})
        self.assertEqual(result["answers"]["4"]["score"], 50)
        with self.assertRaises(AppError): self.action("question", {"index": 5})
        with self.assertRaises(AppError): self.action("evaluate", {"index": 2, "answer": "Not yet prepared"})

    def test_failed_section_request_preserves_existing_lesson_and_answers(self):
        self.client.chat.completions.side_effect = [completion(outline_data()), completion(single_question())]
        self.action("prepare")
        snapshot = deepcopy(self.state["lesson"])
        self.state["answers"]["0"] = {"score": 50}
        self.client.chat.completions.side_effect = AppError("Temporary failure")
        with self.assertRaises(AppError): self.action("question", {"index": 4})
        self.assertEqual(self.state["lesson"], snapshot)
        self.assertEqual(self.state["answers"]["0"]["score"], 50)

    def test_failed_first_question_reuses_outline_on_retry(self):
        self.client.chat.completions.side_effect = [completion(outline_data()), AppError("Temporary failure")]
        with self.assertRaises(AppError): self.action("prepare")
        self.assertIsNone(self.state["lesson"])
        self.assertIsNotNone(self.state["outline"])
        self.client.chat.completions.side_effect = None
        self.client.chat.completions.return_value = completion(single_question())
        self.action("prepare")
        self.assertEqual(self.client.chat.completions.call_count, 3)

    def test_section_progress_survives_serverless_snapshot(self):
        import secrets
        self.client.chat.completions.side_effect = [completion(outline_data()), completion(single_question())]
        self.action("prepare")
        key = secrets.token_urlsafe(32)
        self.state = decode_state(encode_state(self.state, key), key)
        self.assertEqual(self.state["mode"], "sections")
        self.assertEqual(len(self.state["outline"]["sections"]), 5)
        self.assertFalse(self.state["lesson"]["questions"][4]["ready"])
        self.client.chat.completions.side_effect = None
        self.client.chat.completions.return_value = completion(single_question(5))
        self.assertTrue(self.action("question", {"index": 4})["lesson"]["questions"][4]["ready"])


if __name__ == "__main__":
    unittest.main()
