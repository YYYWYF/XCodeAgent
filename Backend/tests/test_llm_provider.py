"""验证当前转发配置与现有消息辅助转换，不保留上游连接配置。"""

import json
import os
import unittest
from unittest.mock import patch

from app.config import Settings
from app.services.llm_client import _openai_messages, _openai_tool


class ModelProviderConversionTests(unittest.TestCase):
    def test_openai_tool_uses_function_schema(self) -> None:
        """辅助工具声明保持模型所需函数格式。"""
        result = _openai_tool({"name": "read", "input_schema": {"type": "object", "required": ["path"]}})
        self.assertEqual(result["function"]["name"], "read")
        self.assertEqual(result["function"]["parameters"]["required"], ["path"])

    def test_message_tool_calls_and_results_preserve_relationship(self) -> None:
        """工具调用编号与工具结果对应，不丢失参数。"""
        result = _openai_messages([
            {"role": "assistant", "tool_calls": [{"id": "call-1", "name": "read", "input": {"path": "README.md"}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ])
        self.assertEqual(json.loads(result[0]["tool_calls"][0]["function"]["arguments"]), {"path": "README.md"})
        self.assertEqual(result[1]["tool_call_id"], "call-1")

    def test_defaults_start_without_upstream_configuration(self) -> None:
        """未配置真实模型时后端可启动，连接信息仅指向转发服务。"""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.model_transmit_url, "http://127.0.0.1:8100")
        self.assertEqual(settings.model_name, "studio-model")
        self.assertEqual(settings.model_provider, "transmit")
        self.assertFalse(hasattr(settings, "model_api_key"))
        self.assertFalse(hasattr(settings, "model_base_url"))

    def test_transmit_settings_and_business_budgets_are_independent(self) -> None:
        """服务连接与 UI/DAG 调用预算独立读取，密钥不进入配置 repr。"""
        with patch.dict(os.environ, {
            "MODEL_TRANSMIT_URL": "http://transmit.test:8100/", "MODEL_TRANSMIT_TOKEN": "private-test-token",
            "MODEL_TRANSMIT_TRUST_ENV": "true", "MODEL_TRANSMIT_SPAID": "spaid-test",
            "MODEL_MAX_RETRIES": "0", "MODEL_TIMEOUT_SECONDS": "45",
            "DEVAGENTSTUDIO_UI_DESIGN_MAX_TOKENS": "12288",
            "DEVAGENTSTUDIO_DAG_UNIT_MAX_TOKENS": "4096",
        }, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.model_transmit_url, "http://transmit.test:8100")
        self.assertEqual(settings.model_transmit_spaid, "spaid-test")
        self.assertTrue(settings.model_transmit_trust_env)
        self.assertEqual(settings.model_max_retries, 0)
        self.assertEqual(settings.model_timeout_seconds, 45)
        self.assertEqual(settings.ui_design_max_tokens, 12288)
        self.assertEqual(settings.dag_unit_max_tokens, 4096)
        self.assertNotIn("private-test-token", repr(settings))

    def test_upstream_environment_is_not_a_configuration_source(self) -> None:
        """历史上游变量即使存在也不能覆盖当前统一转发合同。"""
        with patch.dict(os.environ, {
            "MODEL_BASE_URL": "https://upstream.invalid/v1", "MODEL_API_KEY": "must-not-read",
            "MODEL_NAME": "must-not-read", "UI_DESIGN_MODEL_NAME": "must-not-read",
        }, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.model_name, "studio-model")
        self.assertEqual(settings.model_transmit_url, "http://127.0.0.1:8100")
        self.assertNotIn("must-not-read", repr(settings))


if __name__ == "__main__":
    unittest.main()
