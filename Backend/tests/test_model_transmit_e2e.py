"""通过真实本地 HTTP 验证 Studio、AG-UI 转发服务和模型 SDK 的完整合同。"""

from __future__ import annotations

import asyncio
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage

from app.agents.transmit_chat_model import TransmitChatModel
from app.services.model_transmit_client import ModelTransmitError


class MockModelHandler(BaseHTTPRequestHandler):
    """仅在回环地址模拟标准 JSON 和 SSE 上游，并记录实际收到的请求。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        """关闭 HTTP 访问日志，避免测试输出消息内容或请求凭证。"""

    def do_POST(self):
        """根据本次用户消息选择正常、工具调用、认证失败或截断响应。"""
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        record = {"path": self.path, "headers": dict(self.headers), "body": body}
        with self.server.record_lock:
            self.server.records.append(record)
        mode = body["messages"][-1]["content"]
        if mode == "fail":
            self._send(401, {"error": {"message": "mock upstream authentication rejected", "type": "authentication_error"}})
            return
        common = {"id": "chatcmpl-e2e", "created": 1, "model": body["model"]}
        usage = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
        if not body.get("stream"):
            self._send(200, {**common, "object": "chat.completion", "choices": [
                {"index": 0, "message": {"role": "assistant", "content": f"reply:{mode}"}, "finish_reason": "stop"}],
                "usage": usage})
            return
        deltas = [{"role": "assistant", "content": "你好"}, {"content": "，世界"}]
        finish = "stop"
        if mode == "tools":
            deltas = [{"role": "assistant", "tool_calls": [{"index": 0, "id": "call-e2e", "type": "function",
                       "function": {"name": "lookup", "arguments": '{"city":"'}}]},
                      {"tool_calls": [{"index": 0, "function": {"arguments": '深圳"}'}}]}]
            finish = "tool_calls"
        chunks = [{**common, "object": "chat.completion.chunk", "choices": [
            {"index": 0, "delta": delta, "finish_reason": None}]} for delta in deltas]
        if mode == "multiple":
            chunks.insert(0, {**common, "object": "chat.completion.chunk", "choices": [
                {"index": 1, "delta": {"role": "assistant", "content": "other candidate"},
                 "finish_reason": "stop"}]})
        if mode != "truncated":
            chunks.extend([{**common, "object": "chat.completion.chunk", "choices": [
                {"index": 0, "delta": {}, "finish_reason": finish}]},
                {**common, "object": "chat.completion.chunk", "choices": [], "usage": usage}])
        payload = "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks)
        payload += "data: [DONE]\n\n"
        self._send(200, payload.encode(), "text/event-stream")

    def _send(self, status, payload, content_type="application/json"):
        """在模拟上游编码响应并显式关闭连接，不编写产品 SSE 解析器。"""
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()
        self.close_connection = True


class ModelTransmitEndToEndTests(unittest.TestCase):
    """不访问真实模型或 LangSmith，使用独立子进程和临时 SQLite 观察首尾行为。"""

    def setUp(self):
        """为每项用例启动回环上游并登记资源清理。"""
        tracing = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"})
        tracing.start()
        self.addCleanup(tracing.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockModelHandler)
        self.server.daemon_threads = True
        self.server.records = []
        self.server.record_lock = threading.Lock()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.upstream_url = f"http://127.0.0.1:{self.server.server_port}/v1"

    @contextmanager
    def _relay(self, upstream="direct", spaid_source="request"):
        """使用隐藏子进程启动相邻服务，所有网络地址和凭证均由测试环境覆盖。"""
        service_root = Path(__file__).resolve().parents[3] / "code7.1_model_transmit"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "history.sqlite3"
            env = {**os.environ, "MODEL_UPSTREAM": upstream, "SERVICE_TOKEN": "e2e-relay-token",
                   "DIRECT_BASE_URL": self.upstream_url, "DIRECT_API_KEY": "e2e-direct-key",
                   "DIRECT_MODEL_NAME": "e2e-direct-model", "GATEWAY_BASE_URL": self.upstream_url,
                   "GATEWAY_API_KEY": "e2e-gateway-api-key", "GATEWAY_KEY": "e2e-gateway-key",
                   "GATEWAY_MODEL_NAME": "fast", "GATEWAY_SPAID_SOURCE": spaid_source,
                   "GATEWAY_SPAID": "fixed-user", "HISTORY_DB_PATH": str(database),
                   "UPSTREAM_TRUST_ENV": "false", "LANGSMITH_ENABLED": "false",
                   "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
                   "MOCK_ANALYTICS_ENABLED": "true", "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHONIOENCODING": "utf-8"}
            with tempfile.TemporaryFile() as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "127.0.0.1",
                     "--port", str(port), "--workers", "1", "--log-level", "error"],
                    cwd=service_root, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                try:
                    url = f"http://127.0.0.1:{port}"
                    with httpx.Client(trust_env=False, timeout=0.5) as client:
                        deadline = time.monotonic() + 45
                        while time.monotonic() < deadline:
                            if process.poll() is not None:
                                log.seek(0)
                                self.fail(f"转发子进程启动失败：{log.read().decode(errors='replace')[-3000:]}")
                            try:
                                if client.get(f"{url}/health").status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            time.sleep(0.05)
                        else:
                            log.seek(0)
                            diagnostic = log.read().decode(errors="replace")[-3000:]
                            self.fail(f"转发子进程未在限定时间内启动：{diagnostic}")
                    yield url, database
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)

    def _model(self, url, **kwargs):
        """创建真实 HTTP Studio 模型适配器，并显式关闭测试进程的远程追踪。"""
        return TransmitChatModel(transmit_url=url, transmit_token="e2e-relay-token", max_retries=0,
                                 timeout_seconds=5, max_tokens=512, trust_env=False, **kwargs)

    def _history(self, path, count):
        """等待服务完成收尾事务，再读取已完成或失败的实际历史记录。"""
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with closing(sqlite3.connect(path)) as db:
                db.row_factory = sqlite3.Row
                rows = [dict(row) for row in db.execute("SELECT * FROM model_calls ORDER BY started_at")]
            if len(rows) == count and all(row["status"] != "running" for row in rows):
                for row in rows:
                    for key in ("input_context", "model_output", "usage", "error", "analysis", "extensions"):
                        row[key] = json.loads(row[key]) if row[key] else None
                return rows
            time.sleep(0.02)
        self.fail("历史记录未在限定时间内完成落库")

    def test_direct_sync_async_sessions_and_exact_input_history(self):
        """同步及并发异步均走 direct，上下文按次保存且不同会话互不追加历史。"""
        with self._relay() as (url, database):
            model = self._model(url, extra_body={"thinking": {"type": "disabled"}})
            first = model.invoke([SystemMessage("system"), HumanMessage("first")],
                                 config={"configurable": {"thread_id": "session-a"},
                                         "metadata": {"user_id": "user-a", "project_id": "project-a"}})
            second = model.invoke("second", config={"configurable": {"thread_id": "session-a"}})

            async def concurrent_calls():
                """共享模型实例发起不同会话，验证身份不会在缓存实例中串用。"""
                return await asyncio.gather(*[model.ainvoke(name, config={"configurable": {"thread_id": name}})
                                              for name in ("session-b", "session-c")])

            results = asyncio.run(concurrent_calls())
            self.assertEqual([first.content, second.content], ["reply:first", "reply:second"])
            self.assertEqual([result.content for result in results], ["reply:session-b", "reply:session-c"])
            rows = self._history(database, 4)
            self.assertEqual({row["session_id"] for row in rows}, {"session-a", "session-b", "session-c"})
            self.assertEqual(rows[0]["input_context"]["messages"],
                             [{"role": "system", "content": "system"}, {"role": "user", "content": "first"}])
            self.assertEqual(rows[1]["input_context"]["messages"], [{"role": "user", "content": "second"}])
            self.assertEqual((rows[0]["user_id"], rows[0]["project_id"]), ("user-a", "project-a"))
            self.assertTrue(all(row["status"] == "completed" for row in rows))
            self.assertEqual(len(self.server.records), 4)
            for record in self.server.records:
                headers = {name.lower(): value for name, value in record["headers"].items()}
                self.assertEqual(record["path"], "/v1/chat/completions")
                self.assertEqual(record["body"]["model"], "e2e-direct-model")
                self.assertEqual(record["body"]["thinking"], {"type": "disabled"})
                self.assertEqual(headers["authorization"], "Bearer e2e-direct-key")
                self.assertNotIn("x-gateway-key", headers)
                self.assertNotIn("x-spaid", headers)

    def test_real_sse_text_and_tool_argument_fragments(self):
        """真实 SSE 文本和工具参数分片穿过两层协议后可完整聚合且不重复。"""
        with self._relay() as (url, database):
            model = self._model(url)
            text = list(model.stream("text", config={"configurable": {"thread_id": "text-session"}}))
            self.assertEqual("".join(chunk.content for chunk in text), "你好，世界")
            tools = [{"type": "function", "function": {"name": "lookup", "description": "查询城市",
                     "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
            chunks = list(model.bind_tools(tools, tool_choice="lookup").stream(
                "tools", config={"configurable": {"thread_id": "tool-session"}}))
            combined = sum(chunks, AIMessageChunk(content=""))
            self.assertEqual(combined.tool_calls, [{"name": "lookup", "args": {"city": "深圳"},
                                                   "id": "call-e2e", "type": "tool_call"}])
            self.assertGreaterEqual(sum(bool(chunk.tool_call_chunks) for chunk in chunks), 2)
            self.assertEqual(combined.response_metadata["finish_reason"], "tool_calls")
            self.assertEqual(combined.usage_metadata["total_tokens"], 8)
            rows = self._history(database, 2)
            self.assertEqual(rows[1]["model_output"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"],
                             '{"city":"深圳"}')
            self.assertEqual(self.server.records[1]["body"]["tools"], tools)
            self.assertEqual(self.server.records[1]["body"]["tool_choice"]["function"]["name"], "lookup")

    def test_gateway_fixed_and_request_spaid_header_contract(self):
        """两种 spaid 来源均发送独立网关凭证和对应会话、请求标识。"""
        for source in ("fixed", "request"):
            with self.subTest(source=source), self._relay("gateway", source) as (url, database):
                response = self._model(url, spaid="request-user").invoke(
                    source, config={"configurable": {"thread_id": f"gateway-{source}"}})
                self.assertEqual(response.content, f"reply:{source}")
                row = self._history(database, 1)[0]
                record = self.server.records[-1]
                headers = {name.lower(): value for name, value in record["headers"].items()}
                self.assertEqual(record["path"], "/v1/chat/completions")
                self.assertEqual(record["body"]["model"], "fast")
                self.assertEqual(headers["authorization"], "Bearer e2e-gateway-api-key")
                self.assertEqual(headers["x-gateway-key"], "e2e-gateway-key")
                self.assertEqual(headers["x-spaid"], "fixed-user" if source == "fixed" else "request-user")
                self.assertEqual(headers["x-session-id"], f"gateway-{source}")
                self.assertEqual(headers["x-request-id"], row["request_id"])
                self.assertEqual(row["upstream_type"], "gateway")

    def test_interleaved_candidates_keep_primary_output_separate(self):
        """独立到达的第二候选不会混入 Studio 首候选，历史仍保留全部产出。"""
        with self._relay() as (url, database):
            chunks = list(self._model(url).stream("multiple", n=2))
            self.assertEqual("".join(chunk.content for chunk in chunks), "你好，世界")
            row = self._history(database, 1)[0]
            self.assertEqual(row["model_output"]["choices"][1]["message"]["content"], "other candidate")
            self.assertEqual(row["input_context"]["n"], 2)

    def test_upstream_auth_failure_and_truncated_stream_are_persisted(self):
        """上游认证失败与缺少终止原因的流均明确报错，并保存失败和部分输出。"""
        with self._relay() as (url, database):
            model = self._model(url)
            with self.assertRaises(ModelTransmitError) as rejected:
                model.invoke("fail", config={"configurable": {"thread_id": "failure-session"}})
            self.assertEqual(rejected.exception.code, "UPSTREAM_AUTH_ERROR")
            self.assertEqual(rejected.exception.status_code, 401)
            received = []
            with self.assertRaises(ModelTransmitError):
                for chunk in model.stream("truncated", config={"configurable": {"thread_id": "truncated-session"}}):
                    received.append(chunk.content)
            self.assertEqual("".join(received), "你好，世界")
            rows = self._history(database, 2)
            self.assertEqual([row["status"] for row in rows], ["failed", "failed"])
            self.assertEqual(rows[0]["error"]["code"], "UPSTREAM_AUTH_ERROR")
            self.assertEqual(rows[1]["model_output"]["choices"][0]["message"]["content"], "你好，世界")
            self.assertEqual(len(self.server.records), 2)


if __name__ == "__main__":
    unittest.main()
