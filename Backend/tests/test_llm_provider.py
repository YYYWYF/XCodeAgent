from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from app.config import Settings
from app.services.llm_client import _openai_messages, _openai_tool


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

    def test_settings_default_to_openai_provider_when_not_configured(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MODEL_BASE_URL": "https://example.com/v1",
                "MODEL_API_KEY": "test-key",
                "MODEL_NAME": "test-model",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.model_provider, "openai")

    def test_settings_normalize_openai_compatible_provider(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "openai-compatible",
                "MODEL_BASE_URL": "https://example.com/v1",
                "MODEL_API_KEY": "test-key",
                "MODEL_NAME": "test-model",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.model_provider, "openai")

    def test_settings_reject_non_openai_provider(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "other",
                "MODEL_BASE_URL": "https://example.com/v1",
                "MODEL_API_KEY": "test-key",
                "MODEL_NAME": "test-model",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "Only OpenAI-compatible"):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
