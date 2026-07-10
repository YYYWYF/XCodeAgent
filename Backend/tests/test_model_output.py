from __future__ import annotations

import unittest

from app.utils.model_output import extract_json_object


class ModelOutputTests(unittest.TestCase):
    def test_extracts_plain_json_object(self) -> None:
        self.assertEqual(extract_json_object('{"status": "ok"}'), {"status": "ok"})

    def test_extracts_json_from_markdown_fence(self) -> None:
        text = '```json\n{"pages": [{"id": "home"}]}\n```'
        self.assertEqual(
            extract_json_object(text),
            {"pages": [{"id": "home"}]},
        )

    def test_extracts_first_object_surrounded_by_commentary(self) -> None:
        text = '模型说明：以下是结果。\n{"version": 1}\n后续说明。'
        self.assertEqual(extract_json_object(text), {"version": 1})

    def test_returns_none_without_valid_object(self) -> None:
        self.assertIsNone(extract_json_object("not json"))
        self.assertIsNone(extract_json_object("[]"))

    def test_extracts_object_nested_in_array_text(self) -> None:
        self.assertEqual(extract_json_object('[{"id": 1}]'), {"id": 1})


if __name__ == "__main__":
    unittest.main()
