from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.services.workspace_bootstrap.models import TemplateEngineError
from app.services.workspace_bootstrap.run_log import TemplateRunLog
from app.services.workspace_bootstrap.template_engine_client import TemplateEngineClient
from app.services.workspace_bootstrap.template_generate import cleanup_template_download


class TemplateDownloadCleanupTests(unittest.TestCase):
    """验证连接失败、取消和 Windows 文件占用时的下载资源清理边界。"""

    def test_connection_failure_and_cancellation_release_download_resources(self) -> None:
        """失败和取消均关闭原始句柄并删除临时文件，且原异常继续传播。"""

        for failure in (httpx.ConnectError("unreachable"), httpx.ConnectTimeout("timeout"), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                opened: list[tuple[int, str]] = []
                original = tempfile.mkstemp

                def capture(**kwargs):
                    """记录真实临时文件句柄以验证关闭行为。"""

                    result = original(**kwargs)
                    opened.append(result)
                    return result

                def respond(_request):
                    """在建立连接阶段注入失败或取消。"""

                    raise failure

                def factory(**kwargs):
                    """使用离线传输，避免测试访问外部模板服务。"""

                    return httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs)

                client = TemplateEngineClient(base_url="http://engine.invalid", connect_timeout=1,
                                              read_timeout=1, max_package_bytes=100, client_factory=factory)
                expected = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else TemplateEngineError
                with patch("app.services.workspace_bootstrap.template_generate.tempfile.mkstemp", side_effect=capture):
                    with self.assertRaises(expected):
                        asyncio.run(client.generate({}, temporary_dir=directory))
                self.assertEqual(list(Path(directory).glob("*.zip")), [])
                self.assertEqual(len(opened), 1)
                with self.assertRaises(OSError):
                    os.fstat(opened[0][0])

    def test_locked_file_cleanup_only_warns(self) -> None:
        """模拟 Windows 文件占用，清理失败仅记告警而不抛出。"""

        with tempfile.TemporaryDirectory() as directory:
            run_log = TemplateRunLog.create(directory)
            with patch.object(Path, "unlink", side_effect=PermissionError("file is locked")):
                cleanup_template_download(Path(directory) / "template.zip", run_log=run_log)
            run_log.close()
            content = run_log.path.read_text(encoding="utf-8")
            self.assertIn('event="cleanup_failed"', content)
            self.assertIn('level="WARNING"', content)
