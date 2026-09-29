"""通过流式 HTTP 获取 Template Engine 首次工程 ZIP。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from collections.abc import Callable
from typing import Any

import httpx

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.workspace_bootstrap.models import TemplateEngineError, TemplatePackageDownload

_CHUNK_BYTES = 64 * 1024
logger = logging.getLogger("uvicorn.error")

# 临时 fallback：Engine 未配置时直接 git clone 固定模板仓库，保证本地可拉起。
# 后续接入真实 Engine 后可移除。
_GIT_FALLBACK_FRONTEND_URL = "https://github.com/ruyue1/frontend-template.git"
_GIT_FALLBACK_BACKEND_URL = "https://github.com/Hupy2118/springboot-template.git"
_GIT_FALLBACK_TEMPLATE_REVISION = "git-fallback-v1"
# 必须与读取方一致：template_package.py 用 app.branding 的 WORKSPACE_ARTIFACT_DIR
# 定位 ZIP 内的 TemplateState。这里硬编码旧目录名会让 Bootstrap 直接失败。
_TEMPLATE_STATE_ZIP_PATH = (WORKSPACE_ARTIFACT_DIR / "template-state.json").as_posix()


class TemplateEngineClient:
    """封装 Engine 连接、超时与下载大小限制，避免调用方处理传输细节。"""

    def __init__(self, *, base_url: str, connect_timeout: float, read_timeout: float, max_package_bytes: int, client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient) -> None:
        """保存冻结的 Engine 连接配置，不在前端或 Workspace 写入连接状态。"""

        self._base_url = base_url.rstrip("/")
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._max_package_bytes = max_package_bytes
        self._client_factory = client_factory

    async def generate(self, requested_config: dict[str, Any], *, temporary_dir: str | Path | None = None) -> TemplatePackageDownload:
        """调用 `/v1/generate` 并分块写临时 ZIP，同时计算 SHA-256。"""

        if not self._base_url:
            # Engine 未配置时走 git fallback：直接 clone 固定模板仓库并打包成契约 ZIP。
            logger.info("Template Engine 未配置，使用 git fallback 拉取模板仓库。")
            return await self._generate_from_git_fallback(requested_config, temporary_dir=temporary_dir)
        directory = str(Path(temporary_dir)) if temporary_dir is not None else None
        descriptor, name = tempfile.mkstemp(prefix="devagentstudio-template-", suffix=".zip", dir=directory)
        temporary_path = Path(name)
        digest = hashlib.sha256()
        size = 0
        try:
            timeout = httpx.Timeout(connect=self._connect_timeout, read=self._read_timeout, write=self._read_timeout, pool=self._connect_timeout)
            # trust_env=False：模板引擎是本机直连的基础设施；httpx 在 Windows 会采用注册表系统代理但不执行其排除列表，回环请求会被代理拦截。
            async with self._client_factory(timeout=timeout, trust_env=False) as client:
                async with client.stream("POST", f"{self._base_url}/v1/generate", json={"requestedConfig": requested_config}, headers={"Accept": "application/zip"}) as response:
                    if response.status_code >= 400:
                        raise await _engine_response_error(response, "Template Engine 拒绝请求")
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

    async def _generate_from_git_fallback(
        self, requested_config: dict[str, Any], *, temporary_dir: str | Path | None = None
    ) -> TemplatePackageDownload:
        """git clone 固定前后端模板仓库，打包成 frontend/backend + template-state 的契约 ZIP。"""

        directory = str(Path(temporary_dir)) if temporary_dir is not None else None
        clone_root = Path(tempfile.mkdtemp(prefix="devagentstudio-git-fallback-", dir=directory))
        temporary_path: Path | None = None
        try:
            frontend_dir = clone_root / "frontend"
            backend_dir = clone_root / "backend"
            _git_clone(_GIT_FALLBACK_FRONTEND_URL, frontend_dir)
            _git_clone(_GIT_FALLBACK_BACKEND_URL, backend_dir)
            # 移除 clone 下来的 .git，避免进入契约 ZIP。
            shutil.rmtree(frontend_dir / ".git", ignore_errors=True)
            shutil.rmtree(backend_dir / ".git", ignore_errors=True)

            template_state = _build_fallback_template_state(requested_config)
            state_bytes = json.dumps(template_state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

            descriptor, name = tempfile.mkstemp(
                prefix="devagentstudio-template-", suffix=".zip", dir=directory
            )
            temporary_path = Path(name)
            digest = hashlib.sha256()
            size = 0
            with os.fdopen(descriptor, "wb") as output:
                with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
                    for root_name in ("frontend", "backend"):
                        src_root = clone_root / root_name
                        for file_path in src_root.rglob("*"):
                            if not file_path.is_file() or file_path.is_symlink():
                                continue
                            arcname = f"{root_name}/{file_path.relative_to(src_root).as_posix()}"
                            package.write(file_path, arcname)
                    package.writestr(_TEMPLATE_STATE_ZIP_PATH, state_bytes)
                output.flush()
                os.fsync(output.fileno())
            with temporary_path.open("rb") as output:
                for chunk in iter(lambda: output.read(_CHUNK_BYTES), b""):
                    digest.update(chunk)
                    size += len(chunk)
            return TemplatePackageDownload(temporary_path, digest.hexdigest(), size, "application/zip")
        except Exception as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise TemplateEngineError(f"git fallback 拉取模板失败：{exc}") from exc
        finally:
            shutil.rmtree(clone_root, ignore_errors=True)

    async def update(
        self,
        current_template_state: dict[str, Any],
        requested_config: dict[str, Any],
        *,
        mode: str = "APPLY",
        temporary_dir: str | Path | None = None,
    ) -> TemplatePackageDownload | None:
        """调用 V2 单次 `/v1/update`；204 返回 `None`，200 时下载 Strategy Package。"""

        if not self._base_url:
            raise TemplateEngineError("Template Engine 地址未配置。")
        if mode not in {"APPLY", "RECONCILE"}:
            raise TemplateEngineError("Template Engine 更新 mode 必须是 APPLY 或 RECONCILE。")
        directory = str(Path(temporary_dir)) if temporary_dir is not None else None
        descriptor, name = tempfile.mkstemp(prefix="devagentstudio-template-update-", suffix=".zip", dir=directory)
        temporary_path = Path(name)
        digest = hashlib.sha256()
        size = 0
        try:
            # 仅记录调用边界与非敏感摘要；不得输出完整请求配置或 ZIP 内容。
            logger.info(
                "模板更新请求已发起：endpoint=%s/v1/update，当前模板版本=%s。",
                self._base_url,
                str(current_template_state.get("templateRevision") or "unknown"),
            )
            timeout = httpx.Timeout(connect=self._connect_timeout, read=self._read_timeout, write=self._read_timeout, pool=self._connect_timeout)
            # trust_env=False：模板引擎是本机直连的基础设施；httpx 在 Windows 会采用注册表系统代理但不执行其排除列表，回环请求会被代理拦截。
            async with self._client_factory(timeout=timeout, trust_env=False) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/v1/update",
                    json={
                        "protocolVersion": "2",
                        "currentTemplateState": current_template_state,
                        "requestedConfig": requested_config,
                        "mode": mode,
                    },
                    headers={"Accept": "application/zip"},
                ) as response:
                    logger.info(
                        "模板更新接口已响应：endpoint=%s/v1/update，status=%s。",
                        self._base_url,
                        response.status_code,
                    )
                    if response.status_code == 204:
                        os.close(descriptor)
                        descriptor = -1
                        temporary_path.unlink(missing_ok=True)
                        logger.info("模板更新接口返回无变更（HTTP 204）。")
                        return None
                    if response.status_code >= 400:
                        raise await _engine_response_error(response, "Template Engine 拒绝更新请求")
                    content_type = response.headers.get("content-type")
                    if not content_type or not content_type.lower().startswith("application/zip"):
                        raise TemplateEngineError("Template Engine 更新未返回 application/zip。")
                    with os.fdopen(descriptor, "wb") as output:
                        descriptor = -1
                        async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                            size += len(chunk)
                            if size > self._max_package_bytes:
                                raise TemplateEngineError("模板更新 ZIP 超过下载大小限制。")
                            digest.update(chunk)
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
            download = TemplatePackageDownload(temporary_path, digest.hexdigest(), size, content_type)
            logger.info("模板更新 ZIP 下载完成：bytes=%s，sha256=%s。", size, download.sha256)
            return download
        except httpx.TimeoutException as exc:
            logger.warning("模板更新接口调用超时：endpoint=%s/v1/update。", self._base_url)
            raise TemplateEngineError("调用 Template Engine 更新超时。") from exc
        except httpx.HTTPError as exc:
            logger.warning("模板更新接口调用失败：endpoint=%s/v1/update，error=%s。", self._base_url, type(exc).__name__)
            raise TemplateEngineError("调用 Template Engine 更新失败。") from exc
        except Exception:
            logger.exception("模板更新接口处理失败：endpoint=%s/v1/update。", self._base_url)
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)
            raise


async def _engine_response_error(response: httpx.Response, prefix: str) -> TemplateEngineError:
    """先读取流式错误 body，再保留 Engine 标准字段和 HTTP status，避免 ResponseNotRead。"""

    raw = await response.aread()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # 非 JSON 错误响应通常来自中间层（如本机代理拦截）而非 Engine 本身，记录响应头与片段便于定位。
        payload = None
        logger.warning(
            "Template Engine 返回非 JSON 错误响应：status=%s，content-type=%s，server=%s，body=%r。",
            response.status_code,
            response.headers.get("content-type", ""),
            response.headers.get("server", ""),
            raw[:300],
        )
    if isinstance(payload, dict) and all(key in payload for key in ("code", "message")):
        engine_error = {
            key: payload[key]
            for key in ("code", "message", "details", "traceId")
            if key in payload
        }
        return TemplateEngineError(
            f"{prefix}（HTTP {response.status_code}）：{json.dumps(engine_error, ensure_ascii=False, separators=(',', ':'))}",
            engine_error=engine_error,
            http_status=response.status_code,
        )
    return TemplateEngineError(f"{prefix}（HTTP {response.status_code}）。", http_status=response.status_code)


def _git_clone(url: str, dest: Path) -> None:
    """以 depth=1 浅克隆指定仓库到 dest，失败抛出 TemplateEngineError。"""

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise TemplateEngineError("未找到 git 命令，无法执行 git fallback。") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise TemplateEngineError(f"git clone 失败：{url} {stderr}") from exc


def _build_fallback_template_state(requested_config: dict[str, Any]) -> dict[str, Any]:
    """从请求编译 fallback TemplateState：requested/effective 取已启用能力，appliedAdditions 为空。"""

    capabilities = requested_config.get("capabilities") if isinstance(requested_config, dict) else None
    enabled: dict[str, dict[str, Any]] = {}
    if isinstance(capabilities, dict):
        for capability_id, definition in capabilities.items():
            if (
                isinstance(capability_id, str)
                and isinstance(definition, dict)
                and definition.get("enabled") is True
            ):
                config = definition.get("config") or {}
                if not isinstance(config, dict):
                    config = {}
                enabled[capability_id] = {"enabled": True, "config": config}
    return {
        "schemaVersion": 2,
        "templateRevision": _GIT_FALLBACK_TEMPLATE_REVISION,
        "requested": enabled,
        "effective": enabled,
        "appliedAdditions": {},
    }

