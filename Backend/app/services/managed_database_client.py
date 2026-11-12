"""适配已有 SaaS 注册接口；一次调用只发出一次 POST，不自动重试。"""

from __future__ import annotations

import ipaddress
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.services.internal_service_tls import build_internal_service_ssl_context


def unwrap_bee_envelope(payload: Any) -> Any:
    """剥离 Bee 框架统一响应包装 {returnCode, errorMsg, body}，返回业务 body。

    非流式接口标注 @BeeResponseBody 后，生产环境响应体被包装；
    流式 SSE 与错误响应（BaseExceptionHandler）不包装，原样返回。
    """

    if (
        isinstance(payload, dict)
        and isinstance(payload.get("returnCode"), str)
        and "body" in payload
    ):
        return payload["body"]
    return payload


class ManagedDatabaseRegistrationError(ValueError):
    """注册失败的安全提示，并区分是否可能已在远端创建记录。"""

    def __init__(self, message: str, *, uncertain: bool = False) -> None:
        """只保留脱敏文案与结果确定性，不公开驱动或 HTTP 原始内容。"""

        super().__init__(message)
        self.uncertain = uncertain


class RegistrationResponse(BaseModel):
    """严格校验 SaaS 注册合同，不接受软删或无效挂载。

    SaaS 端使用 Bee 框架 @BeeResponseBody 统一包装响应，实际返回 HTTP 200。
    """

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    datasource_key: str = Field(alias="datasourceKey", pattern=r"^[0-9a-f]{12}$")
    datasource_id: int = Field(alias="datasourceId", gt=0, le=9007199254740991)
    status: Literal["REGISTERED"]
    is_deleted: Literal[False] = Field(alias="isDeleted")


class ManagedDatabaseClient:
    """将外部普通 HTTP 合同限制在后端基础设施适配器中。"""

    def __init__(
        self, settings: Settings, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        """先检查地址与密钥配置，配置无效时不开始注册。"""

        url = settings.managed_database_base_url.rstrip("/")
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            port = parsed.port
            loopback = host == "localhost"
            if not loopback:
                try:
                    loopback = ipaddress.ip_address(host).is_loopback
                except ValueError:
                    loopback = False
            # 内网明文 HTTP 仅允许显式白名单内的域名，避免误把公网服务降级为明文传输。
            http_allowed = host in {
                item.strip().lower()
                for item in settings.managed_database_http_allowed_hosts.split(",")
                if item.strip()
            }
            valid = (
                bool(host)
                and parsed.scheme in {"http", "https"}
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
                and (port is None or 1 <= port <= 65535)
                and (parsed.scheme == "https" or loopback or http_allowed)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ManagedDatabaseRegistrationError(
                "SaaS 地址无效；远程服务必须使用 HTTPS，"
                "内网 HTTP 域名需加入 DEVAGENTSTUDIO_MANAGED_DATABASE_HTTP_ALLOWED_HOSTS 白名单。"
            )
        if not settings.managed_database_service_key.strip():
            raise ManagedDatabaseRegistrationError(
                "请在后端配置 MANAGED_DATABASE_SERVICE_KEY 后再注册模拟数据库。"
            )
        self._url = url + "/managed-database/datasources"
        self._key = settings.managed_database_service_key
        self._timeout = settings.managed_database_timeout_seconds
        self._transport = transport

    def _actor_headers(self) -> dict[str, str]:
        """读取当前登录用户并编码为 MDC 身份头；未登录返回空。"""

        from urllib.parse import quote

        from app.services.user_context import current_user

        user_id, user_name = current_user()
        headers: dict[str, str] = {}
        if user_id:
            headers["X-Managed-User-Id"] = quote(user_id, safe="")
        if user_name:
            headers["X-Managed-User-Name"] = quote(user_name, safe="")
        return headers

    def register(self, app_name: str) -> dict[str, object]:
        """仅提交当前应用名称申请资源，结果未知时仍禁止自动重试。"""

        if (
            not isinstance(app_name, str)
            or not app_name.strip()
            or app_name != app_name.strip()
            or len(app_name) > 255
        ):
            raise ManagedDatabaseRegistrationError(
                "注册内置数据源需要非空且不超过 255 个字符的应用名称。"
            )

        try:
            # 内网 SaaS 网关依赖旧式重协商，必须显式带入内网 TLS 上下文，否则打包环境会连接失败。
            with httpx.Client(
                transport=self._transport,
                timeout=self._timeout,
                follow_redirects=False,
                trust_env=False,
                verify=build_internal_service_ssl_context(),
            ) as client:
                response = client.post(
                    self._url,
                    json={"appName": app_name},
                    headers={
                        **{
                            "Authorization": f"Bearer {self._key}",
                            "Accept": "application/json",
                        },
                        **self._actor_headers(),
                    },
                )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise ManagedDatabaseRegistrationError(
                "无法连接 SaaS，未提交注册；请检查服务地址和运行状态。"
            ) from None
        except httpx.RequestError:
            raise ManagedDatabaseRegistrationError(
                "SaaS 注册结果未确认，请先人工核对，不能重新提交创建。", uncertain=True
            ) from None
        if response.status_code != 200:
            # 仅当前合同中明确在创建前拒绝的状态可安全由用户再次发起；其他响应保守处理。
            if response.status_code in {400, 401}:
                message = "SaaS 拒绝注册，请检查请求合同或后端服务密钥。"
                raise ManagedDatabaseRegistrationError(message)
            raise ManagedDatabaseRegistrationError(
                "SaaS 未返回确认成功的注册结果，请先人工核对，不能重新提交创建。",
                uncertain=True,
            )
        try:
            registration = RegistrationResponse.model_validate(
                unwrap_bee_envelope(response.json())
            )
        except (ValueError, ValidationError):
            raise ManagedDatabaseRegistrationError(
                "SaaS 已响应但注册结果不符合合同，请先人工核对，不能重新提交创建。",
                uncertain=True,
            ) from None
        return registration.model_dump(by_alias=True, exclude={"is_deleted"})
