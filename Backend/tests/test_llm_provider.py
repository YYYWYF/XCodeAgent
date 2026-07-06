from __future__ import annotations

import json
import unittest

from app.services.llm_client import _anthropic_messages, _openai_messages, _openai_tool


class ModelProviderConversionTests(unittest.TestCase):
    def test_openai_tool_uses_function_schema(self) -> None:
        tool = _openai_tool(
            {
                "name": "file_read",
                "description": "Read a file.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        )

        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["function"]["name"], "file_read")
        self.assertEqual(tool["function"]["parameters"]["required"], ["path"])

    def test_openai_messages_convert_tool_calls_and_results(self) -> None:
        messages = _openai_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "call-1", "name": "file_read", "input": {"path": "README.md"}}
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": '{"ok":true}',
                    "is_error": False,
                },
            ]
        )

        function = messages[0]["tool_calls"][0]["function"]
        self.assertEqual(function["name"], "file_read")
        self.assertEqual(json.loads(function["arguments"]), {"path": "README.md"})
        self.assertEqual(
            messages[1],
            {"role": "tool", "tool_call_id": "call-1", "content": '{"ok":true}'},
        )

    def test_anthropic_messages_convert_tool_calls_and_results(self) -> None:
        messages = _anthropic_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "call-1", "name": "file_read", "input": {"path": "README.md"}}
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": '{"ok":true}',
                    "is_error": False,
                },
            ]
        )

        self.assertEqual(messages[0]["content"][0]["type"], "tool_use")
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"][0]["type"], "tool_result")
        self.assertEqual(messages[1]["content"][0]["tool_use_id"], "call-1")


if __name__ == "__main__":
    unittest.main()
