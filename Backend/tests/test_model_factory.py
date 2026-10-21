"""验证调用预算与参数隔离，不建立模型网络连接。"""

from copy import deepcopy
from dataclasses import asdict, replace
import unittest
from unittest.mock import patch

from app.agents.model_factory import create_chat_model
from app.config import Settings


class ModelFactoryOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        """隔离转发模型构造，避免测试调用真实服务。"""
        self.settings = Settings(model_transmit_url="http://transmit.test", model_transmit_token="test-token",
                                 default_max_tokens=2048, model_max_retries=2, default_temperature=0.4)
        self.factory = self.enterContext(patch("app.agents.model_factory.TransmitChatModel"))
        self.handler = self.enterContext(patch("app.agents.model_factory.ModelOutputLogHandler"))

    def test_default_factory_uses_only_transmit_connection(self) -> None:
        """默认构造只持有转发连接，不创建真实厂商客户端。"""
        self.assertIs(create_chat_model(self.settings), self.factory.return_value)
        self.factory.assert_called_once_with(
            transmit_url="http://transmit.test", transmit_token="test-token", trust_env=False, spaid="",
            temperature=0.4, max_tokens=2048, max_retries=2, timeout_seconds=120.0,
            extra_body=None, callbacks=None,
        )

    def test_concurrent_model_budgets_do_not_modify_global_settings(self) -> None:
        """不同任务可分别覆盖预算，零重试正确保留且不污染全局配置。"""
        original = asdict(self.settings)
        budgets = [(4096, 0, 45.0), (2048, 2, 120.0), (8192, 1, 90.0)]
        create_chat_model(self.settings, max_tokens_override=4096, max_retries_override=0, timeout_seconds_override=45)
        create_chat_model(self.settings)
        create_chat_model(self.settings, max_tokens_override=8192, max_retries_override=1, timeout_seconds_override=90)
        for call, expected in zip(self.factory.call_args_list, budgets):
            self.assertEqual((call.kwargs["max_tokens"], call.kwargs["max_retries"], call.kwargs["timeout_seconds"]), expected)
        self.assertEqual(asdict(self.settings), original)

    def test_extensions_are_deep_copied_and_logging_is_preserved(self) -> None:
        """thinking 扩展与日志回调保留，嵌套参数不会被其他模型实例修改。"""
        extras = {"thinking": {"type": "disabled"}}
        original = deepcopy(extras)
        create_chat_model(replace(self.settings, model_output_log_enabled=True), extra_model_kwargs=extras)
        kwargs = self.factory.call_args.kwargs
        self.assertEqual(kwargs["callbacks"], [self.handler.return_value])
        kwargs["extra_body"]["thinking"]["type"] = "changed"
        self.assertEqual(extras, original)


if __name__ == "__main__":
    unittest.main()
