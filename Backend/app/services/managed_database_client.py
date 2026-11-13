"""内置数据库 SaaS 的 HTTP/SSE 通信入口，每次调用只发送一次请求。"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx
from httpx_sse import SSEError, connect_sse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.services.builtin_authorization_seed_client import (
    SeedDetail,
    SeedReceipt,
    SeedRemoteError,
    decode_seed_response,
)
from app.services.database_credentials import DatabaseCredentialError
from app.services.internal_service_tls import build_internal_service_ssl_context
from app.services.managed_database_agui import decode_table_data, require_table_data, table_data_input
from app.services.managed_database_bee import unwrap_bee_envelope


class ManagedDatabaseRegistrationError(ValueError):
    """注册失败的安全提示，并区分是否可能已在远端创建记录。"""

    def __init__(self, message: str, *, uncertain: bool = False) -> None:
        """只保留脱敏文案与结果确定性，不公开驱动或 HTTP 原始内容。"""

        super().__init__(message)
        self.uncertain = uncertain


class ManagedDatabaseStartupError(DatabaseCredentialError):
    """仅携带可公开的资源错误，避免将响应正文或凭据带入启动记录。"""


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
    """集中发起 Managed Database 请求，保留各端点的传输和错误语义。"""

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

        from app.services.user_context import current_user

        user_id, user_name = current_user()
        headers: dict[str, str] = {}
        if user_id:
            headers["X-Managed-User-Id"] = quote(user_id, safe="")
        if user_name:
            headers["X-Managed-User-Name"] = quote(user_name, safe="")
        return headers

    def _http_client(self, *, internal_tls: bool) -> httpx.Client:
        """每次请求创建独立连接，按原端点选择内网或默认 TLS。"""

        options = {"verify": build_internal_service_ssl_context()} if internal_tls else {}
        return httpx.Client(
            transport=self._transport, timeout=self._timeout,
            follow_redirects=False, trust_env=False, **options,
        )

    def _headers(self, accept: str, *, actor: bool = False) -> dict[str, str]:
        """生成 Bearer 与 Accept，仅在既有端点加入当前登录身份。"""

        headers = {"Authorization": f"Bearer {self._key}", "Accept": accept}
        if actor:
            headers.update(self._actor_headers())
        return headers

    def _request_json(
        self, method: str, url: str, *, body: dict | None = None,
    ) -> httpx.Response:
        """发送普通 JSON 请求，保留调用端点对异常与响应的解释权。"""

        with self._http_client(internal_tls=True) as client:
            return client.request(method, url, json=body, headers=self._headers("application/json", actor=True))

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
            response = self._request_json("POST", self._url, body={"appName": app_name})
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

    def submit_table(
        self, datasource_key: str, *, mode: str, sql: str, allow_data_loss: bool = False,
    ) -> Any:
        """提交一次单表操作，只有确认允许清空时才附带 allowDataLoss。"""

        body: dict[str, Any] = {"mode": mode, "sql": sql}
        if allow_data_loss:
            body["allowDataLoss"] = True
        return self._request_table(datasource_key, "table-operations", body=body)

    def batch_create_tables(self, datasource_key: str, sqls: list[str]) -> Any:
        """一次提交完整 CREATE 列表，不复用单表请求体或自动重试。"""

        return self._request_table(datasource_key, "table-operations/batch", body={"mode": "CREATE", "sqls": sqls})

    def get_table_operation(self, datasource_key: str, operation_id: str) -> Any:
        """读取原操作详情，不再次提交 SQL。"""

        return self._request_table(datasource_key, f"table-operations/{operation_id}")

    def list_table_operations(self, datasource_key: str, limit: int = 100) -> Any:
        """读取最近的原操作记录。"""

        return self._request_table(datasource_key, f"table-operations?limit={limit}")

    def check_tables(self, datasource_key: str, sqls: list[str]) -> Any:
        """只读检查表结构，保留任务输入守卫及暂时不可用分类。"""

        return self._request_table(datasource_key, "tables/check", body={"sqls": sqls})

    def list_tables(self, datasource_key: str) -> Any:
        """读取应用已建成的表目录。"""

        return self._request_table(datasource_key, "tables")

    def get_table_columns(self, datasource_key: str, table: str) -> Any:
        """按原路径编码读取单表列定义。"""

        return self._request_table(datasource_key, f"tables/{quote(table, safe='')}/columns")

    def _request_table(self, datasource_key: str, path: str, *, body: dict | None = None) -> Any:
        """发送一次请求；错误脱敏，提交结果未知时提示先查询原记录。"""

        from app.services.builtin_authorization_task_store import check_task_inputs, input_guard
        from app.services.managed_table_client import ManagedTableQueryTransientError, decode_table_response

        check_task_inputs()
        managed_task = input_guard.get() is not None
        if not re.fullmatch(r"[0-9a-f]{12}", datasource_key):
            raise ValueError("数据源 datasourceKey 无效。")
        url = f"{self._url}/{datasource_key}/{path}"
        try:
            response = self._request_json("GET" if body is None else "POST", url, body=body)
        except httpx.RequestError:
            if managed_task and (body is None or path == "tables/check"):
                raise ManagedTableQueryTransientError("SaaS 原操作查询暂时不可用。") from None
            raise ManagedDatabaseRegistrationError(
                "SaaS 请求未取得确认结果。若已提交执行，请刷新原操作记录，不要重复提交。",
                uncertain=body is not None,
            ) from None
        return decode_table_response(response, path, body=body, managed_task=managed_task)

    def check_runtime_tables(self, datasource_key: str, sqls: tuple[str, ...]) -> object:
        """执行启动专用结构检查，保留启动错误分类及 SQL 数组格式。"""

        return self._request_runtime(datasource_key, "tables/check", sqls=sqls)

    def get_runtime_connection(self, datasource_key: str) -> object:
        """读取内部调试连接，由启动调用方核对连接与快照身份。"""

        return self._request_runtime(datasource_key, "runtime-connection")

    def _request_runtime(self, datasource_key: str, path: str, *, sqls: tuple[str, ...] | None = None) -> object:
        """发出一次只读请求，不自动重试，不公开响应或请求异常内容。"""

        try:
            response = self._request_json(
                "POST" if sqls is not None else "GET", f"{self._url}/{datasource_key}/{path}",
                body={"sqls": list(sqls)} if sqls is not None else None,
            )
        except httpx.RequestError:
            raise ManagedDatabaseStartupError("无法连接内置数据库资源服务，请确认 SaaS 运行状态后重新启动。") from None
        if response.status_code != 200:
            try:
                payload = response.json()
                code = payload.get("returnCode") if isinstance(payload, dict) else None
            except ValueError:
                code = None
            if response.status_code in {401, 403}:
                message = "内置数据库资源服务鉴权失败，请检查 Studio 后端服务密钥。"
            elif code == "RESOURCE_BUSY":
                message = "内置数据库存在未完成操作，请先在实体中核对执行结果，再重新启动。"
            elif code == "RUNTIME_CONNECTION_NOT_CONFIGURED":
                message = "SaaS 尚未配置当前挂载的专用调试连接，请配置后重新启动。"
            elif response.status_code == 404:
                message = "SaaS 应用或运行时连接接口不存在，请核对应用注册与 SaaS 版本。"
            else:
                message = "内置数据库资源暂不可用，请检查 SaaS 挂载配置后重新启动。"
            raise ManagedDatabaseStartupError(message)
        try:
            return unwrap_bee_envelope(response.json())
        except ValueError:
            raise ManagedDatabaseStartupError("SaaS 返回的数据库资源结果无效，请核对服务后重新启动。") from None

    def submit_authorization_seed(self, key: str, payload: dict) -> SeedReceipt:
        """只发送一次 POST，HTTP 200 仅是受理事实。"""

        body = self._request_seed(key, "batch", payload=payload)
        try:
            return SeedReceipt.model_validate(body)
        except ValidationError:
            raise SeedRemoteError("seed_invalid_response") from None

    def get_seed_operation(self, key: str, operation_id: str, idempotency_key: str) -> SeedDetail | None:
        """优先读取原操作编号，同时校验本地保存的幂等键。"""

        return self._get_seed(key, idempotency_key, operation_id=operation_id)

    def get_seed_by_key(self, key: str, idempotency_key: str) -> SeedDetail | None:
        """按原幂等键只读查找 Seed 操作。"""

        return self._get_seed(key, idempotency_key)

    def _get_seed(self, key: str, idempotency_key: str, *, operation_id: str | None = None) -> SeedDetail | None:
        """按原 ID 或原幂等键 GET 一次，仅明确的业务查无记录返回空。"""

        if not re.fullmatch(r"[0-9a-f]{64}", idempotency_key):
            raise SeedRemoteError("seed_invalid_identity")
        if operation_id is not None and not re.fullmatch(r"[1-9][0-9]{0,18}", operation_id):
            raise SeedRemoteError("seed_invalid_identity")
        body = self._request_seed(key, operation_id or f"by-key/{idempotency_key}", allow_missing=True)
        if body is None:
            return None
        try:
            return SeedDetail.model_validate(body)
        except ValidationError:
            raise SeedRemoteError("seed_invalid_response") from None

    def _request_seed(self, key: str, path: str, *, payload: dict | None = None, allow_missing: bool = False) -> dict | None:
        """解析 SaaS JSON 包装并脱敏网络、HTTP 和解析错误。"""

        from app.services.builtin_authorization_seed import canonical_json

        if not re.fullmatch(r"[0-9a-f]{12}", key):
            raise SeedRemoteError("seed_invalid_identity")
        headers = self._headers("application/json")
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            with self._http_client(internal_tls=False) as client:
                response = client.request(
                    "GET" if payload is None else "POST", f"{self._url}/{key}/data-operations/{path}",
                    headers=headers, content=canonical_json(payload) if payload is not None else None,
                )
        except httpx.TimeoutException:
            raise SeedRemoteError("seed_timeout") from None
        except httpx.RequestError:
            raise SeedRemoteError("seed_network_error") from None
        return decode_seed_response(response, submitting=payload is not None, allow_missing=allow_missing)

    def query_table_data(self, datasource_key: str, table: str, query: dict[str, Any]) -> Any:
        """建立一次只读 SSE 连接，收到完整 AG-UI 结果后返回。"""

        thread_id, run_id, payload = table_data_input(query)
        path = f"tables/{quote(table, safe='')}/data/run"
        try:
            with self._http_client(internal_tls=False) as client:
                with connect_sse(
                    client, "POST", f"{self._url}/{datasource_key}/{path}", json=payload,
                    headers=self._headers("text/event-stream"),
                ) as source:
                    if source.response.status_code != 200:
                        raise ValueError("SaaS 表数据查询接口不可用，请确认新接口已部署及服务鉴权配置。")
                    state = decode_table_data(source, thread_id, run_id)
        except (httpx.HTTPError, SSEError, ValidationError):
            raise ValueError("SaaS 表数据查询连接中断或响应无效，请重新查询。") from None
        return require_table_data(*state)
