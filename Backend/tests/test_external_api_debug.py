"""通过受控 HTTP 服务验证真实调试请求、目标约束和取消。"""
import asyncio
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.services.data_sources import mutate_catalog
from app.services.external_api_debug import DebugInput, send_debug_request


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        """回传收到的参数并固定使用错误状态码。"""
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        if self.headers.get("X-Wait"):
            self.server.received.set()
            self.server.release.wait(4)
            return
        self.send_response(422)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps([self.path, self.headers.get("Authorization"), body.decode()]).encode())

    def log_message(self, *_args):
        """测试服务不记录临时请求内容。"""


class ExternalApiDebugTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """为每个用例创建独立目录、已登记来源和真实回环服务。"""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        server.received, server.release = threading.Event(), threading.Event()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.release.set)
        self.server = server
        base = f"http://127.0.0.1:{server.server_port}/v1"
        path = "/orders/{id}"
        mutate_catalog(folder.name, action="create", source={
            "type": "external_api", "id": "src", "name": "订单", "baseUrl": base,
            "directories": [{"id": "dir", "name": "业务", "operations": [{
                "id": "op", "name": "订单", "method": "GET", "path": path,
                "pathParameters": [{"name": "id", "type": "string", "required": True}]}]}]})
        self.request = DebugInput(workspaceRoot=folder.name, sourceId="src", operationId="op",
            expectedBase=base, expectedMethod="GET", expectedPath=path,
            requestMethod="POST", requestPath=path, pathValues={"id":"a/b"},
            query={"term":"A B"}, headers={"Authorization":"Bearer test"}, body="{}", timeoutMs=3000)

    async def test_response_preserves_http_error(self):
        """路径和查询参数分别编码，业务错误响应保留正文。"""
        result = await send_debug_request(self.request)
        self.assertEqual(result["statusCode"], 422)
        self.assertEqual(json.loads(result["body"]), ["/v1/orders/a%2Fb?term=A+B", "Bearer test", "{}"])

    async def test_unsaved_request_overrides_stay_scoped_to_saved_domain(self):
        """调试未保存的方法和路径，且拒绝跨域或跳出基础路径。"""
        self.request.requestPath = "/draft/{id}"
        result = await send_debug_request(self.request)
        self.assertEqual(json.loads(result["body"])[0], "/v1/draft/a%2Fb?term=A+B")
        for invalid in ("//example.invalid/path", "/%2e%2e/other", "https://example.invalid/path"):
            self.request.requestPath = invalid
            with self.assertRaisesRegex(ValueError, "相对路径"):
                await send_debug_request(self.request)

    async def test_changed_target_not_sent(self):
        """旧详情不能向另一个地址发送凭据。"""
        self.request.expectedBase += "/other"
        with self.assertRaisesRegex(ValueError, "变更"):
            await send_debug_request(self.request)

    async def test_cancel_pending_request(self):
        """服务端已接收请求时取消等待，无须等待外部响应。"""
        self.request.headers["X-Wait"] = "1"
        task = asyncio.create_task(send_debug_request(self.request))
        self.assertTrue(await asyncio.to_thread(self.server.received.wait, 3))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
