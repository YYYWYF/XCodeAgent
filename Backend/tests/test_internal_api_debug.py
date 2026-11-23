"""验证正式内部接口调试的请求定义、运行实例及 AG-UI 响应。"""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app.routes.internal_api_debug import run_internal_api_debug
from app.services.application_lifecycle import application_lifecycle_path
from app.services.internal_api_debug import DescribeInternalDebug, SendInternalDebug, describe_internal_debug, send_internal_debug


class ApplicationHandler(BaseHTTPRequestHandler):
    def do_PATCH(self) -> None:
        """返回生成业务接口收到的字段，以验证真实转发。"""
        payload = self.rfile.read(int(self.headers.get("content-length", "0"))).decode()
        self.server.calls.append((self.path, self.headers.get("Cookie"), payload))
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"message":"forbidden"}')

    def log_message(self, *_args) -> None:
        """测试凭据和请求内容不进入终端日志。"""


class InternalApiDebugTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        """创建当前迭代的正式接口、完成状态和隔离的 HTTP 服务。"""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.lifecycle_path = application_lifecycle_path(root)
        self.lifecycle_path.parent.mkdir(parents=True)
        self.lifecycle = {
            "application": {"id": "posts", "name": "文章"},
            "revision": 1, "updatedAt": "2026-01-01T00:00:00Z",
            "initialization": {"stage": "ready_for_workbench", "status": "completed", "iterationId": "iteration"},
            "developmentArtifacts": {"endpoints": {"posts": {"update": {
                "initialDevelopmentStatus": "completed", "changeStatus": "unchanged",
                "completedAt": "2026-01-01T00:00:00Z", "completedRunId": "run", "completedThreadId": "thread"
            }}}}
        }
        self.save_lifecycle()
        plans = self.lifecycle_path.parent / "plans"
        plans.mkdir()
        (plans / "technical-plan.json").write_text(json.dumps({
            "confirmation_status": "confirmed",
            "api_contracts": [{
                "id": "posts",
                "schemas": {"UpdatePost": {"type": "object", "properties": {"title": {"type": "string"}}}},
                "endpoints": [{
                    "id": "update", "method": "PATCH", "path": "/api/posts/{id}",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}},
                        {"name": "mode", "in": "query", "required": False, "schema": {"type": "string"}},
                    ],
                    "request_schema_ref": "#/schemas/UpdatePost",
                }]
            }]
        }), encoding="utf-8")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ApplicationHandler)
        self.server.calls = []
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.runtime = {
            "attemptId": "current", "status": "failed", "frontend": {"status": "failed"},
            "backend": {"status": "running", "port": self.server.server_port},
        }
        self.runtime_read = self.enterContext(patch(
            "app.services.internal_api_debug.runtime_snapshot", return_value=self.runtime,
        ))
        self.target = {"workspaceRoot": str(root), "iterationId": "iteration", "apiContractId": "posts", "endpointId": "update"}
        self.request = SendInternalDebug(
            action="send", **self.target, attemptId="current",
            expectedMethod="PATCH", expectedPath="/api/posts/{id}",
            pathValues={"id": "7"}, query={"mode": "edit title"}, headers={"Cookie": "SESSION=test-only"},
            body='{"title":"draft"}', timeoutMs=3000,
        )

    def save_lifecycle(self) -> None:
        """保存当前测试迭代的正式状态。"""
        self.lifecycle_path.write_text(json.dumps(self.lifecycle), encoding="utf-8")

    def test_describe_returns_formal_parameters_and_template_without_mutation(self) -> None:
        """描述复用正式类型和正文结构，读取过程不改写生命周期。"""
        before = self.lifecycle_path.read_bytes()
        result = describe_internal_debug(DescribeInternalDebug(action="describe", **self.target))
        self.assertEqual(result["method"], "PATCH")
        self.assertEqual(result["pathNames"], ["id"])
        self.assertEqual(result["parameters"], [
            {"location": "path", "name": "id", "type": "integer", "required": True},
            {"location": "query", "name": "mode", "type": "string", "required": False},
        ])
        self.assertEqual(json.loads(result["body"]), {"title": ""})
        self.assertEqual(self.lifecycle_path.read_bytes(), before)
        self.runtime_read.assert_not_called()

    async def test_sends_once_to_ready_backend_and_preserves_business_failure(self) -> None:
        """前端预览失败仍能调用已就绪后端，并返回实际状态和正文。"""
        result = await send_internal_debug(self.request)
        self.assertEqual(self.server.calls, [("/api/posts/7?mode=edit+title", "SESSION=test-only", '{"title":"draft"}')])
        self.assertEqual(result["statusCode"], 403)
        self.assertEqual(json.loads(result["body"]), {"message": "forbidden"})
        self.assertEqual(result["headers"]["content-type"], "application/json")

    async def test_outdated_targets_and_stopped_backend_do_not_send(self) -> None:
        """迭代、接口定义或运行轮次失效时禁止把草稿发送到其他实例。"""
        for fields, message in (
            ({"iterationId": "different"}, "迭代已变化"),
            ({"expectedMethod": "GET"}, "接口定义已变化"),
            ({"attemptId": "previous"}, "运行实例已变化"),
        ):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, message):
                await send_internal_debug(self.request.model_copy(update=fields))
        self.runtime["backend"]["status"] = "stopped"
        with self.assertRaisesRegex(ValueError, "后端服务尚未就绪"):
            await send_internal_debug(self.request)
        self.lifecycle["developmentArtifacts"]["endpoints"]["posts"]["update"]["changeStatus"] = "changed"
        self.save_lifecycle()
        with self.assertRaisesRegex(ValueError, "尚未按当前契约完成"):
            describe_internal_debug(DescribeInternalDebug(action="describe", **self.target))
        self.assertEqual(self.server.calls, [])

    async def test_ag_ui_stream_exposes_success_and_validation_failure(self) -> None:
        """两种结果均通过同一调试快照和 AG-UI 生命周期返回。"""
        for action, key in (("describe", "endpoint"), ("unknown", "error")):
            with self.subTest(action=action):
                response = await run_internal_api_debug({
                    "threadId": "internal-debug-thread", "runId": f"internal-debug-{action}", "messages": [],
                    "state": {}, "tools": [], "context": [],
                    "forwardedProps": {"internalApiDebug": {"action": action, **self.target}}
                }, "text/event-stream")
                chunks = [chunk async for chunk in response.body_iterator]
                event_stream = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
                events = [json.loads(line[6:]) for line in event_stream.splitlines() if line.startswith("data: ")]
                self.assertEqual(events[0]["type"], "RUN_STARTED")
                self.assertEqual(events[-1]["type"], "RUN_FINISHED")
                snapshot = next(event["snapshot"] for event in events if event["type"] == "STATE_SNAPSHOT")
                self.assertIn(key, snapshot["internalApiDebug"])
