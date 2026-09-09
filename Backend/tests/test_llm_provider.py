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

    def test_ui_design_model_falls_back_to_global_when_unconfigured(self) -> None:
        """未配置 UI_DESIGN_MODEL_* 时，UI 节点用全局模型（for_ui_design_model 返回自身）。"""

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

        self.assertIs(settings.for_ui_design_model(), settings)

    def test_ui_design_model_requires_all_three_fields(self) -> None:
        """UI_DESIGN_MODEL_* 缺任一项即回落全局模型，不产生半配置状态。"""

        with patch.dict(
            os.environ,
            {
                "MODEL_BASE_URL": "https://example.com/v1",
                "MODEL_API_KEY": "test-key",
                "MODEL_NAME": "test-model",
                "UI_DESIGN_MODEL_BASE_URL": "https://api.deepseek.com/v1",
                "UI_DESIGN_MODEL_NAME": "deepseek-v4-pro",
                # 缺 UI_DESIGN_MODEL_API_KEY
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertIs(settings.for_ui_design_model(), settings)

    def test_ui_design_model_overrides_when_fully_configured(self) -> None:
        """三项齐全时，UI 节点模型字段整体替换为独立配置，缺省项跟随全局。"""

        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "openai",
                "MODEL_BASE_URL": "https://tunnel.example.com/v1",
                "MODEL_API_KEY": "global-key",
                "MODEL_NAME": "glm-5.2",
                "MODEL_CUSTOM_HEADERS": "CF-Access-Client-Id: global-id",
                "UI_DESIGN_MODEL_BASE_URL": "https://api.deepseek.com/v1",
                "UI_DESIGN_MODEL_API_KEY": "deepseek-key",
                "UI_DESIGN_MODEL_NAME": "deepseek-v4-pro",
            },
            clear=True,
        ):
            settings = Settings.from_env()
            resolved = settings.for_ui_design_model()

        self.assertEqual(resolved.model_base_url, "https://api.deepseek.com/v1")
        self.assertEqual(resolved.model_api_key, "deepseek-key")
        self.assertEqual(resolved.model_name, "deepseek-v4-pro")
        # 缺省项跟随全局：provider / custom_headers / trust_env。
        self.assertEqual(resolved.model_provider, "openai")
        self.assertEqual(
            resolved.model_custom_headers, {"CF-Access-Client-Id": "global-id"}
        )
        # 非模型字段不受影响。
        self.assertEqual(resolved.ui_design_max_tokens, settings.ui_design_max_tokens)

    def test_ui_design_model_explicit_provider_and_headers_win(self) -> None:
        """显式配置的 UI_DESIGN_MODEL_PROVIDER/CUSTOM_HEADERS 覆盖全局值。"""

        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "anthropic",
                "MODEL_BASE_URL": "https://tunnel.example.com/v1",
                "MODEL_API_KEY": "global-key",
                "MODEL_NAME": "glm-5.2",
                "MODEL_CUSTOM_HEADERS": "X-Global: 1",
                "UI_DESIGN_MODEL_PROVIDER": "openai",
                "UI_DESIGN_MODEL_BASE_URL": "https://api.deepseek.com/v1",
                "UI_DESIGN_MODEL_API_KEY": "deepseek-key",
                "UI_DESIGN_MODEL_NAME": "deepseek-v4-pro",
                "UI_DESIGN_MODEL_CUSTOM_HEADERS": "X-UI: 2",
            },
            clear=True,
        ):
            resolved = Settings.from_env().for_ui_design_model()

        self.assertEqual(resolved.model_provider, "openai")
        self.assertEqual(resolved.model_custom_headers, {"X-UI": "2"})

    def test_ui_design_model_rejects_unknown_provider(self) -> None:
        """UI_DESIGN_MODEL_PROVIDER 非法时在解析点报错（misconfiguration fails loud）。"""

        with patch.dict(
            os.environ,
            {
                "MODEL_BASE_URL": "https://example.com/v1",
                "MODEL_API_KEY": "test-key",
                "MODEL_NAME": "test-model",
                "UI_DESIGN_MODEL_PROVIDER": "other",
                "UI_DESIGN_MODEL_BASE_URL": "https://api.deepseek.com/v1",
                "UI_DESIGN_MODEL_API_KEY": "deepseek-key",
                "UI_DESIGN_MODEL_NAME": "deepseek-v4-pro",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "UI_DESIGN_MODEL_PROVIDER"):
                Settings.from_env().for_ui_design_model()

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
