"""复用模板服务配置执行桌面应用版本检测，不代理安装包下载。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.services.template_engine_tls import build_template_engine_ssl_context

APP_UPDATE_TIMEOUT_SECONDS = 5.0
MAX_RESPONSE_BYTES = 64 * 1024


class AppUpdateRelease(BaseModel):
    """校验 Java 更新响应，并保留当前对外字段命名。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    update_available: bool = Field(alias="updateAvailable")
    latest_app_version: str = Field(alias="latestAppVersion", min_length=1)
    app_download_url: str | None = Field(alias="appDownloadUrl")


class AppUpdateCheckError(RuntimeError):
    """仅对外提供脱敏的版本检测失败原因。"""


async def check_app_update(
    settings: Settings,
    current_version: str,
    *,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> AppUpdateRelease:
    """从现有 Settings 获取模板服务地址，使用独立五秒预算检测更新。"""

    base_url = settings.template_engine_base_url.rstrip("/")
    try:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid base URL")
    except ValueError:
        raise AppUpdateCheckError("模板服务地址未配置或格式无效。") from None

    async def request_release() -> AppUpdateRelease:
        """限量读取 Java JSON 响应，避免沿用模板生成的长超时和 ZIP 传输策略。"""

        async with client_factory(
            timeout=httpx.Timeout(APP_UPDATE_TIMEOUT_SECONDS),
            trust_env=False,
            verify=build_template_engine_ssl_context(),
        ) as client:
            async with client.stream(
                "POST",
                f"{base_url}/v1/app-updates/check",
                json={"currentVersion": current_version},
                headers={"Accept": "application/json"},
            ) as response:
                if response.status_code != 200:
                    raise AppUpdateCheckError(
                        f"模板服务版本检测失败（HTTP {response.status_code}）。"
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise AppUpdateCheckError("版本检测响应过大。")
                release = AppUpdateRelease.model_validate(json.loads(body))
                if release.update_available:
                    address = urlsplit(release.app_download_url or "")
                    if (
                        address.scheme not in {"http", "https"}
                        or not address.hostname
                        or address.username
                        or address.password
                    ):
                        raise ValueError("invalid CDN URL")
                elif release.app_download_url is not None:
                    raise ValueError("unexpected CDN URL")
                return release

    try:
        return await asyncio.wait_for(
            request_release(), timeout=APP_UPDATE_TIMEOUT_SECONDS
        )
    except (TimeoutError, httpx.TimeoutException):
        raise AppUpdateCheckError("调用模板服务检测更新超时。") from None
    except httpx.HTTPError:
        raise AppUpdateCheckError("无法连接模板服务进行版本检测。") from None
    except httpx.InvalidURL:
        raise AppUpdateCheckError("模板服务地址格式无效。") from None
    except (ValueError, ValidationError):
        raise AppUpdateCheckError("模板服务返回的更新信息无效。") from None
