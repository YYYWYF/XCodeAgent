"""读取 SaaS 应用范围的真实元数据，内置挂载不向平台公开真实 Schema。"""

from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.domain.managed_database import ManagedDatabaseRegistration
from app.services.application_config.repository import ApplicationConfigRepository
from app.services.data_source_policy import workspace_uses_builtin_database
from app.services.managed_table_client import ManagedTableClient, ManagedTableError
from app.services.managed_database_client import ManagedDatabaseRegistrationError


class MetadataModel(BaseModel):
    """只投射当前接口允许的元数据字段，忽略额外审计信息。"""

    model_config = ConfigDict(strict=True, extra="ignore")


class ManagedTable(MetadataModel):
    """SaaS 已建成且真实存在的表摘要。"""

    tableName: str = Field(min_length=1, max_length=64)
    description: str = Field(max_length=2048)
    columnCount: int = Field(ge=1)


class ManagedTables(MetadataModel):
    """表清单必须声明对应的应用身份。"""

    datasourceKey: str
    tables: list[ManagedTable] = Field(max_length=2000)


class ManagedColumn(MetadataModel):
    """保留数据库实际字段信息，不使用本地设计替代真实字段。"""

    name: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=128)
    nullable: bool
    primaryKey: bool
    autoIncrement: bool
    defaultValue: str | None = None
    description: str = Field(max_length=2048)


class ManagedColumns(MetadataModel):
    """单表回执必须同时匹配应用与目标表。"""

    datasourceKey: str
    tableName: str
    description: str
    columns: list[ManagedColumn] = Field(min_length=1, max_length=4096)


def _binding(workspace: str | Path) -> ManagedDatabaseRegistration:
    """只从权威 application.json 读取已成功注册的内置挂载身份。"""

    application = ApplicationConfigRepository(workspace).load()
    datasource = application.get("datasource") or {}
    if datasource.get("type") != "database" or (datasource.get("db") or {}).get("useBuiltin") is not True:
        raise ValueError("当前应用未启用平台内置数据库。")
    try:
        binding = ManagedDatabaseRegistration.model_validate(application.get("managedDatabase") or {})
    except ValidationError:
        raise ValueError("平台内置数据库尚未完成应用注册。") from None
    if binding.status != "REGISTERED":
        raise ValueError("平台内置数据库尚未确认注册成功。")
    return binding


def _require_unchanged(workspace: str | Path, binding: ManagedDatabaseRegistration) -> None:
    """查询期间应用绑定变化时丢弃结果，不能把另一份挂载快照返回给调用方。"""

    if _binding(workspace) != binding:
        raise ValueError("应用数据库绑定已变化，请重新读取数据表。")


def _read_response(remote: ManagedTableClient, datasource_key: str, path: str) -> Any:
    """把只读资源错误转为数据源提示，不引导用户重发执行或查看无关操作。"""

    try:
        return remote.request(datasource_key, path)
    except ManagedTableError as exc:
        if exc.issue["code"] == "RESOURCE_BUSY":
            raise ValueError("平台内置数据库存在未完成操作或资源繁忙，请稍后刷新数据源。") from None
        if exc.issue["code"] == "TABLE_BINDING_CONFLICT":
            raise ValueError("当前表的应用归属不可用，请刷新数据源后重新选择。") from None
        raise ValueError("平台内置数据库元数据读取失败，请确认服务状态后刷新数据源。") from None
    except ManagedDatabaseRegistrationError:
        raise ValueError("暂时无法读取平台内置数据库，请确认服务状态后刷新数据源。") from None


def read_managed_tables(workspace: str | Path, source_id: str, *, client: ManagedTableClient | None = None) -> dict[str, Any]:
    """从 SaaS 读取本应用表目录，schema 留空表示由固定挂载解析。"""

    binding = _binding(workspace)
    remote = client or ManagedTableClient(Settings.from_env())
    raw = _read_response(remote, binding.datasource_key, "tables")
    try:
        result = ManagedTables.model_validate(raw)
    except ValidationError:
        raise ValueError("平台内置数据库返回的表清单不符合接口合同。") from None
    names = [table.tableName for table in result.tables]
    if result.datasourceKey != binding.datasource_key or len(names) != len(set(names)):
        raise ValueError("平台内置数据库表清单的应用身份或表名无效。")
    _require_unchanged(workspace, binding)
    return {"sourceId": source_id, "schema": "", "managedDatasourceKey": binding.datasource_key,
            "tables": [{"name": item.tableName, "description": item.description, "columnCount": item.columnCount}
                       for item in result.tables]}


def read_managed_columns(workspace: str | Path, source_id: str, table: str, *, client: ManagedTableClient | None = None) -> dict[str, Any]:
    """读取实际列并保持完整表候选；不接受 Schema 或任意 datasourceKey 输入。"""

    binding = _binding(workspace)
    remote = client or ManagedTableClient(Settings.from_env())
    listing = read_managed_tables(workspace, source_id, client=remote)
    if table not in {item["name"] for item in listing["tables"]}:
        raise ValueError("该表不在当前应用已建成的表清单中，请刷新数据源。")
    raw = _read_response(remote, binding.datasource_key, f"tables/{quote(table, safe='')}/columns")
    try:
        result = ManagedColumns.model_validate(raw)
    except ValidationError:
        raise ValueError("平台内置数据库返回的字段信息不符合接口合同。") from None
    names = [column.name for column in result.columns]
    if result.datasourceKey != binding.datasource_key or result.tableName != table or len(names) != len(set(names)):
        raise ValueError("平台内置数据库字段信息的应用或表身份不一致。")
    _require_unchanged(workspace, binding)
    return {**listing, "table": table, "description": result.description,
            "columns": [{**column.model_dump(), "required": not column.nullable} for column in result.columns]}


def validate_managed_snapshots(workspace: str | Path, design: dict[str, Any]) -> None:
    """开发门禁检查内置表的已确认引用，真实删除、改类型或改约束后要求重新确认。"""

    from app.services.data_sources import DataSourceError, database_source_mode

    builtin = workspace_uses_builtin_database(workspace)
    for snapshot in design.get("sourceSnapshots") or []:
        if snapshot.get("sourceType") != "database":
            continue
        saved = snapshot.get("details") or {}
        source_id = snapshot.get("sourceId") or ""
        # 外置冻结映射沿用原门禁，不依赖当前可编辑目录或 SaaS；残留内置快照仍需阻断重绑。
        if not saved.get("managedDatasourceKey") and not builtin:
            continue
        try:
            mode = database_source_mode(workspace, source_id)
        except DataSourceError:
            if saved.get("managedDatasourceKey") or source_id == "application-database":
                raise ValueError("API 映射的内置数据库来源已变化，请重新确认。") from None
            continue
        if mode != "builtin":
            if saved.get("managedDatasourceKey"):
                raise ValueError("API 映射的内置数据库来源已变化，请重新确认。")
            continue
        # 内置来源必须携带当前资源身份，不能因快照缺字段而跳过真实结构复核。
        if not saved.get("managedDatasourceKey"):
            raise ValueError("内置数据库映射快照缺少数据源 Key，请重新确认 API 映射。")
        current = read_managed_columns(workspace, source_id, saved.get("table") or "")
        if saved.get("managedDatasourceKey") != current["managedDatasourceKey"] or saved.get("schema") != "":
            raise ValueError("API 映射的内置数据库绑定已变化，请重新确认。")
        columns = {column["name"]: column for column in current["columns"]}
        for old in saved.get("columns") or []:
            actual = columns.get(old.get("name"))
            if actual is None or any(old.get(key) != actual.get(key) for key in
                                     ("type", "required", "primaryKey", "autoIncrement", "defaultValue")):
                raise ValueError(f"内置表 {current['table']} 的引用字段 {old.get('name')} 已变化，请重新配置并确认 API 映射。")
