from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import httpx

from app.services.workspace_bootstrap.models import TemplateEngineError
from app.services.workspace_bootstrap.run_log import TemplateRunLog
from app.services.workspace_bootstrap.template_engine_client import TemplateEngineClient
from app.services.workspace_bootstrap.template_generate import cleanup_template_download
from tests.test_runtime_temporary import isolated_runtime_area


class _FailingBody(httpx.AsyncByteStream):
    """在收到并写入一个完整块后注入传输错误或取消。"""

    def __init__(self, failure: BaseException) -> None:
        """记录传输过程中要触发的异常。"""
        self.failure = failure

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """先产生一块数据，再模拟读取失败。"""
        yield b"z" * (64 * 1024)
        raise self.failure


class TemplateDownloadCleanupTests(unittest.TestCase):
    """验证连接失败、取消和 Windows 文件占用时的下载资源清理边界。"""

    def test_connection_failure_and_cancellation_release_download_resources(self) -> None:
        """失败和取消均关闭原始句柄并删除临时文件，且原异常继续传播。"""

        failures: tuple[tuple[BaseException, bool], ...] = (
            (httpx.ConnectError("unreachable"), False),
            (httpx.ConnectTimeout("timeout"), False),
            (asyncio.CancelledError(), False),
            (httpx.ReadTimeout("timeout while reading"), True),
            (httpx.ReadError("broken stream"), True),
            (asyncio.CancelledError(), True),
        )
        for operation in ("generate", "update", "update_v4"):
            for failure, during_body in failures:
                with self.subTest(operation=operation, failure=type(failure).__name__, during_body=during_body):
                    self._assert_failed_transfer_releases(operation, failure, during_body)

    def _assert_failed_transfer_releases(
        self, operation: str, failure: BaseException, during_body: bool,
    ) -> None:
        """对同一个失败场景验证三个入口的文件与原文件描述符都已释放。"""
        with tempfile.TemporaryDirectory() as directory:
            opened: list[tuple[int, str]] = []
            original = tempfile.mkstemp

            def capture(**kwargs):
                """记录真实临时文件句柄以验证关闭行为。"""

                result = original(**kwargs)
                opened.append(result)
                return result

            def respond(_request: httpx.Request) -> httpx.Response:
                """在连接或正文传输阶段注入失败。"""
                if during_body:
                    return httpx.Response(
                        200, headers={"content-type": "application/zip"}, stream=_FailingBody(failure),
                    )
                raise failure

            def factory(**kwargs):
                """使用离线传输，避免测试访问外部模板服务。"""

                return httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs)

            client = TemplateEngineClient(base_url="http://engine.invalid", connect_timeout=1,
                                          read_timeout=1, max_package_bytes=128 * 1024, client_factory=factory)
            expected = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else TemplateEngineError
            with patch("app.services.workspace_bootstrap.template_generate.tempfile.mkstemp", side_effect=capture):
                with self.assertRaises(expected):
                    if operation == "generate":
                        asyncio.run(client.generate({}, temporary_dir=directory))
                    else:
                        update = client.update if operation == "update" else client.update_v4
                        asyncio.run(update({}, {"capabilities": {}}, temporary_dir=directory))
            self.assertEqual(list(Path(directory).glob("*.zip")), [])
            self.assertEqual(len(opened), 1)
            with self.assertRaises(OSError):
                os.fstat(opened[0][0])

    def test_default_downloads_keep_the_instance_until_the_consumer_finishes(self) -> None:
        """成功交付的 ZIP 放在实例目录中，并保留到调用方清理。"""
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"zip", headers={"content-type": "application/zip"})
        )

        def factory(**kwargs) -> httpx.AsyncClient:
            """为各下载入口提供真实流式客户端和离线响应。"""
            return httpx.AsyncClient(transport=transport, **kwargs)

        client = TemplateEngineClient(
            base_url="http://engine.invalid", connect_timeout=1, read_timeout=1,
            max_package_bytes=100, client_factory=factory,
        )
        with tempfile.TemporaryDirectory() as directory:
            for operation in ("generate", "update", "update_v4"):
                with self.subTest(operation=operation):
                    area = isolated_runtime_area(Path(directory))
                    with patch("app.services.runtime_temporary._area", area):
                        if operation == "generate":
                            download = asyncio.run(client.generate({}))
                        else:
                            update = client.update if operation == "update" else client.update_v4
                            download = asyncio.run(update({}, {"capabilities": {}}))
                    assert download is not None
                    try:
                        self.assertEqual(download.temporary_path.parent, area.directory)
                        area.close()
                        self.assertEqual(download.temporary_path.read_bytes(), b"zip")
                    finally:
                        cleanup_template_download(download.temporary_path, lease=download.temporary_lease)
                        area.close()
                    self.assertFalse(area.directory.exists())

    def test_no_change_releases_v3_and_v4_download_files(self) -> None:
        """V3/V4 返回无变更时关闭并删除已创建的空 ZIP。"""
        transport = httpx.MockTransport(lambda request: httpx.Response(204))

        def factory(**kwargs) -> httpx.AsyncClient:
            """通过离线客户端验证无变更响应。"""
            return httpx.AsyncClient(transport=transport, **kwargs)

        client = TemplateEngineClient(
            base_url="http://engine.invalid", connect_timeout=1, read_timeout=1,
            max_package_bytes=100, client_factory=factory,
        )
        with tempfile.TemporaryDirectory() as directory:
            for update in (client.update, client.update_v4):
                with self.subTest(operation=update.__name__):
                    self.assertIsNone(asyncio.run(update({}, {"capabilities": {}}, temporary_dir=directory)))
                    self.assertEqual(list(Path(directory).glob("*.zip")), [])

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
