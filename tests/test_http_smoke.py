"""真实回环 HTTP 烟测：仅上游采用内存模拟，不访问企业服务。"""

import json
import socket
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import httpx
import uvicorn
from httpx_sse import connect_sse

from app.main import create_app
from app.upstream import Upstream
from tests.helpers import chunk, envelope, settings, sse_response


class HttpSmokeTests(unittest.TestCase):
    """验证实际启动、健康接口、HTTP SSE 与单行观测输出。"""

    def test_health_and_inference_over_real_local_http(self):
        """使用系统分配的空闲端口，结束时回收服务线程和监听套接字。"""
        with tempfile.TemporaryDirectory() as directory:
            config = settings(Path(directory))

            def handler(request):
                """模拟最终内容帧之后的独立用量尾帧。"""
                tail = chunk(usage={"prompt_tokens": 284, "completion_tokens": 45, "total_tokens": 329})
                tail["choices"] = []
                return sse_response([chunk({"content": "OK"}), chunk(finish_reason="stop"), tail])

            app = create_app(config, Upstream(config, httpx.MockTransport(handler)))
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_config=None, log_level="error", access_log=False))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            output = StringIO()
            try:
                with redirect_stdout(output):
                    thread.start()
                    deadline = time.monotonic() + 10
                    while not server.started and thread.is_alive() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(server.started, "独立 HTTP 服务未就绪")
                    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False) as client:
                        self.assertEqual(client.get("/health").json()["status"], "ok")
                        raw = envelope("http-smoke-run", metadata={"extensions": {"current_operation": "HTTP smoke test"}})
                        with connect_sse(client, "POST", "/model-inference/run", json=raw,
                                         headers={"Authorization": "Bearer local-model-transmit-dev-token"}) as source:
                            source.response.raise_for_status()
                            events = [json.loads(item.data) for item in source.iter_sse()]
                    self.assertTrue(app.state.observation_output.drain(timeout=5))
                self.assertEqual(events[-1]["type"], "RUN_FINISHED")
                lines = output.getvalue().splitlines()
                self.assertEqual(len(lines), 1)
                record = json.loads(lines[0])
                self.assertEqual(record["identity"]["run_id"], "http-smoke-run")
                self.assertEqual(record["data"]["model"]["total_tokens"], 329)
                self.assertEqual(record["identity"]["current_operation"], "http_smoke_test")
                self.assertTrue(record["data"]["started_at"].endswith("+08:00"))
                self.assertTrue(record["data"]["ended_at"].endswith("+08:00"))
            finally:
                server.should_exit = True
                if thread.ident is not None:
                    thread.join(timeout=5)
                listener.close()
            self.assertFalse(thread.is_alive(), "烟测服务没有正常回收")
