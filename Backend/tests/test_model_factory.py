"""统一模型转发工厂的预算、配置隔离和回调合同。"""

import os
import unittest
from dataclasses import asdict, replace
from unittest.mock import patch

from app.config import Settings
from app.agents.model_factory import create_chat_model


class ModelFactoryTests(unittest.TestCase):
    """工厂只创建转发模型，调用方业务预算仍沿用最新 Studio。"""

    def setUp(self):
        """隔离模型构造，不访问真实服务。"""
        self.settings = Settings(model_transmit_url="http://relay.test:8100", model_transmit_token="service-secret",
                                 default_temperature=0.4, default_max_tokens=2048,
                                 model_timeout_seconds=120, model_max_retries=2)
        self.factory = self.enterContext(patch("app.agents.model_factory.TransmitChatModel"))

    def test_factory_preserves_budgets_and_uses_only_service_connection(self):
        """模型构造不读取真实上游地址或密钥。"""
        result = create_chat_model(self.settings)
        self.assertIs(result, self.factory.return_value)
        kwargs = self.factory.call_args.kwargs
        self.assertEqual(kwargs["transmit_url"], "http://relay.test:8100")
        self.assertEqual(kwargs["transmit_token"], "service-secret")
        self.assertEqual((kwargs["max_tokens"], kwargs["max_retries"], kwargs["timeout_seconds"]), (2048, 2, 120))
        self.assertNotIn("api_key", kwargs)
        self.assertNotIn("base_url", kwargs)

    def test_zero_retry_and_call_overrides_do_not_mutate_global_settings(self):
        """零重试、独立输出及超时预算不得污染其他场景。"""
        before = asdict(self.settings)
        create_chat_model(self.settings, max_tokens_override=4096, max_retries_override=0, timeout_seconds_override=45)
        kwargs = self.factory.call_args.kwargs
        self.assertEqual((kwargs["max_tokens"], kwargs["max_retries"], kwargs["timeout_seconds"]), (4096, 0, 45))
        create_chat_model(self.settings)
        self.assertEqual(self.factory.call_args.kwargs["max_retries"], 2)
        self.assertEqual(asdict(self.settings), before)

    def test_logging_and_extra_parameters_preserve_original_input(self):
        """保留正文回调及 thinking，不回写扩展参数。"""
        extras = {"thinking": {"type": "disabled"}}
        with patch("app.agents.model_factory.ModelOutputLogHandler") as handler:
            create_chat_model(replace(self.settings, model_output_log_enabled=True), extra_model_kwargs=extras)
        kwargs = self.factory.call_args.kwargs
        self.assertEqual(kwargs["callbacks"], [handler.return_value])
        self.assertEqual(kwargs["extra_body"], extras)
        self.assertIsNot(kwargs["extra_body"], extras)

    def test_environment_needs_no_upstream_model_key(self):
        """没有直连模型环境也能创建本地模型视图，旧上游变量不会触发直连。"""
        with patch.dict(os.environ, {"MODEL_TRANSMIT_URL": "http://relay.test:8100/",
                                     "MODEL_TRANSMIT_TOKEN": "service-only", "MODEL_PROVIDER": "anthropic",
                                     "MODEL_API_KEY": "must-not-load", "MODEL_BASE_URL": "https://upstream.invalid"}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.model_transmit_url, "http://relay.test:8100")
        self.assertEqual(settings.model_provider, "transmit")
        self.assertEqual(settings.model_api_key, "")
        self.assertEqual(settings.model_base_url, "")
        self.assertEqual(settings.model_api_name, "studio-model")

    def test_ui_connection_can_override_without_changing_business_parameters(self):
        """UI 独立转发连接只替换连接视图，生成预算和全局实例不变。"""
        original = replace(self.settings, ui_design_model_transmit_url="http://ui-relay.test:8200",
                           ui_design_model_transmit_token="ui-secret", ui_design_model_transmit_trust_env=True)
        resolved = original.for_ui_design_model()
        self.assertEqual(resolved.model_transmit_url, "http://ui-relay.test:8200")
        self.assertEqual(resolved.model_transmit_token, "ui-secret")
        self.assertTrue(resolved.model_transmit_trust_env)
        self.assertEqual(resolved.ui_design_max_tokens, original.ui_design_max_tokens)
        self.assertEqual(original.model_transmit_url, self.settings.model_transmit_url)
        self.assertIs(self.settings.for_ui_design_model(), self.settings)

    def test_ui_environment_can_inherit_token_and_proxy(self):
        """UI 地址独立、令牌未设置时复用全局服务令牌，不复用上游密钥。"""
        with patch.dict(os.environ, {"MODEL_TRANSMIT_URL": "http://main.test:8100", "MODEL_TRANSMIT_TOKEN": "global",
                                     "MODEL_TRANSMIT_TRUST_ENV": "true", "UI_DESIGN_MODEL_TRANSMIT_URL": "http://ui.test:8200"}, clear=True):
            resolved = Settings.from_env().for_ui_design_model()
        self.assertEqual(resolved.model_transmit_token, "global")
        self.assertTrue(resolved.model_transmit_trust_env)

    def test_invalid_service_url_does_not_echo_embedded_secret(self):
        """地址误填密钥时明确拒绝，错误不回显输入。"""
        with patch.dict(os.environ, {"MODEL_TRANSMIT_URL": "https://user:SECRET@relay.test/v1"}, clear=True):
            with self.assertRaises(ValueError) as captured:
                Settings.from_env()
        self.assertNotIn("SECRET", str(captured.exception))

    def test_ui_generator_keeps_thinking_and_dedicated_token_budget(self):
        """不修改 UI 业务代码即可绑定独立转发和原有生成参数。"""
        from app.services.ui_design_generator import _create_ui_design_model
        ui = replace(self.settings, ui_design_model_transmit_url="http://ui.test:8200", ui_design_max_tokens=12288)
        with patch("app.services.ui_design_generator.create_chat_model") as factory:
            _create_ui_design_model(ui)
        self.assertEqual(factory.call_args.args[0].model_transmit_url, "http://ui.test:8200")
        self.assertEqual(factory.call_args.kwargs["extra_model_kwargs"], {"thinking": {"type": "disabled"}})
        factory.return_value.bind.assert_called_once_with(max_tokens=12288)
