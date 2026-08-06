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
    """校验当前正式规划链路已启用的数据源类型。"""

    return ensure_requirements_datasource_type(datasource_type)


def datasource_type_from_artifact(
    artifact: dict[str, Any],
    *,
    fallback: EnabledDatasourceType | None = None,
) -> EnabledDatasourceType:
    """从正式工件提取唯一数据源类型，并拒绝混合类型和旧类型。"""

    sources = artifact.get("data_sources")
    source_types = {
        str(source.get("type") or "")
        for source in sources
        if isinstance(source, dict)
    } if isinstance(sources, list) else set()
    if not source_types and fallback is not None:
        return ensure_enabled_datasource_type(fallback)
    if len(source_types) != 1:
        raise DataSourcePolicyError("正式工件必须包含唯一且一致的数据源类型。")
    return ensure_enabled_datasource_type(cast(DatasourceType, source_types.pop()))


def apply_authoritative_datasource_type(
    spec: dict[str, Any],
    datasource_type: DatasourceType,
) -> dict[str, Any]:
    """复制正式工件，并把所有数据源类型投影为应用配置类型。"""

    if not isinstance(datasource_type, str) or datasource_type not in CANONICAL_DATASOURCE_TYPES:
        raise DataSourcePolicyError("不能投影无效的数据源类型。")

    projected = deepcopy(spec)
    sources = projected.get("data_sources")
    if not isinstance(sources, list):
        return projected

    projected["data_sources"] = [
        {**source, "type": datasource_type}
        for source in sources
        if isinstance(source, dict)
    ]
    return projected
