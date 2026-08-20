from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, cast


DatasourceType = Literal["database", "static", "external_api"]
EnabledDatasourceType = Literal["database", "static"]
ENABLED_DATASOURCE_TYPES = frozenset({"database", "static"})
CANONICAL_DATASOURCE_TYPES = frozenset({"database", "static", "external_api"})


class DataSourcePolicyError(ValueError):
    """表示应用数据源配置不符合当前正式数据源策略。"""


def read_application_datasource_type(workspace_root: str | Path) -> DatasourceType:
    """从工作区 application.json 读取唯一权威的数据源类型。"""

    application_path = (
        Path(workspace_root).expanduser() / ".xcodeagent" / "application.json"
    )
    if not application_path.is_file():
        raise DataSourcePolicyError(
            "当前工作区缺少 .xcodeagent/application.json，无法确定数据源类型。"
        )

    try:
        application = json.loads(application_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataSourcePolicyError("当前工作区 application.json 无法读取。") from exc

    datasource = application.get("datasource") if isinstance(application, dict) else None
    raw_type = datasource.get("type") if isinstance(datasource, dict) else None
    if not isinstance(raw_type, str) or raw_type not in CANONICAL_DATASOURCE_TYPES:
        raise DataSourcePolicyError(
            "当前应用的数据源类型无效，仅支持 database、static、external_api。"
        )
    return cast(DatasourceType, raw_type)


def application_has_database_config(workspace_root: str | Path) -> bool:
    """判断创建应用时是否填写了可用的数据库连接信息。"""

    application_path = (
        Path(workspace_root).expanduser() / ".xcodeagent" / "application.json"
    )
    if not application_path.is_file():
        return False
    try:
        application = json.loads(application_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    datasource = application.get("datasource") if isinstance(application, dict) else None
    database = datasource.get("db") if isinstance(datasource, dict) else None
    if not isinstance(database, dict):
        return False
    plant_mode = database.get("plantMode")
    if isinstance(plant_mode, dict) and all(
        str(plant_mode.get(key) or "").strip()
        for key in ("domain", "userName", "schema")
    ):
        return True
    dbid_mode = database.get("dbidMode")
    if isinstance(dbid_mode, dict) and all(
        str(dbid_mode.get(key) or "").strip()
        for key in ("dbid", "domain", "userName", "schema")
    ):
        return True
    return False


def ensure_requirements_datasource_type(
    datasource_type: DatasourceType,
) -> EnabledDatasourceType:
    """拒绝当前创建规划流程尚未启用的 external_api 数据源。"""

    if not isinstance(datasource_type, str) or datasource_type not in CANONICAL_DATASOURCE_TYPES:
        raise DataSourcePolicyError("当前应用的数据源类型无效。")
    if datasource_type not in ENABLED_DATASOURCE_TYPES:
        raise DataSourcePolicyError("当前创建应用流程暂不支持 external_api 数据源。")
    return cast(EnabledDatasourceType, datasource_type)


def ensure_enabled_datasource_type(
    datasource_type: DatasourceType,
) -> EnabledDatasourceType:
    """校验正式规划链路支持的数据源类型，含数据库、外部 API 与静态数据。"""

    if not isinstance(datasource_type, str) or datasource_type not in CANONICAL_DATASOURCE_TYPES:
        raise DataSourcePolicyError("当前应用的数据源类型无效，仅支持 database、static、external_api。")
    return cast(EnabledDatasourceType, datasource_type)


def datasource_type_from_artifact(
    artifact: dict[str, Any],
    *,
    fallback: EnabledDatasourceType | None = None,
) -> EnabledDatasourceType:
    """从正式工件推导规划默认类型：全 static 返回 static，其余返回 database。"""

    sources = artifact.get("data_sources")
    source_types = {
        str(source.get("type") or "").strip()
        for source in sources
        if isinstance(source, dict)
    } if isinstance(sources, list) else set()
    for source_type in source_types:
        if source_type not in CANONICAL_DATASOURCE_TYPES:
            raise DataSourcePolicyError(
                f"正式工件包含非法数据源类型：{source_type or '空'}。"
            )
    if not source_types:
        return ensure_enabled_datasource_type(fallback or "database")
    if source_types <= {"static"}:
        return "static"
    return "database"


def apply_authoritative_datasource_type(
    spec: dict[str, Any],
    datasource_type: DatasourceType,
) -> dict[str, Any]:
    """规范化数据源类型：合法类型原样保留，缺省类型用默认值补齐，非法类型拒绝。"""

    if not isinstance(datasource_type, str) or datasource_type not in CANONICAL_DATASOURCE_TYPES:
        raise DataSourcePolicyError("不能投影无效的数据源类型。")

    projected = deepcopy(spec)
    sources = projected.get("data_sources")
    if not isinstance(sources, list):
        return projected

    projected["data_sources"] = [
        {
            **source,
            "type": (
                str(source.get("type") or "").strip()
                if str(source.get("type") or "").strip() in CANONICAL_DATASOURCE_TYPES
                else datasource_type
            ),
        }
        for source in sources
        if isinstance(source, dict)
    ]
    return projected
