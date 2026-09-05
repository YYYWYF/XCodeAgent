"""通过流式 HTTP 获取 Template Engine 首次工程 ZIP。"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from collections.abc import Callable
from typing import Any

import httpx

from app.services.workspace_bootstrap.models import TemplateEngineError, TemplatePackageDownload

_CHUNK_BYTES = 64 * 1024


class TemplateEngineClient:
    """封装 Engine token、超时与下载大小限制，避免调用方处理传输细节。"""

    def __init__(self, *, base_url: str, token: str, connect_timeout: float, read_timeout: float, max_package_bytes: int, client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient) -> None:
        """保存冻结的 Engine 连接配置，不在前端或 Workspace 写入凭据。"""

        self._base_url = base_url.rstrip("/")
        self._token = token
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._max_package_bytes = max_package_bytes
        self._client_factory = client_factory

    async def generate(self, requested_config: dict[str, Any], *, temporary_dir: str | Path | None = None) -> TemplatePackageDownload:
        """调用 `/v1/generate` 并分块写临时 ZIP，同时计算 SHA-256。"""

        if not self._base_url or not self._token:
            raise TemplateEngineError("Template Engine 地址或凭据未配置。")
        directory = str(Path(temporary_dir)) if temporary_dir is not None else None
        descriptor, name = tempfile.mkstemp(prefix="xcodeagent-template-", suffix=".zip", dir=directory)
        temporary_path = Path(name)
        digest = hashlib.sha256()
        size = 0
        try:
            timeout = httpx.Timeout(connect=self._connect_timeout, read=self._read_timeout, write=self._read_timeout, pool=self._connect_timeout)
            async with self._client_factory(timeout=timeout) as client:
                async with client.stream("POST", f"{self._base_url}/v1/generate", json={"requestedConfig": requested_config}, headers={"Authorization": f"Bearer {self._token}", "Accept": "application/zip"}) as response:
                    if response.status_code >= 400:
                        raise TemplateEngineError(f"Template Engine 拒绝请求（HTTP {response.status_code}）。")
                    content_type = response.headers.get("content-type")
                    if not content_type or not content_type.lower().startswith("application/zip"):
                        raise TemplateEngineError("Template Engine 未返回 application/zip。")
                    with os.fdopen(descriptor, "wb") as output:
                        descriptor = -1
                        async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                            size += len(chunk)
                            if size > self._max_package_bytes:
                                raise TemplateEngineError("模板 ZIP 超过下载大小限制。")
                            digest.update(chunk)
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
            return TemplatePackageDownload(temporary_path, digest.hexdigest(), size, content_type)
        except httpx.TimeoutException as exc:
            raise TemplateEngineError("调用 Template Engine 超时。") from exc
        except httpx.HTTPError as exc:
            raise TemplateEngineError("调用 Template Engine 失败。") from exc
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)
            raise
