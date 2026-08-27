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

    def test_settings_load_ui_design_limits(self) -> None:
        """验证 UI 设计稿生成限制可由环境变量覆盖。"""

        with patch.dict(
            os.environ,
            {
                "MODEL_BASE_URL": "https://example.com/v1",
                "MODEL_API_KEY": "test-key",
                "MODEL_NAME": "test-model",
                "XCODEAGENT_UI_DESIGN_MAX_TOKENS": "12288",
                "XCODEAGENT_UI_DESIGN_MAX_RETRIES": "3",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.ui_design_max_tokens, 12288)
        self.assertEqual(settings.ui_design_max_retries, 3)

    def test_dag_business_self_check_defaults_to_disabled(self) -> None:
        """验证 DAG 业务自检在未配置环境变量时默认关闭。"""

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

        self.assertFalse(settings.dag_business_self_check_enabled)

    def test_dag_business_self_check_can_be_enabled(self) -> None:
        """验证环境变量可显式开启 DAG 业务自检。"""

        with patch.dict(
            os.environ,
            {
                "MODEL_BASE_URL": "https://example.com/v1",
                "MODEL_API_KEY": "test-key",
                "MODEL_NAME": "test-model",
                "XCODEAGENT_DAG_BUSINESS_SELF_CHECK_ENABLED": "true",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertTrue(settings.dag_business_self_check_enabled)

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
