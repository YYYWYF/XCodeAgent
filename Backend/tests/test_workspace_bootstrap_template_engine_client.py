from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx

from app.services.workspace_bootstrap.models import TemplateEngineError
from app.services.workspace_bootstrap.template_engine_client import TemplateEngineClient


class TemplateEngineClientTests(unittest.TestCase):
    """验证客户端流式下载 ZIP，不暴露 Engine 凭据到调用方。"""

    def test_downloads_zip_and_enforces_size_limit(self) -> None:
        """确认成功下载计算摘要，超限时删除临时文件并失败。"""

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "application/zip"}, content=b"zip-bytes")
        )
        factory = lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            client = TemplateEngineClient(base_url="http://engine", token="token", connect_timeout=1, read_timeout=1, max_package_bytes=100, client_factory=factory)
            result = asyncio.run(client.generate({"capabilities": {}}, temporary_dir=directory))
            self.assertEqual(result.temporary_path.read_bytes(), b"zip-bytes")
            result.temporary_path.unlink()
            limited = TemplateEngineClient(base_url="http://engine", token="token", connect_timeout=1, read_timeout=1, max_package_bytes=3, client_factory=factory)
            with self.assertRaises(TemplateEngineError):
                asyncio.run(limited.generate({"capabilities": {}}, temporary_dir=directory))
            self.assertEqual(list(Path(directory).glob("*.zip")), [])

    def test_update_logs_external_engine_call_and_no_change_response(self) -> None:
        """后端调用外部模板更新接口时必须记录请求、响应和无变更结果。"""

        def respond(request: httpx.Request) -> httpx.Response:
            """校验客户端确实向外部 Engine 的更新路径发起请求。"""

            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/v1/update")
            return httpx.Response(204)

        transport = httpx.MockTransport(respond)
        factory = lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)
        client = TemplateEngineClient(
            base_url="http://engine",
            token="token",
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
