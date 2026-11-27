"""用真实回环服务验证页面静态资源、应用 API 和热更新的调试转发。"""

import asyncio
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from app.services.backend_runtime_jar import BACKEND_PROCESS_FILENAME, write_backend_process_record
from app.services.page_debug_gateway import close_page_debug_gateway, page_debug_projection
from app.services.preview_runtime_state import (
    begin_service_attempt, finish_service_attempt, read_record, record_service_stop, runtime_root,
)


class _ApplicationHandler(BaseHTTPRequestHandler):
    """模拟前端页面和可观测业务接口，保留请求方法与正文。"""

    def log_message(self, *_args: object) -> None:
        """避免测试服务输出与被测日志混淆。"""

    def do_GET(self) -> None:
        """返回前端文档，确认页面路由在 Java 未启动时可用。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<main>Page debug</main>")

    def do_POST(self) -> None:
        """记录一次真实业务调用并返回应用本身的错误响应。"""
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.calls.append((self.path, self.headers.get("Origin"), self.headers.get("Cookie"), body))
        self.send_response(422)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", "a=1; Path=/")
        self.send_header("Set-Cookie", "b=2; Path=/")
        self.end_headers()
        self.wfile.write(json.dumps({"application": self.server.server_port}).encode())


class PageDebugGatewayTests(unittest.IsolatedAsyncioTestCase):
    """验证固定入口在前后端分端启停和换端口时的行为。"""

    def setUp(self) -> None:
        """准备独立工作区进程身份，结束后回收网关与测试服务。"""
        workspace = self.enterContext(tempfile.TemporaryDirectory())
        self.workspace = Path(workspace).resolve()
        self.addCleanup(close_page_debug_gateway, self.workspace)

    def _http_service(self) -> ThreadingHTTPServer:
        """分配独立测试端口，记录业务请求次数。"""
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ApplicationHandler)
        server.calls = []
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        return server

    def _running(self, layer: str, port: int) -> None:
        """模拟启动器发布实际端口和受管进程登记。"""
        begin_service_attempt(self.workspace, layer)
        root = runtime_root(self.workspace)
        fields = {}
        if layer == "frontend":
            (root / "frontend.pid").write_text(str(os.getpid()), encoding="utf-8")
            fields["preview_url"] = f"http://127.0.0.1:{port}"
        else:
            write_backend_process_record(root / BACKEND_PROCESS_FILENAME, pid=os.getpid(),
                                         jar=root / "backend-jar" / ("a" * 32) / "app.jar")
        finish_service_attempt(self.workspace, layer, {
            "status": "running", "server": {"pid": os.getpid(), "port": port, "ready": True}, **fields,
        })

    def _projection(self) -> dict:
        """用同一工作区快照取得页面调试入口与状态。"""
        return page_debug_projection(str(self.workspace), read_record(self.workspace))

    async def test_page_renders_before_backend_and_preserves_real_responses_after_start(self) -> None:
        """未就绪时只提示，恢复后同一入口按实际 Java 端口转发一次。"""
        frontend, backend, next_backend = (self._http_service() for _ in range(3))
        self._running("frontend", frontend.server_port)
        initial = self._projection()
        origin = initial["origin"]
        async with httpx.AsyncClient(base_url=origin, trust_env=False) as client:
            page = await client.get("/articles")
            self.assertEqual(page.text, "<main>Page debug</main>")
            self.assertNotIn("backendRequestBlockedAt", self._projection())
            waiting = await client.post("/api/articles", json={"title": "new"})
            self.assertEqual(waiting.status_code, 503)
            self.assertEqual(waiting.json()["code"], "BACKEND_NOT_READY")
            first_block = self._projection()["backendRequestBlockedAt"]
            self.assertIsInstance(first_block, int)
            self.assertEqual(self._projection()["backendRequestBlockedAt"], first_block)
            with patch("app.services.page_debug_gateway.time_ns", return_value=(first_block + 1) * 1_000_000):
                self.assertEqual((await client.post("/api/articles")).status_code, 503)
            self.assertGreater(self._projection()["backendRequestBlockedAt"], first_block)
            begin_service_attempt(self.workspace, "backend")
            finish_service_attempt(self.workspace, "backend", {"status": "failed", "message": "Maven 构建失败"})
            self.assertEqual((await client.post("/api/articles")).status_code, 503)
            self.assertEqual(self._projection(), initial)
            self._running("backend", backend.server_port)
            self.assertEqual(self._projection(), initial)
            blocked = await client.post("/api/articles", headers={"Origin": "https://invalid.example"}, content="blocked")
            self.assertEqual(blocked.status_code, 403)
            self.assertEqual((await client.post("/api/%2e%2e/outside", content="blocked")).status_code, 403)
            response = await client.post("/api/articles?mode=edit%20title", headers={"Origin": origin, "Cookie": "SESSION=sample"}, content=b'{"title":"new"}')
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json(), {"application": backend.server_port})
            self.assertEqual(response.headers.get_list("set-cookie"), ["a=1; Path=/", "b=2; Path=/"])
            self.assertEqual(backend.calls, [("/api/articles?mode=edit%20title", f"http://127.0.0.1:{backend.server_port}", "SESSION=sample", b'{"title":"new"}')])
            self.assertEqual(frontend.calls, [])
            record_service_stop(self.workspace, "backend", {"status": "stopped", "message": "已停止"})
            self.assertEqual((await client.post("/api/articles")).status_code, 503)
            self._running("backend", next_backend.server_port)
            self.assertEqual(self._projection(), initial)
            self.assertEqual((await client.post("/api/articles")).json(), {"application": next_backend.server_port})
            self.assertEqual(len(backend.calls), 1)
            self.assertEqual(len(next_backend.calls), 1)

    async def test_development_websocket_stays_on_frontend(self) -> None:
        """通过调试入口转发调试页的开发服务器 WebSocket 和子协议。"""
        async def echo(client) -> None:
            """回显开发服务器收发的文本帧。"""
            await client.send(await client.recv())

        async with serve(echo, "127.0.0.1", 0, subprotocols=["hmr"]) as frontend:
            self._running("frontend", frontend.sockets[0].getsockname()[1])
            origin = self._projection()["origin"]
            async with connect(origin.replace("http:", "ws:") + "/hmr", origin=origin, subprotocols=["hmr"], proxy=None) as page:
                self.assertEqual(page.subprotocol, "hmr")
                await page.send("hot update")
                self.assertEqual(await asyncio.wait_for(page.recv(), 3), "hot update")
