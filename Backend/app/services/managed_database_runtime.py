"""内置数据库启动专用适配，不向通用 MySQL 工具或 DDL 执行器开放连接。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.config import Settings
from app.branding import WORKSPACE_ARTIFACT_DIR
from app.domain.entity_tables import EntityTableRequest, TableReadiness
from app.domain.managed_database import ManagedDatabaseRegistration
from app.services.application_config.repository import ApplicationConfigRepository
from app.services.database_credentials import DatabaseCredentialError, load_application_json
from app.services.managed_database_client import ManagedDatabaseClient
from app.services.managed_database_readiness import database_check_report
from app.workspace.endpoint_design_documents import technical_plan_path


class ManagedDatabaseStartupError(DatabaseCredentialError):
    """仅携带可公开的资源错误，避免将响应正文或凭据带入启动记录。"""


class RuntimeConnection(BaseModel):
    """严格验证后端内部连接合同，密码默认表示始终脱敏。"""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    datasourceKey: str = Field(pattern=r"^[0-9a-f]{12}$")
    datasourceId: int = Field(gt=0)
    host: str = Field(pattern=r"^[A-Za-z0-9._:-]+$", min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1, max_length=64)
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1)
    sslMode: Literal["DISABLED", "PREFERRED", "REQUIRED", "VERIFY_CA", "VERIFY_IDENTITY"]

    def jdbc_url(self) -> str:
        """用固定参数生成 JDBC URL，不接收服务端任意连接参数或拼接密码。"""

        host = f"[{self.host}]" if ":" in self.host else self.host
        return (f"jdbc:mysql://{host}:{self.port}/{quote(self.database, safe='')}"
                f"?useUnicode=true&characterEncoding=utf-8&sslMode={self.sslMode}"
                "&serverTimezone=Asia/Shanghai&connectTimeout=5000&socketTimeout=30000")


@dataclass(frozen=True)
class StartupSnapshot:
    """只保存本次检查依据，允许进程内传递，不包含任何连接凭据。"""

    datasource_key: str
    datasource_id: int
    digest: str
    table_names: tuple[str, ...]
    sqls: tuple[str, ...]
    entities: tuple[tuple[str, str, str], ...] = ()


class ManagedRuntimeClient(ManagedDatabaseClient):
    """复用 SaaS 地址与鉴权约束，资源错误不套用建表重发提示。"""

    def request(self, datasource_key: str, path: str, *, sqls: tuple[str, ...] | None = None) -> object:
        """发出一次只读请求，不自动重试，不公开响应或请求异常内容。"""

        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout,
                              follow_redirects=False, trust_env=False) as client:
                response = client.request(
                    "POST" if sqls is not None else "GET", f"{self._url}/{datasource_key}/{path}",
                    json={"sqls": list(sqls)} if sqls is not None else None,
                    headers={"Authorization": f"Bearer {self._key}", "Accept": "application/json"},
                )
        except httpx.RequestError:
            raise ManagedDatabaseStartupError("无法连接内置数据库资源服务，请确认 SaaS 运行状态后重新启动。") from None
        if response.status_code != 200:
            try:
                payload = response.json()
                code = payload.get("code") if isinstance(payload, dict) else None
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
            return response.json()
        except ValueError:
            raise ManagedDatabaseStartupError("SaaS 返回的数据库资源结果无效，请核对服务后重新启动。") from None


def _current_documents(root: Path) -> tuple[dict, dict, str]:
    """读取唯一应用配置和当前技术规划，计算不持久化的启动指纹。"""

    try:
        application = ApplicationConfigRepository(root).load()
        plan = json.loads(technical_plan_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError, DatabaseCredentialError):
        raise ManagedDatabaseStartupError("请先完成应用配置并确认技术规划，再启动内置数据库应用。") from None
    if not isinstance(plan, dict) or plan.get("artifact_type") != "technical-plan" or plan.get("confirmation_status") != "confirmed":
        raise ManagedDatabaseStartupError("技术规划尚未确认，请先确认当前实体设计，再启动应用。")
    digest = hashlib.sha256(json.dumps([application, plan], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return application, plan, digest


def _require_current(root: Path, snapshot: StartupSnapshot) -> None:
    """等待、构建或取密期间发生配置和设计变更时拒绝旧启动快照。"""

    if _current_documents(root)[2] != snapshot.digest:
        raise ManagedDatabaseStartupError("应用配置或技术规划已变化，请重新启动，不能沿用本次数据库检查。")


def _client(client: ManagedRuntimeClient | None) -> ManagedRuntimeClient:
    """把基础客户端缺配置错误转换为启动资源问题，不暴露底层异常。"""

    try:
        return client or ManagedRuntimeClient(Settings.from_env())
    except ValueError:
        raise ManagedDatabaseStartupError("请在 Studio 后端配置 SaaS 地址和 MANAGED_DATABASE_SERVICE_KEY 后重新启动。") from None


def _check(
    root: Path, snapshot: StartupSnapshot, remote: ManagedRuntimeClient | None,
    on_readiness: Callable[[dict], None] | None = None,
) -> ManagedRuntimeClient:
    """核对本次完整表集合、资源身份和真实结构，不执行 SQL 或自动重建。"""

    trusted = None
    if on_readiness:
        on_readiness(database_check_report(snapshot.entities, status="checking", message="正在检查当前应用全部实体表。"))
    try:
        _require_current(root, snapshot)
        remote = _client(remote)
        try:
            readiness = TableReadiness.model_validate(remote.request(snapshot.datasource_key, "tables/check", sqls=snapshot.sqls))
        except ValidationError:
            raise ManagedDatabaseStartupError("SaaS 表结构检查响应无效，请核对服务后重新启动。") from None
        if readiness.datasourceId != snapshot.datasource_id:
            raise ManagedDatabaseStartupError("SaaS 数据源与当前应用注册绑定不一致，已停止启动。")
        if readiness.reason:
            raise ManagedDatabaseStartupError("内置数据库正在变更或存在未完成操作，请核对实体执行结果后重新启动。")
        names = [entry.tableName for entry in readiness.tables]
        if len(names) != len(set(names)) or set(names) != set(snapshot.table_names):
            raise ManagedDatabaseStartupError("SaaS 未完整检查当前应用的全部实体表，已停止启动。")
        # 身份和完整范围校验通过后才能关联实体；不展示部分或串应用的结构响应。
        _require_current(root, snapshot)
        trusted = readiness
        hints = {"NOT_CREATED": "尚未建表", "STRUCTURE_MISMATCH": "结构与当前设计不一致", "TABLE_CONFLICT": "表归属不可用"}
        failures = [f"{entry.tableName}：{hints.get(entry.status, '尚未就绪')}"
                    for entry in readiness.tables if entry.status != "SYNCED"]
        if not readiness.ready or failures:
            raise ManagedDatabaseStartupError("内置数据库尚未就绪，请查看实体表检查清单并处理异常项，再重新启动。")
    except ManagedDatabaseStartupError as exc:
        if on_readiness:
            on_readiness(database_check_report(snapshot.entities, status="blocked" if trusted else "error",
                                               message=str(exc), readiness=trusted))
        raise
    if on_readiness:
        on_readiness(database_check_report(snapshot.entities, status="ready", message="当前应用全部实体表结构一致。", readiness=trusted))
    return remote


def prepare_managed_startup(
    root: Path, *, client: ManagedRuntimeClient | None = None,
    on_readiness: Callable[[dict], None] | None = None,
) -> StartupSnapshot | None:
    """只对内置应用冻结完整设计并预检；外部模式保持原有连接解析路径。"""

    from app.services.entity_tables import prepare_entity_table

    if not (root / WORKSPACE_ARTIFACT_DIR / "application.json").is_file():
        return None
    try:
        application = load_application_json(root)
    except (OSError, ValueError, DatabaseCredentialError):
        # 配置尚不可读时无法认定为内置资源失败，交回通用配置错误路径。
        raise DatabaseCredentialError("当前应用配置无法读取，请修正配置后重新启动。") from None
    datasource = application.get("datasource") or {}
    if not isinstance(datasource, dict) or not isinstance(datasource.get("db"), dict) or datasource["db"].get("useBuiltin") is not True:
        return None
    application, plan, digest = _current_documents(root)
    try:
        binding = ManagedDatabaseRegistration.model_validate(application.get("managedDatabase") or {})
        if binding.status != "REGISTERED" or datasource.get("type") != "database":
            raise ValueError()
        entities = plan.get("entities")
        if not isinstance(entities, list) or len(entities) > 200:
            raise ValueError()
        previews = [prepare_entity_table(EntityTableRequest(action="prepare", workspaceRoot=str(root), entityId=entity["id"]))
                    for entity in entities]
    except (ValueError, KeyError, TypeError):
        raise ManagedDatabaseStartupError("内置数据库绑定或实体定义无效，请完成应用注册并重新确认技术规划。") from None
    names = tuple(preview["tableName"] for preview in previews)
    if len(names) != len(set(names)):
        raise ManagedDatabaseStartupError("当前技术规划存在重复实体表，请修订并重新确认。")
    snapshot = StartupSnapshot(binding.datasource_key, binding.datasource_id, digest, names,
                               tuple(preview["sql"] for preview in previews),
                               tuple((entity["id"], str(entity.get("name") or entity["id"]), preview["tableName"])
                                     for entity, preview in zip(entities, previews)))
    _check(root, snapshot, client, on_readiness)
    return snapshot


def resolve_managed_runtime(
    root: Path, snapshot: StartupSnapshot, *, client: ManagedRuntimeClient | None = None,
    on_readiness: Callable[[dict], None] | None = None,
) -> RuntimeConnection:
    """Java 创建前复查真实结构、一次取密，并丢弃身份或设计不一致的响应。"""

    remote = _check(root, snapshot, client, on_readiness)
    try:
        connection = RuntimeConnection.model_validate(remote.request(snapshot.datasource_key, "runtime-connection"))
    except ValidationError:
        raise ManagedDatabaseStartupError("SaaS 调试连接响应无效，已停止启动，请核对服务配置。") from None
    if connection.datasourceKey != snapshot.datasource_key or connection.datasourceId != snapshot.datasource_id:
        raise ManagedDatabaseStartupError("SaaS 调试连接与当前应用绑定不一致，已停止启动。")
    _require_current(root, snapshot)
    return connection
