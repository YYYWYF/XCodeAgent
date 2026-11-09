"""封装首次 Template Engine Generate 请求和模板 ZIP 流式下载。"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from app.services.template_engine_tls import build_template_engine_ssl_context
from app.services.workspace_bootstrap.models import TemplateEngineError, TemplatePackageDownload
from app.services.workspace_bootstrap.run_log import TemplateRunLog

_CHUNK_BYTES = 64 * 1024


async def generate_template_package(
    *,
    base_url: str,
    connect_timeout: float,
    read_timeout: float,
    max_package_bytes: int,
    client_factory: Callable[..., httpx.AsyncClient],
    requested_config: dict[str, Any],
    response_error: Callable[[httpx.Response, str], Awaitable[TemplateEngineError]],
    temporary_dir: str | Path | None = None,
    run_log: TemplateRunLog | None = None,
) -> TemplatePackageDownload:
    """调用 V3 `/v1/generate`，在临时文件中流式下载并计算 SHA-256。"""

    if not base_url:
        raise TemplateEngineError("模板生成服务地址未配置，无法生成模板代码。")
    endpoint = f"{base_url}/v1/generate"
    request_started = time.monotonic()
    if run_log is not None:
        run_log.event(
            stage="template_request",
            event="stage_started",
            message="开始请求模板引擎",
            endpoint=endpoint,
            requestKeys=sorted(requested_config),
            capabilityKeys=sorted((requested_config.get("capabilities") or {}).keys()),
            applicationConfigIncluded="applicationConfig" in requested_config,
        )
    directory = str(Path(temporary_dir)) if temporary_dir is not None else None
    try:
        descriptor, name = tempfile.mkstemp(prefix="devagentstudio-template-", suffix=".zip", dir=directory)
    except BaseException as exc:
        if run_log is not None:
            run_log.event(
                stage="template_request",
                event="stage_failed",
                message="创建模板下载临时文件失败",
                level="ERROR",
                duration_ms=int((time.monotonic() - request_started) * 1000),
                exception=exc,
            )
        raise
    temporary_path = Path(name)
    digest = hashlib.sha256()
    size = 0
    request_completed = False
    download_started: float | None = None
    http_status: int | None = None
    succeeded = False
    try:
        timeout = httpx.Timeout(connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout)
        # trust_env=False：模板引擎是本机直连基础设施，避免 Windows 系统代理拦截回环请求。
        async with client_factory(timeout=timeout, trust_env=False, verify=build_template_engine_ssl_context()) as client:
            async with client.stream(
                "POST",
                endpoint,
                json={"requestedConfig": requested_config},
                headers={"Accept": "application/zip"},
            ) as response:
                http_status = response.status_code
                request_completed = True
                if run_log is not None:
                    run_log.event(
                        stage="template_request",
                        event="response_received",
                        message="模板引擎已返回响应头",
                        duration_ms=int((time.monotonic() - request_started) * 1000),
                        httpStatus=response.status_code,
                        contentType=response.headers.get("content-type", ""),
                        contentLength=response.headers.get("content-length", "unknown"),
                    )
                if response.status_code >= 400:
                    raise await response_error(response, "模板生成服务拒绝请求")
                content_type = response.headers.get("content-type")
                if not content_type or not content_type.lower().startswith("application/zip"):
                    raise TemplateEngineError("模板生成服务未返回 application/zip。")
                if run_log is not None:
                    run_log.event(
                        stage="template_request",
                        event="stage_completed",
                        message="模板引擎请求成功",
                        duration_ms=int((time.monotonic() - request_started) * 1000),
                        httpStatus=response.status_code,
                        contentType=content_type,
                    )
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = -1
                    download_started = time.monotonic()
                    if run_log is not None:
                        run_log.event(
                            stage="template_download",
                            event="stage_started",
                            message="开始下载模板 ZIP",
                            httpStatus=response.status_code,
                            contentLength=response.headers.get("content-length", "unknown"),
                        )
                    async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                        size += len(chunk)
                        if size > max_package_bytes:
                            raise TemplateEngineError("模板 ZIP 超过下载大小限制。")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
        download = TemplatePackageDownload(temporary_path, digest.hexdigest(), size, content_type)
        if run_log is not None and download_started is not None:
            run_log.event(
                stage="template_download",
                event="download_completed",
                message="模板 ZIP 下载完成",
                duration_ms=int((time.monotonic() - download_started) * 1000),
                httpStatus=http_status,
                downloadedBytes=size,
                sha256=download.sha256,
                contentType=content_type,
            )
        succeeded = True
        return download
    except httpx.TimeoutException as exc:
        if run_log is not None:
            failed_stage = "template_download" if download_started is not None else "template_request"
            run_log.event(
                stage=failed_stage,
                event="download_failed" if download_started is not None else "stage_failed",
                message="模板文件下载超时" if download_started is not None else "模板引擎请求超时",
                level="ERROR",
                duration_ms=int((time.monotonic() - (download_started or request_started)) * 1000),
                downloadedBytes=size if download_started is not None else None,
                httpStatus=http_status,
                exception=exc,
            )
        raise TemplateEngineError("调用模板生成服务超时。") from exc
    except httpx.HTTPError as exc:
        if run_log is not None:
            failed_stage = "template_download" if download_started is not None else "template_request"
            run_log.event(
                stage=failed_stage,
                event="download_failed" if download_started is not None else "stage_failed",
                message="模板文件下载连接失败" if download_started is not None else "模板引擎连接或请求失败",
                level="ERROR",
                duration_ms=int((time.monotonic() - (download_started or request_started)) * 1000),
                downloadedBytes=size if download_started is not None else None,
                httpStatus=http_status,
                exception=exc,
            )
        raise TemplateEngineError("调用模板生成服务失败。") from exc
    except Exception as exc:
        if run_log is not None and download_started is not None:
            run_log.event(
                stage="template_download",
                event="download_failed",
                message="模板 ZIP 写入或下载失败",
                level="ERROR",
                duration_ms=int((time.monotonic() - download_started) * 1000),
                downloadedBytes=size,
                httpStatus=http_status,
                exception=exc,
            )
        elif run_log is not None:
            run_log.event(
                stage="template_request",
                event="stage_failed",
                message="模板引擎响应不满足 ZIP 契约" if request_completed else "模板引擎请求失败",
                level="ERROR",
                duration_ms=int((time.monotonic() - request_started) * 1000),
                httpStatus=http_status,
                exception=exc,
            )
        raise
    finally:
        # 包括超时、连接失败与任务取消；先关句柄再删文件，兼容 Windows 占用规则。
        if not succeeded:
            cleanup_template_download(temporary_path, descriptor=descriptor, run_log=run_log)


def cleanup_template_download(
    path: Path, *, descriptor: int = -1, run_log: TemplateRunLog | None = None,
) -> None:
    """尽力释放下载资源并记录结果，清理异常不能覆盖业务异常或已提交成功。"""

    operations = []
    if descriptor >= 0:
        operations.append(("download_handle_close", lambda: os.close(descriptor)))
    operations.append(("download_file_cleanup", lambda: path.unlink(missing_ok=True)))
    for operation, cleanup in operations:
        try:
            cleanup()
        except Exception as exc:
            if run_log is not None:
                run_log.event(stage=operation, event="cleanup_failed", level="WARNING",
                              message="下载资源清理失败", resourcePath=str(path), exception=exc)
        else:
            if run_log is not None:
                run_log.event(stage=operation, event="cleanup_completed",
                              message="下载资源已释放", resourcePath=str(path))
