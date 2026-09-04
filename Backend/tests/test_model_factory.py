"""T1.4 factory 调用级 override 与全局模型配置隔离测试。"""

from copy import deepcopy
from dataclasses import asdict, replace
import unittest
from unittest.mock import Mock, patch

import httpx

from app.agents.model_factory import create_chat_model
from app.config import Settings


class ModelFactoryOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        """隔离模型和 HTTP 客户端构造，不建立网络连接或留下客户端资源。"""

        self.settings = Settings(
            model_base_url="https://example.com/v1", model_api_key="test-key",
            model_name="test-model [display]", default_temperature=0.4,
            default_max_tokens=2048, model_max_retries=2, model_timeout_seconds=120.0,
            model_trust_env=True,
        )
        self.model_factory = self.enterContext(patch("app.agents.model_factory.ChatOpenAI"))
        self.sync_factory = self.enterContext(patch("app.agents.model_factory.httpx.Client"))
        self.async_factory = self.enterContext(patch("app.agents.model_factory.httpx.AsyncClient"))
        self.handler_factory = self.enterContext(patch("app.agents.model_factory.ModelOutputLogHandler"))

    def _assert_client_timeouts(self, expected: float) -> None:
        """检查同步和异步客户端的读写等待预算一致，并保留旧连接超时策略。"""

        for factory in (self.sync_factory, self.async_factory):
            self.assertEqual(factory.call_args.kwargs["trust_env"], self.settings.model_trust_env)
            timeout = factory.call_args.kwargs["timeout"]
            self.assertEqual(timeout.as_dict(), {
                "connect": 30.0, "read": expected, "write": expected, "pool": expected,
            })

    def test_no_override_preserves_all_existing_constructor_arguments(self) -> None:
        """无 override 时保留全局参数、鉴权、HTTP 客户端、日志和 extra_body 旧行为。"""

        result = create_chat_model(self.settings)
        self.assertIs(result, self.model_factory.return_value)
        self.model_factory.assert_called_once_with(
            model="test-model", base_url="https://example.com/v1", api_key="test-key",
            temperature=0.4, max_tokens=2048, timeout=120.0, max_retries=2,
            http_client=self.sync_factory.return_value, http_async_client=self.async_factory.return_value,
            streaming=False, callbacks=None, model_kwargs={},
        )
        self.sync_factory.assert_called_once_with(trust_env=True, timeout=httpx.Timeout(120.0, connect=30.0))
        self.async_factory.assert_called_once_with(trust_env=True, timeout=httpx.Timeout(120.0, connect=30.0))
        self.handler_factory.assert_not_called()

    def test_explicit_none_matches_omitted_overrides(self) -> None:
        """显式 None 和省略 override 得到完全相同的模型与客户端参数。"""

        create_chat_model(self.settings)
        original = dict(self.model_factory.call_args.kwargs)
        original_timeout = self.sync_factory.call_args.kwargs["timeout"].as_dict()
        create_chat_model(self.settings, max_tokens_override=None, max_retries_override=None, timeout_seconds_override=None)
        self.assertEqual(self.model_factory.call_args.kwargs, original)
        self.assertEqual(self.sync_factory.call_args.kwargs["timeout"].as_dict(), original_timeout)
        self._assert_client_timeouts(120.0)

    def test_max_tokens_override_does_not_override_retry_or_timeout(self) -> None:
        """只覆盖 token budget，不改变重试、timeout 或 Settings。"""

        before = asdict(self.settings)
        create_chat_model(self.settings, max_tokens_override=4096)
        kwargs = self.model_factory.call_args.kwargs
        self.assertEqual((kwargs["max_tokens"], kwargs["max_retries"], kwargs["timeout"]), (4096, 2, 120.0))
        self.assertEqual(asdict(self.settings), before)

    def test_zero_retry_override_is_not_replaced_by_global_default(self) -> None:
        """retry=0 必须显式传到 SDK，不能因假值判断回退为全局重试次数。"""

        create_chat_model(self.settings, max_retries_override=0)
        kwargs = self.model_factory.call_args.kwargs
        self.assertEqual(kwargs["max_retries"], 0)
        self.assertEqual(kwargs["max_tokens"], 2048)
        self.assertEqual(kwargs["timeout"], 120.0)
        self.assertEqual(self.settings.model_max_retries, 2)

    def test_timeout_override_reaches_model_and_both_http_clients(self) -> None:
        """不同独立 timeout 同时进入模型、同步与异步客户端。"""

        for timeout in (8.5, 240.0):
            with self.subTest(timeout=timeout):
                create_chat_model(self.settings, timeout_seconds_override=timeout)
                self.assertEqual(self.model_factory.call_args.kwargs["timeout"], timeout)
                self.assertEqual(self.model_factory.call_args.kwargs["max_retries"], 2)
                self._assert_client_timeouts(timeout)
        self.assertEqual(self.settings.model_timeout_seconds, 120.0)

    def test_dag_then_normal_then_another_override_do_not_share_configuration(self) -> None:
        """同一 Settings 连续创建不同配置的模型和客户端，不污染前后调用。"""

        before = asdict(self.settings)
        models = [Mock(name=f"model-{index}") for index in range(3)]
        clients = [Mock(name=f"client-{index}") for index in range(3)]
        async_clients = [Mock(name=f"async-client-{index}") for index in range(3)]
        self.model_factory.side_effect = models
        self.sync_factory.side_effect = clients
        self.async_factory.side_effect = async_clients
        first = create_chat_model(self.settings, max_tokens_override=4096, max_retries_override=0, timeout_seconds_override=45.0)
        second = create_chat_model(self.settings)
        third = create_chat_model(self.settings, max_tokens_override=8192, max_retries_override=1, timeout_seconds_override=90.0)
        self.assertEqual([first, second, third], models)
        expected = [(4096, 0, 45.0), (2048, 2, 120.0), (8192, 1, 90.0)]
        for index, call in enumerate(self.model_factory.call_args_list):
            kwargs = call.kwargs
            self.assertEqual((kwargs["max_tokens"], kwargs["max_retries"], kwargs["timeout"]), expected[index])
            self.assertIs(kwargs["http_client"], clients[index])
            self.assertIs(kwargs["http_async_client"], async_clients[index])
            for factory in (self.sync_factory, self.async_factory):
                self.assertEqual(factory.call_args_list[index].kwargs["timeout"].read, expected[index][2])
        self.assertEqual(asdict(self.settings), before)

    def test_extra_model_kwargs_and_logging_coexist_with_overrides(self) -> None:
        """调用级覆盖保留 extra_body 包装与日志回调，不修改额外参数输入。"""

        extras = {"thinking": {"type": "disabled"}, "reasoning_effort": "none"}
        before = deepcopy(extras)
        create_chat_model(replace(self.settings, model_output_log_enabled=True),
            max_tokens_override=4096, max_retries_override=0, timeout_seconds_override=60.0,
            extra_model_kwargs=extras)
        kwargs = self.model_factory.call_args.kwargs
        self.assertEqual(kwargs["model_kwargs"], {"extra_body": extras})
        self.assertIsNot(kwargs["model_kwargs"]["extra_body"], extras)
        self.assertTrue(kwargs["streaming"])
        self.assertEqual(kwargs["callbacks"], [self.handler_factory.return_value])
        self.assertEqual(extras, before)
        create_chat_model(self.settings)
        self.assertEqual(self.model_factory.call_args.kwargs["model_kwargs"], {})
        self.assertIsNone(self.model_factory.call_args.kwargs["callbacks"])

    def test_missing_api_key_still_fails_before_constructing_clients(self) -> None:
        """增加 override 后仍保留原有缺失凭据校验，不创建无效客户端。"""

        with self.assertRaisesRegex(RuntimeError, "Missing model API key"):
            create_chat_model(replace(self.settings, model_api_key=""), max_retries_override=0)
        self.model_factory.assert_not_called()
        self.sync_factory.assert_not_called()
        self.async_factory.assert_not_called()
