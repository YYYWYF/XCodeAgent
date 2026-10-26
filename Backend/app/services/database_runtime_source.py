"""统一生成配置与启动器的数据源选择，不从应用配置回退读取外部连接。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from app.persistence.data_sources import DataSourceStorageError, read_sources
from app.services.data_source_policy import uses_builtin_database
from app.services.database_credentials import DatabaseCredentialError, MySQLConnectionConfig, load_application_json
from app.services.data_sources import DataSourceError, public_catalog, resolve_database_config


@dataclass(frozen=True)
class RuntimeDatabaseSource:
    """仅携带数据源身份和连接模式，不保存连接凭据。"""

    source_id: str
    mode: Literal["builtin", "direct", "dbid"]


def select_runtime_database(
    workspace_root: str | Path | None, *, source_id: str | None = None,
) -> RuntimeDatabaseSource | None:
    """按权威模拟开关和当前目录选择数据库，显式引用失效时禁止换库。"""

    if workspace_root is None or not str(workspace_root).strip():
        raise DatabaseCredentialError("缺少当前应用工作区，无法读取数据库配置。")
    application = load_application_json(workspace_root)
    builtin = uses_builtin_database(application)
    try:
        sources = [item for item in read_sources(workspace_root, detail=False) if item["type"] == "database"]
    except DataSourceStorageError as exc:
        raise DatabaseCredentialError(str(exc)) from exc
    if len(sources) > 1:
        raise DatabaseCredentialError("当前应用最多支持一个数据库数据源，无法确定启动连接。")
    if source_id is not None and (not sources or sources[0]["id"] != source_id):
        raise DatabaseCredentialError("任务引用的数据库数据源已不存在，请在应用内配置数据库并重新确认接口映射。")
    if not sources:
        # 模拟模式未初始化目录时仍可告知生成器托管注入策略；资源准入由启动器负责。
        return RuntimeDatabaseSource("application-database", "builtin") if builtin else None
    source = sources[0]
    mode = source.get("mode")
    if mode not in {"builtin", "direct", "dbid"} or (mode == "builtin") != builtin:
        raise DatabaseCredentialError("数据库目录与应用的模拟数据库配置不一致，请先检查数据源配置。")
    # 复用目录的结构与加密格式校验，但生成时不解密密码或调用 DBID 取密服务。
    try:
        public_catalog(workspace_root, source_id=source["id"])
    except DataSourceError as exc:
        raise DatabaseCredentialError(str(exc)) from exc
    except ValidationError:
        raise DatabaseCredentialError("数据库数据源配置格式无效，请在应用内检查配置。") from None
    return RuntimeDatabaseSource(source["id"], mode)


def resolve_runtime_mysql_config(
    workspace_root: str | Path, source: RuntimeDatabaseSource,
) -> MySQLConnectionConfig:
    """只为启动器解析当前外部数据源凭据，模拟连接继续使用 SaaS 专用流程。"""

    if source.mode == "builtin":
        raise DatabaseCredentialError("模拟数据库连接由平台专用启动流程注入。")
    try:
        config = resolve_database_config(workspace_root, source.source_id)
    except DataSourceError as exc:
        raise DatabaseCredentialError(str(exc)) from exc
    return MySQLConnectionConfig(
        host=config["host"], port=config["port"], user=config["user"],
        password=config["password"], database=config["database"],
    )
