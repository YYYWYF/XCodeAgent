"""启动完整 Studio 后端并通过真实 HTTP 验证健康，用户目录使用临时隔离路径。"""

import importlib
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn


class BackendHealthTests(unittest.TestCase):
    """健康检查不依赖真实模型和原有用户密钥，不启动前端。"""

    def test_latest_backend_lifespan_and_health_over_http(self):
        """执行真实 lifespan，包括临时数据库密钥生成，服务退出后正常回收。"""
        with tempfile.TemporaryDirectory() as directory, patch("pathlib.Path.home", return_value=Path(directory)):
            main = importlib.import_module("app.main")
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(main.app, log_config=None, log_level="error", access_log=False))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            try:
                thread.start()
                deadline = time.monotonic() + 15
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(server.started, "Studio 后端未就绪")
                with httpx.Client(timeout=5, trust_env=False) as client:
                    response = client.get(f"http://127.0.0.1:{port}/health")
                response.raise_for_status()
                health = response.json()
                self.assertEqual(health["status"], "ok")
                self.assertEqual(health["provider"], "transmit")
                self.assertEqual(health["model_transmit"]["transport"], "ag-ui-sse")
                self.assertTrue(health["database_encryption"]["enabled"])
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                listener.close()
            self.assertFalse(thread.is_alive(), "后端验证服务未回收")
