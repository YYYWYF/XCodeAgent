"""内置表只读数据和最近操作投影，身份仅来自当前工作区权威配置。"""

from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.domain.managed_database_operations import parse_database_operations
from app.services.managed_database_agui import ManagedDatabaseAgUiClient
from app.services.managed_database_metadata import ManagedColumn, _binding, _require_unchanged, read_managed_tables
from app.services.managed_table_client import ManagedTableClient


class DataPage(BaseModel):
    """严格验证数据展示合同，单元格文本不会经过浮点数转换。"""

    model_config = ConfigDict(strict=True, extra="forbid")
    status: Literal["SUCCEEDED"]
    datasourceKey: str
    tableName: str
    columns: list[ManagedColumn] = Field(min_length=1, max_length=200)
    rows: list[dict[str, str | None]] = Field(max_length=100)
    pageNumber: int = Field(ge=1)
    pageSize: int = Field(ge=1, le=100)
    hasMore: bool
    truncated: bool
    columnFormats: dict[str, Literal["TEXT", "HEX"]]
    filteringSupported: Literal[False]


def _managed_source(workspace: str, source_id: str):
    """只按当前工作区内置来源定位应用，不接受前端指定 datasourceKey。"""

    from app.services.data_sources import database_source_mode

    if database_source_mode(workspace, source_id) != "builtin":
        raise ValueError("数据预览和操作记录目前仅支持平台内置数据库。")
    return _binding(workspace)


def _available_table(workspace: str, source_id: str, table: str):
    """真实行查询必须先通过 SaaS 应用表目录，不能读取目录外表。"""

    binding = _managed_source(workspace, source_id)
    listing = read_managed_tables(workspace, source_id)
    if table not in {item["name"] for item in listing["tables"]}:
        raise ValueError("该表不在当前应用已建成的表清单中，请刷新数据源。")
    return binding


def read_table_page(workspace: str, source_id: str, table: str, page_number: int, page_size: int) -> dict[str, Any]:
    """读取当前表一页数据，完整生命周期及资源身份均通过后才返回。"""

    if page_number < 1 or not 1 <= page_size <= 100 or (page_number - 1) * page_size > 100000:
        raise ValueError("分页参数无效或超出支持的分页范围。")
    binding = _available_table(workspace, source_id, table)
    remote = ManagedDatabaseAgUiClient(Settings.from_env())
    raw = remote.query(binding.datasource_key, f"tables/{quote(table, safe='')}/data/run",
                       {"pageNumber": page_number, "pageSize": page_size, "filter": {"logic": "AND", "conditions": []}})
    try:
        page = DataPage.model_validate(raw)
    except ValidationError:
        raise ValueError("内置数据库表数据响应不符合接口合同。") from None
    if page.datasourceKey != binding.datasource_key or page.tableName != table or page.pageNumber != page_number or page.pageSize != page_size:
        raise ValueError("内置数据库表数据的应用、表或分页身份不一致。")
    names = [column.name for column in page.columns]
    if len(set(names)) != len(names) or set(page.columnFormats) != set(names) or len(page.rows) > page_size or (page.hasMore and len(page.rows) != page_size):
        raise ValueError("内置数据库表数据字段或行数无效。")
    if any(set(row) != set(names) or any(value is not None and len(value) > 4096 for value in row.values()) for row in page.rows):
        raise ValueError("内置数据库表数据与字段清单不一致。")
    _require_unchanged(workspace, binding)
    return {"sourceId": source_id, **page.model_dump()}


def read_table_operations(workspace: str, source_id: str, table: str) -> dict[str, Any]:
    """读取本应用最近一百条原操作中的精确表记录，不伪称完整历史。"""

    if not table or len(table) > 64:
        raise ValueError("目标表名无效。")
    # 历史查询本身不读业务表，不能因在途操作阻断用户核对原记录。
    binding = _managed_source(workspace, source_id)
    raw = ManagedTableClient(Settings.from_env()).request(binding.datasource_key, "table-operations?limit=100")
    entries = parse_database_operations(raw)
    _require_unchanged(workspace, binding)
    return {"historyLimit": 100, "operations": [entry.model_dump() for entry in entries
                                               if entry.targets_table(table)]}
