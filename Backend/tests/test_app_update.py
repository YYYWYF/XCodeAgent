"""桌面更新复用模板服务配置及完整 AG-UI 生命周期的回归。"""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import httpx

from app.config import Settings
from app.protocols.app_update import build_app_update_ag_ui_stream, app_update_capabilities
from app.services.app_update import AppUpdateCheckError, AppUpdateRelease, check_app_update


class AppUpdateTests(unittest.IsolatedAsyncioTestCase):
    """不启动模型或工作区，验证配置、外部请求及 AG-UI 失败收口。"""

    def setUp(self) -> None:
        """为每个用例建立独立模板服务配置及版本响应。"""

        self.settings = Settings(
            model_base_url="", model_api_key="", model_name="test",
            template_engine_base_url="http://engine.example/company/template/",
        )
        self.release = {
            "updateAvailable": True,
            "latestAppVersion": "1.0.1",
            "appDownloadUrl": "http://cdn.example/",
        }

    async def test_uses_configured_engine_base_and_only_sends_version(self) -> None:
        """复用 Settings 地址，保留路径前缀，且不采用模板 ZIP 的长超时。"""

        def respond(request: httpx.Request) -> httpx.Response:
            """核对发往 Java 的最小请求与正确路径。"""

            self.assertEqual(str(request.url), "http://engine.example/company/template/v1/app-updates/check")
            self.assertEqual(request.method, "POST")
            self.assertEqual(json.loads(request.content), {"currentVersion": "1.0.0"})
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(200, json=self.release)

        def factory(**kwargs: object) -> httpx.AsyncClient:
            """通过可注入传输核对更新专用连接设置。"""

            self.assertEqual(kwargs["trust_env"], False)
            self.assertEqual(kwargs["timeout"].read, 5.0)
            return httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs)

        result = await check_app_update(self.settings, "1.0.0", client_factory=factory)
        self.assertEqual(result.model_dump(by_alias=True), self.release)

    async def test_settings_from_environment_is_the_single_address_source(self) -> None:
        """环境配置切换后使用新的模板服务地址，不依赖前端构建配置。"""

        with patch.dict("os.environ", {"DEVAGENTSTUDIO_TEMPLATE_ENGINE_BASE_URL": "https://other-engine.example"}):
            settings = Settings.from_env()
        self.assertEqual(settings.template_engine_base_url, "https://other-engine.example")
        urls: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            """记录实际请求，以证明更新使用同一份 Settings。"""

            urls.append(str(request.url))
            return httpx.Response(200, json={**self.release, "updateAvailable": False, "appDownloadUrl": None})

        result = await check_app_update(settings, "1.0.1", client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(respond), **kw))
        self.assertFalse(result.update_available)
        self.assertEqual(urls, ["https://other-engine.example/v1/app-updates/check"])

    async def test_invalid_configuration_has_no_fallback_address(self) -> None:
        """缺失和非法配置直接失败，禁止偷偷访问默认 Java 地址。"""

        for address in ["", "/relative", "file:///tmp/config", "http://user:secret@engine"]:
            with self.subTest(address=address), self.assertRaises(AppUpdateCheckError):
                await check_app_update(replace(self.settings, template_engine_base_url=address), "1.0.0")

    async def test_http_errors_and_invalid_responses_are_sanitized(self) -> None:
        """Java 错误、异常字段和错误 CDN 不应把响应原文泄漏到 AG-UI。"""

        for status, payload in [
            (503, {"secret": "private-response"}),
            (200, {**self.release, "updateAvailable": "true"}),
            (200, {**self.release, "appDownloadUrl": "file:///private-response"}),
            (200, {**self.release, "updateAvailable": False}),
            (200, {**self.release, "extra": "private-response"}),
        ]:
            with self.subTest(status=status, payload=payload):
                factory = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(lambda _req: httpx.Response(status, json=payload)), **kw)
                with self.assertRaises(AppUpdateCheckError) as caught:
                    await check_app_update(self.settings, "1.0.0", client_factory=factory)
                self.assertNotIn("private-response", str(caught.exception))

    async def test_total_timeout_cancels_the_upstream_request(self) -> None:
        """分块读取也受到总时限约束，取消后不继续等待 Java。"""

        cancelled = asyncio.Event()

        async def respond(_request: httpx.Request) -> httpx.Response:
            """模拟长时间不返回的 Java 检测。"""

            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
            return httpx.Response(200, json=self.release)

        factory = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(respond), **kw)
        with patch("app.services.app_update.APP_UPDATE_TIMEOUT_SECONDS", 0.01):
            with self.assertRaisesRegex(AppUpdateCheckError, "超时"):
                await check_app_update(self.settings, "1.0.0", client_factory=factory)
        self.assertTrue(cancelled.is_set())

    async def test_transport_errors_and_oversized_responses(self) -> None:
        """连接失败和超大响应均转换为有界、脱敏失败。"""

        def offline(request: httpx.Request) -> httpx.Response:
            """模拟含内部地址的传输错误。"""

            raise httpx.ConnectError("private-response", request=request)

        for transport in [httpx.MockTransport(offline), httpx.MockTransport(lambda _req: httpx.Response(200, content=b"x" * 65537))]:
            factory = lambda **kw: httpx.AsyncClient(transport=transport, **kw)
            with self.assertRaises(AppUpdateCheckError) as caught:
                await check_app_update(self.settings, "1.0.0", client_factory=factory)
            self.assertNotIn("private-response", str(caught.exception))

    async def test_success_and_failure_both_finish_ag_ui_lifecycle(self) -> None:
        """独立检测不登记工作区，成功和上游失败都带状态快照与 RunFinished。"""

        for failed in [False, True]:
            operation = AsyncMock(return_value=AppUpdateRelease.model_validate(self.release))
            if failed:
                operation.side_effect = AppUpdateCheckError("检测失败")
            with patch("app.protocols.app_update.check_app_update", operation), patch("app.protocols.ag_ui_action_stream.workflow_run_registry.register") as register:
                events = await self.read_events({"action": "check", "currentVersion": "1.0.0"})
            self.assertEqual(events[0]["type"], "RUN_STARTED")
            self.assertEqual(events[-1]["type"], "RUN_FINISHED")
            self.assertTrue(any(event["type"] == "STATE_SNAPSHOT" for event in events))
            result = next(event["value"] for event in events if event["type"] == "CUSTOM")
            self.assertEqual(result["status"], "failed" if failed else "completed")
            self.assertEqual(events[-1]["result"]["appUpdate"], result)
            operation.assert_awaited_once_with(self.settings, "1.0.0")
            register.assert_not_called()

    async def test_invalid_inputs_do_not_call_java(self) -> None:
        """拒绝客户端注入服务地址、缺失字段及标量强制转换。"""

        for raw in [None, {}, {"action": "check", "currentVersion": 1},
                    {"action": "check", "currentVersion": "1.0.0", "baseUrl": "http://attacker"}]:
            with patch("app.protocols.app_update.check_app_update", AsyncMock()) as operation:
                events = await self.read_events(raw)
            self.assertEqual(events[-1]["type"], "RUN_FINISHED")
            self.assertEqual(events[-1]["result"]["appUpdate"]["status"], "failed")
            operation.assert_not_awaited()
        self.assertNotIn("template_engine_base_url", app_update_capabilities())

    async def read_events(self, raw: object) -> list[dict[str, object]]:
        """收集协议输出，断言使用生产编码器生成的真实 AG-UI 帧。"""

        stream = build_app_update_ag_ui_stream(settings=self.settings, payload={
            "threadId": "update-thread", "runId": "update-run",
            "forwardedProps": {"appUpdate": raw, "workspaceRoot": "/not-a-workspace"},
        })
        return [json.loads(line[6:]) async for chunk in stream for line in chunk.splitlines() if line.startswith("data: ")]
