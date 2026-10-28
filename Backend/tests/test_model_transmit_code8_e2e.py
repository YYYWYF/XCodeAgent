"""最新 Studio 适配器通过真实回环 HTTP 对接只读 code8 服务。"""

import asyncio
import importlib
import importlib.util
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import httpx
import uvicorn

from app.agents.model_factory import create_chat_model
from app.config import Settings


def service_modules():
    """用独立包名读取相邻 code8，避免 Studio 同名 app 包污染服务导入。"""
    # 暂存验证及最终目录都使用固定的相邻工程位置，不写入服务代码或配置。
    location = Path(__file__).resolve()
    service_root = next((candidate for ancestor in location.parents for candidate in (
        ancestor / "code8_model_transmit_service", ancestor if ancestor.name == "code8_model_transmit_service" else ancestor / "__not_service__",
    ) if (candidate / "app/model_observation.py").is_file()), None)
    if service_root is None:
        raise RuntimeError("未找到相邻 code8_model_transmit_service，无法执行跨项目离线回归")
    alias = "code8_test_relay"
    if alias not in sys.modules:
        spec = importlib.util.spec_from_file_location(alias, service_root / "app/__init__.py",
                                                     submodule_search_locations=[str(service_root / "app")])
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        spec.loader.exec_module(module)
    return tuple(importlib.import_module(f"{alias}.{name}") for name in ("main", "config", "upstream"))


class Code8EndToEndTests(unittest.IsolatedAsyncioTestCase):
    """真实 SDK、HTTP、AG-UI、消息转换和 SQLite 埋点贯通验证。"""

    async def test_latest_factory_to_code8_with_exact_input_and_usage(self):
        """同步异步和流式均经 code8，前端 Run 关联保留且模型请求不冲突。"""
        main, config, upstream = service_modules()
        captured = []

        def respond(request):
            """捕获真实 SDK 请求，只在内存返回模型正文和用量。"""
            body = json.loads(request.content)
            captured.append(body)
            base = {"id": "completion-e2e", "model": "actual-upstream-model", "created": 1}
            usage = {"prompt_tokens": 284, "completion_tokens": 45, "total_tokens": 329}
            if not body.get("stream"):
                return httpx.Response(200, json={**base, "object": "chat.completion", "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}], "usage": usage})
            chunks = [
                {**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "O"}, "finish_reason": None}]},
                {**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "K"}, "finish_reason": "stop"}]},
                {**base, "object": "chat.completion.chunk", "choices": [], "usage": usage},
            ]
            content = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content.encode())

        with tempfile.TemporaryDirectory() as directory:
            service_settings = config.Settings(_env_file=None, model_upstream="direct", direct_api_key="fake-upstream-key",
                                               direct_base_url="https://offline.example.test/v1", direct_model_name="requested-model",
                                               service_token="fake-relay-token", langsmith_enabled=False,
                                               history_db_path=Path(directory) / "history.sqlite3")
            relay_app = main.create_app(service_settings, upstream.Upstream(service_settings, httpx.MockTransport(respond)))
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(relay_app, log_config=None, log_level="error", access_log=False))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            output = StringIO()
            try:
                with redirect_stdout(output):
                    thread.start()
                    deadline = time.monotonic() + 10
                    while not server.started and thread.is_alive() and time.monotonic() < deadline:
                        await asyncio.sleep(0.01)
                    self.assertTrue(server.started)
                    settings = Settings(model_transmit_url=f"http://127.0.0.1:{port}", model_transmit_token="fake-relay-token",
                                        model_max_retries=0, model_timeout_seconds=5)
                    model = create_chat_model(settings, max_tokens_override=64, extra_model_kwargs={"thinking": {"type": "disabled"}})
                    run_config = {"configurable": {"thread_id": "existing-thread"},
                                  "metadata": {"run_id": "existing-frontend-run", "project_id": "app-e2e", "workflow_scope": "conversation"}}
                    messages = [{"role": "system", "content": "You are the technical-planning model for an app-generation workflow."},
                                {"role": "user", "content": "exact-context"}]
                    sync = await asyncio.to_thread(model.invoke, messages, run_config)
                    asynchronous = await model.ainvoke(messages, run_config)
                    chunks = [chunk async for chunk in model.astream(messages, run_config)]
                    self.assertEqual(sync.content, "OK")
                    self.assertEqual(asynchronous.content, "OK")
                    self.assertEqual("".join(chunk.content for chunk in chunks), "OK")
                    self.assertEqual(sync.usage_metadata["total_tokens"], 329)
                    self.assertEqual(sum((chunk.usage_metadata or {}).get("total_tokens", 0) for chunk in chunks), 329)
                self.assertEqual(len(captured), 3)
                for body in captured:
                    self.assertEqual(body["model"], "requested-model")
                    self.assertEqual(body["messages"], messages)
                    self.assertEqual(body["max_tokens"], 64)
                    self.assertEqual(body["thinking"], {"type": "disabled"})
                summaries = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith('{"identity":')]
                self.assertEqual(len(summaries), 3)
                self.assertEqual(len({record["identity"]["run_id"] for record in summaries}), 3)
                self.assertTrue(all(record["identity"]["thread_id"] == "existing-thread" for record in summaries))
                self.assertTrue(all(record["data"]["model"]["total_tokens"] == 329 for record in summaries))
                self.assertEqual(output.getvalue().count('= "生成技术规划"'), 3)
                with relay_app.state.history.connect() as db:
                    rows = db.execute("SELECT status,extensions,analysis FROM model_calls").fetchall()
                self.assertEqual(len(rows), 3)
                for row in rows:
                    self.assertEqual(row["status"], "completed")
                    self.assertEqual(json.loads(row["extensions"])["studio_run_id"], "existing-frontend-run")
                    self.assertEqual(json.loads(row["analysis"])["execution_trace"]["data"]["model"]["total_tokens"], 329)
            finally:
                server.should_exit = True
                thread.join(timeout=5)
                listener.close()
            self.assertFalse(thread.is_alive())
