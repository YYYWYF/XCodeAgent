from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from app.services.workspace_bootstrap.models import TemplateEngineError
from app.services.template_reconcile.protocol_v3 import TemplateStateV3
from app.services.workspace_bootstrap.template_engine_client import (
    TemplateEngineClient,
    _build_fallback_template_state,
)
from app.services.workspace_bootstrap.run_log import TemplateRunLog


class TemplateEngineClientTests(unittest.TestCase):
    """验证客户端流式下载 ZIP，且不发送已移除的静态认证头。"""

    def test_downloads_zip_and_enforces_size_limit(self) -> None:
        """确认成功下载计算摘要，超限时删除临时文件并失败。"""

        def respond(request: httpx.Request) -> httpx.Response:
            """验证首次生成请求只携带当前协议需要的内容协商头。"""

            self.assertEqual("POST", request.method)
            self.assertEqual("/v1/generate", request.url.path)
            self.assertEqual("application/zip", request.headers["accept"])
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(
                200,
                headers={"content-type": "application/zip"},
                content=b"zip-bytes",
            )

        transport = httpx.MockTransport(respond)
        factory = lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            client = TemplateEngineClient(base_url="http://engine", connect_timeout=1, read_timeout=1, max_package_bytes=100, client_factory=factory)
            result = asyncio.run(client.generate({"capabilities": {}}, temporary_dir=directory))
            self.assertEqual(result.temporary_path.read_bytes(), b"zip-bytes")
            result.temporary_path.unlink()
            limited = TemplateEngineClient(base_url="http://engine", connect_timeout=1, read_timeout=1, max_package_bytes=3, client_factory=factory)
            with self.assertRaises(TemplateEngineError):
                asyncio.run(limited.generate({"capabilities": {}}, temporary_dir=directory))
            self.assertEqual(list(Path(directory).glob("*.zip")), [])

    def test_generate_records_http_status_and_keeps_request_failure_log(self) -> None:
        """模板引擎返回 HTTP 错误时，独立日志记录状态码并保留失败堆栈。"""

        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                503,
                json={"message": "temporarily unavailable", "password": "engine-secret"},
            )
        )
        factory = lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            run_log = TemplateRunLog.create(workspace)
            client = TemplateEngineClient(
                base_url="https://user-secret:pass-secret@example.test?token=url-secret",
                connect_timeout=1,
                read_timeout=1,
                max_package_bytes=100,
                client_factory=factory,
            )
            try:
                asyncio.run(client.generate({"capabilities": {}}, run_log=run_log))
            except TemplateEngineError as exc:
                run_log.finish_failure(exc)
            else:
                self.fail("expected the Template Engine HTTP failure")

            content = run_log.path.read_text(encoding="utf-8")
            self.assertIn("httpStatus=503", content)
            self.assertIn('event="stage_failed"', content)
            for secret in ("user-secret", "pass-secret", "url-secret", "engine-secret"):
                self.assertNotIn(secret, content)

    def test_generate_logs_connection_timeout_type(self) -> None:
        """网络连接超时日志包含具体异常类型并最终保留本次运行文件。"""

        def timeout(_request: httpx.Request) -> httpx.Response:
            """注入连接超时，验证客户端的异常分类日志。"""

            raise httpx.ConnectTimeout("engine connection timed out")

        transport = httpx.MockTransport(timeout)
        factory = lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            run_log = TemplateRunLog.create(workspace)
            client = TemplateEngineClient(
                base_url="http://engine.invalid",
                connect_timeout=1,
                read_timeout=1,
                max_package_bytes=100,
                client_factory=factory,
            )
            try:
                asyncio.run(client.generate({"capabilities": {}}, run_log=run_log))
            except TemplateEngineError as exc:
                run_log.finish_failure(exc)
            else:
                self.fail("expected the Template Engine connection timeout")

            content = run_log.path.read_text(encoding="utf-8")
            self.assertIn('exceptionType="ConnectTimeout"', content)
            self.assertIn('stage="template_request"', content)
            self.assertIn("Traceback (most recent call last)", content)

    def test_update_logs_external_engine_call_and_no_change_response(self) -> None:
        """后端调用外部模板更新接口时必须记录请求、响应和无变更结果。"""

        def respond(request: httpx.Request) -> httpx.Response:
            """校验客户端确实向外部 Engine 的更新路径发起请求。"""

            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/v1/update")
            self.assertEqual(request.headers["accept"], "application/zip")
            self.assertNotIn("authorization", request.headers)
            self.assertEqual(
                json.loads(request.content),
                {
                    "protocolVersion": "3",
                    "currentTemplateState": {"templateRevision": "v1"},
                    "requestedConfig": {"capabilities": {}},
                    "mode": "APPLY",
                },
            )
            return httpx.Response(204)

        transport = httpx.MockTransport(respond)
        factory = lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)
        client = TemplateEngineClient(
            base_url="http://engine",
            connect_timeout=1,
            read_timeout=1,
            max_package_bytes=100,
            client_factory=factory,
        )

        with self.assertLogs("uvicorn.error", level="INFO") as logs:
            result = asyncio.run(
                client.update(
                    {"templateRevision": "v1"},
                    {"capabilities": {}},
                )
            )

        self.assertIsNone(result)
        rendered = "\n".join(logs.output)
        self.assertIn("模板更新请求已发起", rendered)
        self.assertIn("模板更新接口已响应", rendered)
        self.assertIn("模板更新接口返回无变更", rendered)

    def test_update_preserves_engine_error_json(self) -> None:
        """确认流式 Engine 失败 body 先读取再解析，不会被 ResponseNotRead 或通用 HTTP 错误覆盖。"""

        transport = httpx.MockTransport(
            lambda _request: httpx.Response(409, json={"code": "RECONCILE_STATE_CHANGE_REQUIRED", "message": "必须使用 APPLY", "details": {"currentRevision": "v1"}, "traceId": "trace-123"})
        )
        factory = lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)
        client = TemplateEngineClient(base_url="http://engine", connect_timeout=1, read_timeout=1, max_package_bytes=100, client_factory=factory)

        with self.assertRaises(TemplateEngineError) as raised:
            asyncio.run(client.update({"templateRevision": "v1"}, {"capabilities": {}}))

        self.assertEqual(
            {"code": "RECONCILE_STATE_CHANGE_REQUIRED", "message": "必须使用 APPLY", "details": {"currentRevision": "v1"}, "traceId": "trace-123"},
            raised.exception.engine_error,
        )
        self.assertEqual(409, raised.exception.http_status)
        self.assertIn('"traceId":"trace-123"', str(raised.exception))

    def test_git_fallback_state_matches_current_v3_contract(self) -> None:
        """确认 Git fallback 不会重新产出已删除的 V2 TemplateState。"""

        state = _build_fallback_template_state(
            {
                "capabilities": {
                    "login": {"enabled": True, "config": {}},
                    "authorization": {"enabled": False, "config": {}},
                }
            }
        )

        validated = TemplateStateV3.model_validate(state)

        self.assertEqual(3, validated.schemaVersion)
        self.assertEqual({"login"}, set(validated.requested))
        self.assertEqual({}, validated.installedArtifacts)
        self.assertEqual({}, validated.managedContributions)
