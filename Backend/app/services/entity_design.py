"""实体设计服务：数据源选择、分方案设计、确定性校验与表操作落地。

实体设计是人机协同流程：用户先选择当前实体的数据源（数据库 / 外部 API /
静态数据），再按数据源类型进入对应的设计方案；模型负责提供建议与初稿，
用户负责选择、导入、绑定与最终确认。本模块只提供确定性逻辑：真实数据库
上下文、同名绑定建议、结构化差异与数据库操作编译、确定性校验和表操作执行
证据，不直接调用模型。
"""

from __future__ import annotations

import json
import re
import datetime
from copy import deepcopy
from typing import Any

from app.services.database_execution import (
    create_database_execution_context,
    execute_database_plan,
)
from app.services.entity_design_assist import SUPPORTED_ASSIST_TYPES
from app.services.database_schema_summary import (
    inspect_mysql_schema,
    inspect_mysql_table,
)
from app.services.entity_definitions import (
    ENTITY_FIELD_NAME_RE,
    MYSQL_TYPE_BY_FIELD_TYPE,
    RESERVED_ENTITY_FIELD,
    data_source_type_label,
    database_operation_field_errors,
    entity_mysql_target_table,
    entity_table_name,
    normalize_data_source_type,
    normalize_entity,
)


ENTITY_DESIGN_STAGE_DATA_SOURCE_SELECTION = "data_source_selection"
ENTITY_DESIGN_STAGE_DATABASE = "database_design"
ENTITY_DESIGN_STAGE_EXTERNAL_API_INPUT = "external_api_input"
ENTITY_DESIGN_STAGE_STATIC = "static_design"
ENTITY_DESIGN_STAGE_REVIEW_READY = "review_ready"
ENTITY_DESIGN_STAGE_CONFIRMED = "confirmed"

ENTITY_DESIGN_ACTIONS = {
    "select_data_source",
    "submit_external_api",
    "submit_static_data",
    "submit_bindings",
    "approve_table_generation",
    "list_tables",
    "select_table",
    "ai_assist",
    "execute_add_columns",
    "execute_create_table",
    "submit_entity_design",
}

_MAX_AVAILABLE_TABLES = 40
_MAX_BINDINGS = 200
_MAX_DIFFERENCES = 200
_MAX_OPERATIONS = 100
_MAX_SEED_ROWS = 200


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """只保留列表中的字典项，避免脏数据导致连锁 get 崩溃。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    """把字符串或列表规整为去空字符串列表。"""

    if isinstance(value, str):
        return [item.strip() for item in value.replace("，", "\n").replace(";", "\n").splitlines() if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def entity_design_stage(detail: Any) -> str:
    """返回实体设计当前阶段，缺失时按是否已确认回退。"""

    if not isinstance(detail, dict):
        return ENTITY_DESIGN_STAGE_DATA_SOURCE_SELECTION
    if str(detail.get("status") or "") == "confirmed":
        return ENTITY_DESIGN_STAGE_CONFIRMED
    stage = str(detail.get("design_stage") or "").strip()
    if stage:
        return stage
    return (
        ENTITY_DESIGN_STAGE_REVIEW_READY
        if detail.get("data_source_type")
        else ENTITY_DESIGN_STAGE_DATA_SOURCE_SELECTION
    )


def entity_design_action_payload(
    entity_id: str,
    action: str,
    **values: Any,
) -> dict[str, Any]:
    """构造结构化实体设计动作，供前端 clarificationAnswers 提交。"""

    payload: dict[str, Any] = {
        "action": action,
        "entity_id": entity_id,
    }
    payload.update(values)
    return payload


def normalize_entity_design_action(value: Any) -> dict[str, Any] | None:
    """把用户提交的实体设计动作规整为稳定结构；非法动作返回 None。"""

    if not isinstance(value, dict):
        return None
    action = str(value.get("action") or "").strip()
    if action not in ENTITY_DESIGN_ACTIONS:
        return None
    entity_id = str(value.get("entity_id") or "").strip()
    if not entity_id:
        return None
    normalized: dict[str, Any] = {
        "action": action,
        "entity_id": entity_id,
    }
    if action == "select_data_source":
        raw_source_type = str(value.get("data_source_type") or "").strip()
        if raw_source_type not in {"database", "external_api", "static"}:
            return None
        normalized["data_source_type"] = raw_source_type
        return normalized
    if action == "submit_external_api":
        api_info = value.get("api_info") if isinstance(value.get("api_info"), dict) else {}
        normalized["api_info"] = _normalize_external_api_info(api_info)
        return normalized
    if action == "submit_static_data":
        normalized["seed_rows"] = _bounded_dict_list(value.get("seed_rows"), _MAX_SEED_ROWS)
        field_values = value.get("field_values")
        normalized["field_values"] = (
            {
                str(key).strip(): _string_list(items)[:200]
                for key, items in field_values.items()
                if str(key).strip()
            }
            if isinstance(field_values, dict)
            else {}
        )
        return normalized
    if action == "submit_bindings":
        normalized["bindings"] = _bounded_dict_list(value.get("bindings"), _MAX_BINDINGS)
        matched_table = str(value.get("matched_table") or "").strip()
        if matched_table:
            normalized["matched_table"] = matched_table
        return normalized
    if action == "list_tables":
        return normalized
    if action == "select_table":
        table_name = str(value.get("table_name") or "").strip()
        if not table_name:
            return None
        normalized["table_name"] = table_name
        return normalized
    if action == "ai_assist":
        assist_type = str(value.get("assist_type") or "").strip()
        if assist_type not in SUPPORTED_ASSIST_TYPES:
            return None
        normalized["assist_type"] = assist_type
        instruction = str(value.get("instruction") or "").strip()[:2000]
        if instruction:
            normalized["instruction"] = instruction
        context = value.get("context")
        if isinstance(context, dict):
            trimmed_context: dict[str, Any] = {}
            for key, items in list(context.items())[:20]:
                if isinstance(items, list):
                    trimmed_context[str(key)] = _bounded_dict_list(items, 100)
                else:
                    trimmed_context[str(key)] = items
            normalized["context"] = trimmed_context
        return normalized
    if action == "execute_add_columns":
        table_name = str(value.get("table_name") or "").strip()
        fields = _bounded_dict_list(value.get("fields"), _MAX_BINDINGS)
        if not table_name or not fields:
            return None
        normalized["table_name"] = table_name
        normalized["fields"] = fields
        return normalized
    if action == "execute_create_table":
        proposal = value.get("proposal")
        if not isinstance(proposal, dict) or not proposal.get("name"):
            return None
        normalized["proposal"] = proposal
        return normalized
    if action == "submit_entity_design":
        data_source_type = str(value.get("data_source_type") or "").strip()
        if data_source_type not in {"database", "external_api", "static"}:
            return None
        normalized["data_source_type"] = data_source_type
        for key in ("database_design", "external_api_design", "static_design"):
            design = value.get(key)
            if isinstance(design, dict):
                normalized[key] = design
        normalized["business_rules"] = _bounded_dict_list(
            value.get("business_rules"), _MAX_BINDINGS
        )
        normalized["relationships"] = _bounded_dict_list(
            value.get("relationships"), _MAX_BINDINGS
        )
        normalized["acceptance_criteria"] = _string_list(value.get("acceptance_criteria"))
        normalized["risks"] = _string_list(value.get("risks"))
        return normalized
    if action == "approve_table_generation":
        return normalized
    return None


def _normalize_external_api_info(value: Any) -> dict[str, Any]:
    """规整外部 API 信息：路径、请求方式、请求体与返回体。"""

    if not isinstance(value, dict):
        return {}
    method = str(value.get("method") or "GET").strip().upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        method = "GET"
    return {
        "path": str(value.get("path") or "").strip()[:1000],
        "method": method,
        "request_body": _bounded_json_like(value.get("request_body")),
        "response_body": _bounded_json_like(value.get("response_body")),
    }


def _bounded_json_like(value: Any) -> Any:
    """限制请求/返回体的体积，防止超大文档进入设计上下文。"""

    if value is None:
        return None
    try:
        serialized = json.dumps(value, ensure_ascii=False)
        if len(serialized) > 24000:
            return {"_truncated": True, "note": "外部 API 请求/返回体超过 24000 字符，已截断存储。"}
    except (TypeError, ValueError):
        return str(value)[:24000]
    return deepcopy(value)


def _bounded_dict_list(value: Any, limit: int) -> list[dict[str, Any]]:
    """规整为有限长度的字典列表。"""

    return _dict_items(value)[:limit]


def _default_constraints(fields: Any) -> list[dict[str, Any]]:
    """从字段的 enum_values 确定性生成默认取值约束，供前端首屏展示。"""

    constraints: list[dict[str, Any]] = []
    for field in _dict_items(fields):
        field_name = str(field.get("name") or "").strip()
        enum_values = [
            str(item).strip()
            for item in (field.get("enum_values") or [])
            if str(item).strip()
        ]
        if field_name and enum_values:
            constraints.append(
                {
                    "field": field_name,
                    "values": list(dict.fromkeys(enum_values)),
                }
            )
    return constraints


def entity_design_selection_summary(
    entity: dict[str, Any],
    *,
    database_context_ready: bool = False,
) -> dict[str, Any]:
    """构造数据源选择阶段的结构化摘要，供前端渲染选择界面。"""

    normalized = normalize_entity(entity, 0, with_types=True)
    entity_id = str(normalized.get("id") or "")
    fields = normalized.get("fields") or []
    options = [
        {
            "value": "database",
            "label": data_source_type_label("database"),
            "available": True,
            "description": (
                "绑定数据库表结构与字段；连接已配置时可展示可用表清单，"
                "无对应表时可生成目标表结构并落地表操作。"
            ),
        },
        {
            "value": "external_api",
            "label": data_source_type_label("external_api"),
            "available": True,
            "description": "补充外部 API 的路径、请求方式、请求体与返回体，并绑定返回体字段。",
        },
        {
            "value": "static",
            "label": data_source_type_label("static"),
            "available": True,
            "description": "直接构建字段取值、枚举与种子数据。",
        },
    ]
    return {
        "stage": ENTITY_DESIGN_STAGE_DATA_SOURCE_SELECTION,
        "entity_id": entity_id,
        "entity_name": str(normalized.get("name") or entity_id),
        "entity_description": str(normalized.get("description") or ""),
        "field_count": len(fields),
        "default_constraints": _default_constraints(fields),
        "fields": [
            {
                "name": str(field.get("name") or ""),
                "label": str(field.get("label") or field.get("name") or ""),
                "type": str(field.get("type") or "text"),
                "required": bool(field.get("required")),
                **(
                    {"enum_values": list(field["enum_values"])}
                    if field.get("enum_values")
                    else {}
                ),
            }
            for field in fields
            if field.get("name")
        ],
        "database_context_ready": database_context_ready,
        "data_source_options": options,
    }


def prepare_database_design(
    project_plan: dict[str, Any],
    entity: Any,
    detail: dict[str, Any],
    *,
    workspace_root: str | None = None,
) -> dict[str, Any]:
    """为数据库数据源实体组装真实上下文、绑定建议、结构化差异与表操作。"""

    del project_plan
    normalized = normalize_entity(entity, 0, with_types=True)
    entity_id = str(normalized.get("id") or "")
    target_table = entity_mysql_target_table(normalized)
    target_name = str(target_table.get("name") or entity_table_name(entity_id))
    target_columns = {
        str(column.get("name") or ""): column
        for column in _dict_items(target_table.get("columns"))
        if column.get("name")
    }
    schema_context = inspect_mysql_schema(
        {
            "entity_id": entity_id,
            "data_source_id": str(detail.get("data_source_id") or ""),
        },
        workspace_root=workspace_root,
    )
    available_tables: list[dict[str, Any]] = []
    if schema_context.get("status") == "completed":
        available_tables = _dict_items(schema_context.get("tables"))[:_MAX_AVAILABLE_TABLES]
    table_map = {
        str(table.get("table_name") or table.get("name") or ""): table
        for table in available_tables
    }
    matched_table = table_map.get(target_name)
    bindings: list[dict[str, Any]] = []
    differences: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []

    if matched_table is not None:
        actual_columns = {
            str(column.get("name") or ""): column
            for column in _dict_items(matched_table.get("columns"))
            if column.get("name")
        }
        for field in normalized.get("fields", []):
            field_name = str(field.get("name") or "")
            if not field_name:
                continue
            actual = actual_columns.get(field_name)
            if actual is not None:
                bindings.append(
                    {
                        "entity_field": field_name,
                        "table": target_name,
                        "table_column": field_name,
                        "rule": "same_name",
                        "suggestion": True,
                    }
                )
                expected_type = MYSQL_TYPE_BY_FIELD_TYPE.get(
                    str(field.get("type") or "text"),
                    "VARCHAR(255)",
                )
                actual_type = str(actual.get("type") or "").strip()
                if actual_type and not _mysql_type_family_matches(expected_type, actual_type):
                    differences.append(
                        {
                            "kind": "type_mismatch",
                            "field": field_name,
                            "table": target_name,
                            "expected": expected_type,
                            "actual": actual_type,
                            "resolution": "按实体设计调整列类型",
                        }
                    )
                    operations.append(
                        {
                            "id": f"alter_type_{target_name}_{field_name}",
                            "operation": "alter_column_type",
                            "table": target_name,
                            "column": field_name,
                            "to": {
                                "type": expected_type,
                                "nullable": not bool(field.get("required")),
                            },
                            "source": "entity_design",
                        }
                    )
            else:
                bindings.append(
                    {
                        "entity_field": field_name,
                        "table": target_name,
                        "table_column": field_name,
                        "rule": "add_column",
                        "suggestion": True,
                    }
                )
                differences.append(
                    {
                        "kind": "missing_column",
                        "field": field_name,
                        "table": target_name,
                        "expected": str(target_columns.get(field_name, {}).get("type") or "VARCHAR(255)"),
                        "actual": "无",
                        "resolution": "新增列",
                    }
                )
                operations.append(
                    {
                        "id": f"add_{target_name}_{field_name}",
                        "operation": "add_column",
                        "table": target_name,
                        "column": field_name,
                        "to": {
                            "type": MYSQL_TYPE_BY_FIELD_TYPE.get(
                                str(field.get("type") or "text"),
                                "VARCHAR(255)",
                            ),
                            "nullable": not bool(field.get("required")),
                            "comment": str(field.get("label") or field.get("description") or field_name),
                        },
                        "source": "entity_design",
                    }
                )
        binding_status = "matched"
        table_generation = {"required": False, "proposal": target_table, "approved": False}
    else:
        binding_status = "no_match" if available_tables else "no_context"
        for field in normalized.get("fields", []):
            field_name = str(field.get("name") or "")
            if not field_name:
                continue
            bindings.append(
                {
                    "entity_field": field_name,
                    "table": target_name,
                    "table_column": field_name,
                    "rule": "generated_table",
                    "suggestion": True,
                }
            )
        differences.append(
            {
                "kind": "missing_table",
                "field": "id",
                "table": target_name,
                "expected": "目标表",
                "actual": "无",
                "resolution": "生成目标表结构并绑定字段（需用户审批）",
            }
        )
        table_generation = {"required": True, "proposal": target_table, "approved": False}

    detail["database_design"] = {
        "schema_context": _bounded_schema_context(schema_context),
        "database_name": str(schema_context.get("database") or ""),
        "available_tables": available_tables,
        "matched_table": target_name if matched_table is not None else None,
        "binding_status": binding_status,
        "bindings": bindings[:_MAX_BINDINGS],
        "differences": differences[:_MAX_DIFFERENCES],
        "database_operations": operations[:_MAX_OPERATIONS],
        "table_generation": table_generation,
        "validation_errors": [],
    }
    return detail


def list_database_tables(
    detail: dict[str, Any],
    *,
    workspace_root: str | None = None,
) -> dict[str, Any]:
    """按用户请求查询当前数据库的表清单并写入实体设计。

    不做表结构推导、不生成绑定建议；仅当用户在数据库方案卡片主动点击查询时
    才调用受控 MySQL 工具，查询失败时保留可重试状态。
    """

    database_design = (
        detail.get("database_design")
        if isinstance(detail.get("database_design"), dict)
        else {}
    )
    schema_context = inspect_mysql_schema(
        {
            "entity_id": str(detail.get("entity_id") or ""),
            "data_source_id": str(detail.get("data_source_id") or ""),
        },
        workspace_root=workspace_root,
    )
    available_tables = (
        [
            {
                "name": str(table.get("table_name") or table.get("name") or ""),
                "comment": str(table.get("comment") or ""),
            }
            for table in _dict_items(schema_context.get("tables"))
            if table.get("table_name")
        ][:_MAX_AVAILABLE_TABLES]
        if schema_context.get("status") == "completed"
        else []
    )
    database_design["schema_context"] = _bounded_schema_context(schema_context)
    database_design["database_name"] = str(schema_context.get("database") or "")
    database_design["available_tables"] = available_tables
    database_design["table_query_status"] = schema_context.get("status")
    database_design["table_query_message"] = str(
        schema_context.get("message") or schema_context.get("summary") or ""
    )
    database_design["binding_status"] = "pending_table_selection"
    detail["database_design"] = database_design
    return detail


def select_database_table(
    detail: dict[str, Any],
    table_name: str,
    *,
    workspace_root: str | None = None,
) -> dict[str, Any]:
    """按用户选择的表查询字段结构并写入实体设计，等待用户绑定字段。"""

    database_design = (
        detail.get("database_design")
        if isinstance(detail.get("database_design"), dict)
        else {}
    )
    schema_context = inspect_mysql_table(
        {
            "entity_id": str(detail.get("entity_id") or ""),
            "data_source_id": str(detail.get("data_source_id") or ""),
        },
        table_name,
        workspace_root=workspace_root,
    )
    columns: list[dict[str, Any]] = []
    comment = ""
    if schema_context.get("status") == "completed":
        for table in _dict_items(schema_context.get("tables")):
            if str(table.get("table_name") or table.get("name") or "") == table_name:
                comment = str(table.get("comment") or "")
                columns = _dict_items(table.get("columns"))
                break
    database_design["schema_context"] = _bounded_schema_context(schema_context)
    database_design["database_name"] = str(schema_context.get("database") or "")
    database_design["matched_table"] = table_name
    database_design["selected_table"] = {
        "name": table_name,
        "comment": comment,
        "columns": columns[:_MAX_BINDINGS],
    }
    database_design["table_query_status"] = schema_context.get("status")
    database_design["table_query_message"] = str(
        schema_context.get("message") or schema_context.get("summary") or ""
    )
    database_design["binding_status"] = "pending_binding" if columns else "no_match"
    # 切换目标表后清空旧绑定，避免残留绑定指向错误表。
    database_design["bindings"] = []
    database_design["differences"] = []
    database_design["database_operations"] = []
    detail["database_design"] = database_design
    return detail


def _bounded_schema_context(schema_context: dict[str, Any]) -> dict[str, Any]:
    """裁剪数据库上下文，只保留模型和用户界面所需的稳定摘要。"""

    if not isinstance(schema_context, dict):
        return {}
    return {
        "status": schema_context.get("status"),
        "enabled": bool(schema_context.get("enabled")),
        "reason": schema_context.get("reason"),
        "message": schema_context.get("message"),
        "database": schema_context.get("database"),
        "database_exists": schema_context.get("database_exists"),
        "summary": schema_context.get("summary"),
        "scope": schema_context.get("scope"),
    }


def _mysql_type_family_matches(expected_mysql_type: str, actual_mysql_type: str) -> bool:
    """比较实体列类型与真实列类型是否属于同一兼容族，降低误报。"""

    return _mysql_type_family(expected_mysql_type) == _mysql_type_family(actual_mysql_type)


def _mysql_type_family(raw_type: str) -> str:
    """把 MySQL 类型规约到少量兼容族。"""

    compact = re.sub(r"\s+", " ", str(raw_type or "").upper()).strip()
    if compact.startswith("TINYINT(1)"):
        return "boolean"
    base = re.sub(r"\(.*", "", compact)
    if base in {"BOOL", "BOOLEAN"}:
        return "boolean"
    if base in {"TINYINT", "SMALLINT", "MEDIUMINT", "INT", "INTEGER", "BIGINT"}:
        return "number"
    if base in {"DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL"}:
        return "decimal"
    if base == "DATE":
        return "date"
    if base in {"DATETIME", "TIMESTAMP"}:
        return "datetime"
    return "text"


def attach_external_api_design(detail: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    """把用户补充的外部 API 信息与字段绑定写入实体设计。"""

    api_info = action.get("api_info") if isinstance(action.get("api_info"), dict) else {}
    response_body = api_info.get("response_body")
    response_fields = _extract_response_fields(response_body)
    top_level_fields = {name for name in response_fields if "." not in name}
    nested_by_leaf: dict[str, list[str]] = {}
    for path in response_fields:
        leaf = path.rsplit(".", 1)[-1]
        nested_by_leaf.setdefault(leaf, []).append(path)
    mappings: list[dict[str, Any]] = []
    for field in _dict_items(detail.get("fields")):
        field_name = str(field.get("name") or "")
        if not field_name:
            continue
        if field_name in top_level_fields:
            mappings.append(
                {
                    "entity_field": field_name,
                    "source_field": field_name,
                    "rule": "same_name",
                    "suggestion": True,
                }
            )
        elif (
            field_name in nested_by_leaf
            and len(nested_by_leaf[field_name]) == 1
        ):
            mappings.append(
                {
                    "entity_field": field_name,
                    "source_field": nested_by_leaf[field_name][0],
                    "rule": "nested_match",
                    "suggestion": True,
                }
            )
        else:
            mappings.append(
                {
                    "entity_field": field_name,
                    "source_field": "",
                    "rule": "manual",
                    "suggestion": False,
                }
            )
    detail["external_api_design"] = {
        "api_info": {
            "path": str(api_info.get("path") or ""),
            "method": str(api_info.get("method") or "GET"),
            "request_body": api_info.get("request_body"),
            "response_body": response_body,
        },
        "field_mappings": mappings,
        "validation_errors": [],
    }
    return detail


_MAX_RESPONSE_PATH_DEPTH = 3
_MAX_RESPONSE_PATHS = 300


def _extract_response_fields(response_body: Any) -> set[str]:
    """从返回体中提取字段路径集合（含嵌套对象/数组），兼容 JSON 字符串。"""

    payload = response_body
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return set()
    if isinstance(payload, list):
        payload = next((item for item in payload if isinstance(item, dict)), {})
    if not isinstance(payload, dict):
        return set()
    paths: set[str] = set()
    _collect_response_paths(payload, "", paths, 0)
    return paths


def _collect_response_paths(
    node: Any,
    prefix: str,
    paths: set[str],
    depth: int,
) -> None:
    """深度优先收集 JSON 字段路径；数组取首个元素继续展开并限制深度与数量。"""

    if depth > _MAX_RESPONSE_PATH_DEPTH or len(paths) >= _MAX_RESPONSE_PATHS:
        return
    if isinstance(node, list):
        if node:
            _collect_response_paths(node[0], prefix, paths, depth)
        return
    if not isinstance(node, dict):
        return
    for key in list(node.keys())[:100]:
        if not isinstance(key, str) or not key.strip():
            continue
        path = f"{prefix}.{key.strip()}" if prefix else key.strip()
        paths.add(path)
        _collect_response_paths(node.get(key), path, paths, depth + 1)


def attach_static_design(detail: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    """把用户自行构建的静态数据（种子行与字段取值）写入实体设计。"""

    detail["static_design"] = {
        "seed_rows": _bounded_dict_list(action.get("seed_rows"), _MAX_SEED_ROWS),
        "field_values": (
            {
                str(key).strip(): _string_list(items)[:200]
                for key, items in action.get("field_values", {}).items()
                if str(key).strip()
            }
            if isinstance(action.get("field_values"), dict)
            else {}
        ),
        "validation_errors": [],
    }
    return detail


def apply_entity_design_action(
    project_plan: dict[str, Any],
    detail: dict[str, Any],
    action: dict[str, Any],
    *,
    workspace_root: str | None = None,
) -> dict[str, Any]:
    """按用户动作推进实体设计阶段，返回更新后的实体设计。"""

    action_name = str(action.get("action") or "")
    entity = _find_entity(project_plan, str(detail.get("entity_id") or ""))
    if entity is None:
        raise ValueError(f"项目计划中不存在实体：{detail.get('entity_id')}")
    if action_name == "submit_external_api":
        attach_external_api_design(detail, action)
        detail["design_stage"] = ENTITY_DESIGN_STAGE_REVIEW_READY
        return detail
    if action_name == "submit_static_data":
        attach_static_design(detail, action)
        detail["design_stage"] = ENTITY_DESIGN_STAGE_REVIEW_READY
        return detail
    if action_name == "submit_bindings":
        bindings = _bounded_dict_list(action.get("bindings"), _MAX_BINDINGS)
        database_design = detail.get("database_design") if isinstance(detail.get("database_design"), dict) else {}
        database_design["bindings"] = bindings
        database_design["binding_status"] = "manual"
        matched_table = str(action.get("matched_table") or "").strip()
        if matched_table:
            database_design["matched_table"] = matched_table
        detail["database_design"] = database_design
        detail["design_stage"] = ENTITY_DESIGN_STAGE_REVIEW_READY
        return detail
    if action_name == "list_tables":
        list_database_tables(detail, workspace_root=workspace_root)
        detail["design_stage"] = ENTITY_DESIGN_STAGE_DATABASE
        return detail
    if action_name == "select_table":
        select_database_table(
            detail,
            str(action.get("table_name") or ""),
            workspace_root=workspace_root,
        )
        detail["design_stage"] = ENTITY_DESIGN_STAGE_DATABASE
        return detail
    if action_name == "approve_table_generation":
        database_design = detail.get("database_design") if isinstance(detail.get("database_design"), dict) else {}
        table_generation = (
            database_design.get("table_generation")
            if isinstance(database_design.get("table_generation"), dict)
            else {}
        )
        proposal = table_generation.get("proposal") or entity_mysql_target_table(entity)
        table_generation["approved"] = True
        table_generation["approval_source"] = "entity_design_user_approval"
        database_design["table_generation"] = table_generation
        operations = [
            item
            for item in _dict_items(database_design.get("database_operations"))
            if str(item.get("operation") or "") != "create_table"
        ]
        if not any(
            str(item.get("operation") or "") == "create_table"
            and str(item.get("table") or "") == str(proposal.get("name") or "")
            for item in operations
        ):
            operations.append(
                {
                    "id": f"create_{proposal.get('name') or 'entity_table'}",
                    "operation": "create_table",
                    "table": proposal,
                    "to": {},
                    "source": "entity_design_table_generation",
                    "approved_by_user": True,
                }
            )
        database_design["database_operations"] = operations[:_MAX_OPERATIONS]
        detail["database_design"] = database_design
        detail["design_stage"] = ENTITY_DESIGN_STAGE_REVIEW_READY
        return detail
    raise ValueError(f"不支持的实体设计动作：{action_name}")


def apply_complete_entity_design(
    detail: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, Any]:
    """写入单卡片一次性提交的完整实体设计，并停在 review 确认门禁前。"""

    data_source_type = str(action.get("data_source_type") or "").strip()
    if data_source_type not in {"database", "external_api", "static"}:
        raise ValueError("实体设计必须选择合法数据源类型。")
    detail["data_source_type"] = data_source_type
    detail["design_stage"] = ENTITY_DESIGN_STAGE_REVIEW_READY
    submitted_database_design = (
        action.get("database_design")
        if isinstance(action.get("database_design"), dict)
        else {}
    )
    existing_database_design = (
        detail.get("database_design")
        if isinstance(detail.get("database_design"), dict)
        else {}
    )
    # 单卡片提交只携带绑定等用户改动；服务端此前已从真实连接拿到库名与
    # schema_context，提交时合并保留，避免库名等信息在持久化时丢失。
    merged_database_design = dict(submitted_database_design)
    if not isinstance(merged_database_design.get("schema_context"), dict):
        existing_schema_context = existing_database_design.get("schema_context")
        if isinstance(existing_schema_context, dict):
            merged_database_design["schema_context"] = existing_schema_context
    existing_schema_context = (
        merged_database_design.get("schema_context")
        if isinstance(merged_database_design.get("schema_context"), dict)
        else {}
    )
    database_name = str(
        merged_database_design.get("database_name")
        or existing_schema_context.get("database")
        or existing_database_design.get("database_name")
        or ""
    ).strip()
    if database_name:
        merged_database_design["database_name"] = database_name
    # 提交载荷只带 matched_table 与 bindings，不带目标表列结构；
    # 从 schema_context 补全 selected_table，保证确认后的展示/信息面板
    # 能直接读取字段绑定与真实列，不依赖前端二次回退。
    if not isinstance(merged_database_design.get("selected_table"), dict):
        matched_table = str(merged_database_design.get("matched_table") or "").strip()
        schema_tables = existing_schema_context.get("tables")
        if matched_table and isinstance(schema_tables, list):
            matched_schema_table = next(
                (
                    table
                    for table in schema_tables
                    if isinstance(table, dict)
                    and str(table.get("table_name") or table.get("name") or "").strip()
                    == matched_table
                ),
                None,
            )
            if isinstance(matched_schema_table, dict):
                merged_database_design["selected_table"] = {
                    "name": str(
                        matched_schema_table.get("table_name")
                        or matched_schema_table.get("name")
                        or ""
                    ),
                    "comment": str(matched_schema_table.get("comment") or ""),
                    "columns": matched_schema_table.get("columns") or [],
                }
    detail["database_design"] = merged_database_design
    detail["external_api_design"] = (
        action.get("external_api_design")
        if isinstance(action.get("external_api_design"), dict)
        else {}
    )
    detail["static_design"] = (
        action.get("static_design")
        if isinstance(action.get("static_design"), dict)
        else {}
    )
    detail["business_rules"] = _bounded_dict_list(
        action.get("business_rules"), _MAX_BINDINGS
    )
    detail["relationships"] = _bounded_dict_list(
        action.get("relationships"), _MAX_BINDINGS
    )
    detail["acceptance_criteria"] = _string_list(action.get("acceptance_criteria"))
    detail["risks"] = _string_list(action.get("risks"))
    return detail


def entity_design_validation_errors(
    project_plan: dict[str, Any],
    detail: dict[str, Any],
) -> list[str]:
    """确定性校验实体设计：表名与字段必须落在实体定义内，不允许静默改实体。"""

    entity_id = str(detail.get("entity_id") or "")
    entity = _find_entity(project_plan, entity_id)
    if entity is None:
        return [f"项目计划中不存在实体：{entity_id}"]
    normalized = normalize_entity(entity, 0, with_types=True)
    allowed_columns = {RESERVED_ENTITY_FIELD}
    entity_fields: dict[str, dict[str, Any]] = {}
    for field in normalized.get("fields", []):
        field_name = str(field.get("name") or "")
        if field_name:
            allowed_columns.add(field_name)
            entity_fields[field_name] = field

    source_type = normalize_data_source_type(detail.get("data_source_type"))
    errors: list[str] = []
    if source_type == "database":
        errors.extend(_database_design_errors(project_plan, detail, allowed_columns, entity_fields))
    elif source_type == "external_api":
        errors.extend(_external_api_design_errors(detail, allowed_columns, entity_fields))
    elif source_type == "static":
        errors.extend(_static_design_errors(detail, allowed_columns, entity_fields))
    return list(dict.fromkeys(error for error in errors if error))


def _find_entity(project_plan: dict[str, Any], entity_id: str) -> dict[str, Any] | None:
    """从项目计划实体列表中定位实体。"""

    for entity in project_plan.get("entities") or []:
        if isinstance(entity, dict) and str(entity.get("id") or "") == entity_id:
            return entity
    return None


def _database_design_errors(
    project_plan: dict[str, Any],
    detail: dict[str, Any],
    allowed_columns: set[str],
    entity_fields: dict[str, dict[str, Any]],
) -> list[str]:
    """校验数据库方案的绑定、差异与表操作是否落在实体定义内。"""

    del entity_fields
    errors: list[str] = []
    database_design = detail.get("database_design") if isinstance(detail.get("database_design"), dict) else {}
    operations = _dict_items(database_design.get("database_operations"))
    origin = {
        "effective_source": {
            "kind": (
                "mysql_new_table"
                if any(str(op.get("operation") or "") == "create_table" for op in operations)
                else "mysql_existing"
            )
        },
        "database_operations": operations,
        "matched_table": str(database_design.get("matched_table") or ""),
    }
    errors.extend(database_operation_field_errors(project_plan.get("entities"), origin))
    bindings = _dict_items(database_design.get("bindings"))
    table_generation = database_design.get("table_generation")
    generation_required = (
        isinstance(table_generation, dict) and table_generation.get("required")
    )
    # 新流程必须由用户选择目标表；兼容旧版“生成目标表结构并审批”的存量设计。
    if (
        bindings
        and not generation_required
        and not str(database_design.get("matched_table") or "").strip()
    ):
        errors.append("数据库方案必须先选择目标表，再提交字段绑定。")
    for binding in bindings:
        entity_field = str(binding.get("entity_field") or "").strip()
        if not entity_field:
            errors.append("数据库字段绑定缺少 entity_field。")
            continue
        if entity_field not in allowed_columns:
            errors.append(f"数据库绑定字段 {entity_field} 不在实体定义内。")
        if not str(binding.get("table_column") or "").strip():
            errors.append(f"数据库绑定字段 {entity_field} 缺少 table_column。")
    if generation_required:
        proposal = table_generation.get("proposal")
        if not isinstance(proposal, dict) or not proposal.get("name"):
            errors.append("无对应表时必须生成目标表结构建议。")
        elif not table_generation.get("approved"):
            errors.append("无对应表时须先审批目标表生成方案，再确认实体设计。")
    return errors


def _external_api_design_errors(
    detail: dict[str, Any],
    allowed_columns: set[str],
    entity_fields: dict[str, dict[str, Any]],
) -> list[str]:
    """校验外部 API 方案：路径/方式必填，字段映射指向实体字段。"""

    del entity_fields
    errors: list[str] = []
    external_api_design = detail.get("external_api_design") if isinstance(detail.get("external_api_design"), dict) else {}
    api_info = external_api_design.get("api_info") if isinstance(external_api_design.get("api_info"), dict) else {}
    if not str(api_info.get("path") or "").strip():
        errors.append("外部 API 方案必须补充接口路径。")
    if not str(api_info.get("method") or "").strip():
        errors.append("外部 API 方案必须补充请求方式。")
    for mapping in _dict_items(external_api_design.get("field_mappings")):
        entity_field = str(mapping.get("entity_field") or "").strip()
        if not entity_field:
            errors.append("外部 API 字段映射缺少 entity_field。")
            continue
        if entity_field not in allowed_columns:
            errors.append(f"外部 API 映射字段 {entity_field} 不在实体定义内。")
    return errors


def _static_design_errors(
    detail: dict[str, Any],
    allowed_columns: set[str],
    entity_fields: dict[str, dict[str, Any]],
) -> list[str]:
    """校验静态数据方案：字段落在实体定义内，取值类型与枚举符合字段定义。"""

    errors: list[str] = []
    static_design = detail.get("static_design") if isinstance(detail.get("static_design"), dict) else {}
    for row_index, row in enumerate(_dict_items(static_design.get("seed_rows"))):
        for key, value in row.items():
            field_name = str(key).strip()
            if not field_name:
                continue
            if field_name not in allowed_columns:
                errors.append(f"静态数据第 {row_index + 1} 行字段 {field_name} 不在实体定义内。")
                continue
            type_error = _static_value_type_error(entity_fields.get(field_name, {}), value)
            if type_error:
                errors.append(f"静态数据第 {row_index + 1} 行字段 {field_name}{type_error}")
    field_values = static_design.get("field_values")
    if isinstance(field_values, dict):
        for key, values in field_values.items():
            field_name = str(key).strip()
            if not field_name:
                continue
            if field_name not in allowed_columns:
                errors.append(f"静态数据字段 {field_name} 不在实体定义内。")
                continue
            field = entity_fields.get(field_name, {})
            for value in values:
                type_error = _static_value_type_error(field, value)
                if type_error:
                    errors.append(f"静态数据字段 {field_name}{type_error}")
    return errors


def _static_value_type_error(field: dict[str, Any], value: Any) -> str:
    """按实体字段类型校验静态数据取值；空值不校验。"""

    if value is None or str(value).strip() == "":
        return ""
    field_type = str(field.get("type") or "text")
    if field_type in {"number", "decimal"}:
        if isinstance(value, bool) or not _is_number_like(value):
            return f" 取值 {value} 必须是数字。"
    elif field_type == "boolean":
        if isinstance(value, bool):
            return ""
        text = str(value).strip().lower()
        if text not in {"true", "false", "1", "0"}:
            return f" 取值 {value} 必须是布尔值。"
    elif field_type == "date":
        if not _is_date_like(value, with_time=False):
            return f" 取值 {value} 必须是日期（YYYY-MM-DD）。"
    elif field_type == "datetime":
        if not _is_date_like(value, with_time=True):
            return f" 取值 {value} 必须是日期时间（YYYY-MM-DD HH:MM:SS）。"
    elif field_type == "enum":
        allowed_values = {str(item) for item in (field.get("enum_values") or [])}
        if allowed_values and str(value).strip() not in allowed_values:
            allowed_text = "、".join(sorted(allowed_values))
            return (
                f" 取值 {value} 不在枚举值集合内（ProjectPlan 允许：{allowed_text}；"
                "如需新增取值，请先修订项目计划实体定义）。"
            )
    return ""


def _is_number_like(value: Any) -> bool:
    """判断取值是否可视为数字（含数字字符串）。"""

    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value.strip())
            return True
        except ValueError:
            return False
    return False


def _is_date_like(value: Any, *, with_time: bool) -> bool:
    """判断取值是否为可解析的日期或日期时间字符串。"""

    if not isinstance(value, str):
        return False
    text = value.strip()
    try:
        if with_time:
            datetime.datetime.fromisoformat(text)
        else:
            datetime.date.fromisoformat(text)
        return True
    except ValueError:
        return False


def compile_entity_database_statements(
    detail: dict[str, Any],
    *,
    database: str | None = None,
) -> list[str]:
    """把实体设计的数据库操作编译为可执行的 MySQL 语句。"""

    database_design = detail.get("database_design") if isinstance(detail.get("database_design"), dict) else {}
    operations = _dict_items(database_design.get("database_operations"))
    if not operations:
        return []
    target_database = str(database or database_design.get("schema_context", {}).get("database") or "").strip()
    statements: list[str] = []
    for operation in operations:
        operation_kind = str(operation.get("operation") or "")
        if operation_kind == "create_table":
            table = operation.get("table") if isinstance(operation.get("table"), dict) else {}
            statement = _compile_create_table(table, target_database)
            if statement:
                statements.append(statement)
            continue
        table_name = str(operation.get("table") or "").strip()
        column_name = str(operation.get("column") or "").strip()
        to = operation.get("to") if isinstance(operation.get("to"), dict) else {}
        if not table_name or not column_name:
            continue
        qualified = _qualified_table(target_database, table_name)
        column_type = str(to.get("type") or "VARCHAR(255)")
        nullable = "NULL" if to.get("nullable") is not False else "NOT NULL"
        comment = str(to.get("comment") or "")
        comment_sql = f" COMMENT '{_escape_sql_string(comment)}'" if comment else ""
        if operation_kind == "add_column":
            statements.append(
                f"ALTER TABLE {qualified} ADD COLUMN `{column_name}` {column_type} {nullable}{comment_sql}"
            )
        elif operation_kind == "alter_column_type":
            statements.append(
                f"ALTER TABLE {qualified} MODIFY COLUMN `{column_name}` {column_type} {nullable}{comment_sql}"
            )
        elif operation_kind == "alter_column_nullable":
            statements.append(
                f"ALTER TABLE {qualified} MODIFY COLUMN `{column_name}` {column_type} {nullable}"
            )
        elif operation_kind == "alter_column_default":
            default = to.get("default")
            if default is None:
                statements.append(
                    f"ALTER TABLE {qualified} ALTER COLUMN `{column_name}` DROP DEFAULT"
                )
            else:
                statements.append(
                    f"ALTER TABLE {qualified} ALTER COLUMN `{column_name}` SET DEFAULT "
                    f"{_sql_literal(default)}"
                )
    return statements[:200]


def _compile_create_table(table: dict[str, Any], database: str) -> str:
    """编译 CREATE TABLE 语句，隐式主键 id 由实体设计统一管理。"""

    table_name = str(table.get("name") or "").strip()
    if not table_name:
        return ""
    columns = _dict_items(table.get("columns"))
    if not columns:
        return ""
    column_lines: list[str] = []
    for column in columns:
        column_name = str(column.get("name") or "").strip()
        if not column_name:
            continue
        column_type = str(column.get("type") or "VARCHAR(255)")
        nullable = "NULL" if column.get("nullable") is not False else "NOT NULL"
        auto_increment = " AUTO_INCREMENT" if column.get("auto_increment") else ""
        comment = str(column.get("comment") or "")
        comment_sql = f" COMMENT '{_escape_sql_string(comment)}'" if comment else ""
        column_lines.append(
            f"  `{column_name}` {column_type} {nullable}{auto_increment}{comment_sql}"
        )
    primary_key = _string_list(table.get("primary_key"))
    if primary_key:
        column_lines.append(
            "  PRIMARY KEY (" + ", ".join(f"`{name}`" for name in primary_key) + ")"
        )
    table_comment = str(table.get("comment") or "")
    comment_clause = (
        f" COMMENT='{_escape_sql_string(table_comment)}'" if table_comment else ""
    )
    return (
        f"CREATE TABLE IF NOT EXISTS {_qualified_table(database, table_name)} (\n"
        + ",\n".join(column_lines)
        + f"\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4{comment_clause}"
    )


def _qualified_table(database: str, table_name: str) -> str:
    """生成带库名（如有）的安全表引用。"""

    table = f"`{table_name.replace('`', '``')}`"
    return f"`{database.replace('`', '``')}`.{table}" if database else table


def _escape_sql_string(value: Any) -> str:
    """转义 SQL 字符串字面量中的单引号。"""

    return str(value or "").replace("\\", "\\\\").replace("'", "''")


def _sql_literal(value: Any) -> str:
    """按值类型生成 SQL 字面量。"""

    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return f"'{_escape_sql_string(value)}'"


def execute_entity_database_operations(
    detail: dict[str, Any],
    *,
    workspace_root: str | None = None,
) -> dict[str, Any]:
    """在实体设计确认后落地执行数据库表操作，并记录证据。"""

    database_design = detail.get("database_design") if isinstance(detail.get("database_design"), dict) else {}
    operations = _dict_items(database_design.get("database_operations"))
    operation_ids = [str(operation.get("id") or "") for operation in operations if operation.get("id")]
    schema_context = database_design.get("schema_context") if isinstance(database_design.get("schema_context"), dict) else {}
    if not operations:
        detail["database_execution"] = {
            "status": "skipped",
            "summary": "实体设计未包含数据库表操作，无需落地执行。",
            "operation_ids": [],
        }
        detail["table_operations_executed"] = False
        return detail
    statements = compile_entity_database_statements(detail)
    execution_context = create_database_execution_context(schema_context or {})
    result = execute_database_plan(
        plan={"statements": statements},
        execution_context=execution_context,
        workspace_root=workspace_root,
    )
    evidence = {
        **result,
        "operation_ids": operation_ids,
        "approved_by": "entity_design_confirmation",
        "statement_count": len(statements),
    }
    detail["database_execution"] = evidence
    detail["table_operations_executed"] = result.get("status") == "completed"
    return detail


def entity_design_summary(detail: dict[str, Any]) -> dict[str, Any]:
    """构造实体数据源绑定阶段摘要，供 EntitySourceBinding 投射给前端。"""

    stage = entity_design_stage(detail)
    detail_fields = _dict_items(detail.get("fields"))
    summary: dict[str, Any] = {
        "stage": stage,
        "entity_id": str(detail.get("entity_id") or ""),
        "entity_name": str(detail.get("entity_name") or detail.get("entity_id") or ""),
        "data_source_type": str(detail.get("data_source_type") or ""),
        "default_constraints": _default_constraints(detail_fields),
        "fields": [
            {
                "name": str(field.get("name") or ""),
                "label": str(field.get("label") or field.get("name") or ""),
                "type": str(field.get("type") or "text"),
                "required": bool(field.get("required")),
                **(
                    {"enum_values": list(field["enum_values"])}
                    if field.get("enum_values")
                    else {}
                ),
            }
            for field in detail_fields
            if field.get("name")
        ],
        "validation_errors": _entity_design_validation_errors(detail),
    }
    database_design = detail.get("database_design") if isinstance(detail.get("database_design"), dict) else {}
    if database_design:
        summary["database_design"] = {
            "binding_status": database_design.get("binding_status"),
            "matched_table": database_design.get("matched_table"),
            "table_count": len(_dict_items(database_design.get("available_tables"))),
            "binding_count": len(_dict_items(database_design.get("bindings"))),
            "difference_count": len(_dict_items(database_design.get("differences"))),
            "operation_count": len(_dict_items(database_design.get("database_operations"))),
            "table_generation_required": bool(
                isinstance(database_design.get("table_generation"), dict)
                and database_design.get("table_generation", {}).get("required")
            ),
            "table_generation_approved": bool(
                isinstance(database_design.get("table_generation"), dict)
                and database_design.get("table_generation", {}).get("approved")
            ),
        }
    external_api_design = detail.get("external_api_design") if isinstance(detail.get("external_api_design"), dict) else {}
    if external_api_design:
        api_info = external_api_design.get("api_info") if isinstance(external_api_design.get("api_info"), dict) else {}
        summary["external_api_design"] = {
            "path": api_info.get("path"),
            "method": api_info.get("method"),
            "mapping_count": len(_dict_items(external_api_design.get("field_mappings"))),
        }
    static_design = detail.get("static_design") if isinstance(detail.get("static_design"), dict) else {}
    if static_design:
        summary["static_design"] = {
            "seed_row_count": len(_dict_items(static_design.get("seed_rows"))),
            "field_value_count": len(static_design.get("field_values") or {}),
        }
    if detail.get("database_execution") is not None:
        execution = detail.get("database_execution") if isinstance(detail.get("database_execution"), dict) else {}
        summary["database_execution"] = {
            "status": execution.get("status"),
            "summary": execution.get("summary"),
            "operation_count": len(execution.get("operation_ids") or []),
        }
    return summary


def _entity_design_validation_errors(detail: dict[str, Any]) -> list[str]:
    """汇总实体设计各方案段落中的确定性校验错误，供界面展示。"""

    errors: list[str] = []
    database_design = detail.get("database_design") if isinstance(detail.get("database_design"), dict) else {}
    external_api_design = detail.get("external_api_design") if isinstance(detail.get("external_api_design"), dict) else {}
    static_design = detail.get("static_design") if isinstance(detail.get("static_design"), dict) else {}
    errors.extend(_string_list(database_design.get("validation_errors")))
    errors.extend(_string_list(external_api_design.get("validation_errors")))
    errors.extend(_string_list(static_design.get("validation_errors")))
    return list(dict.fromkeys(errors))


def entity_bound_design_gate(
    project_plan: dict[str, Any],
    contract_id: str,
) -> tuple[list[str], list[dict[str, str]]]:
    """校验接口契约绑定的实体是否全部完成实体设计并确认（前置门禁）。

    返回（错误文案列表, 缺失实体描述列表）；缺失实体携带 entity_id 与
    entity_name，供前端门禁卡展示并支持一键跳转实体设计。
    """

    errors: list[str] = []
    missing: list[dict[str, str]] = []
    contract = next(
        (
            item
            for item in project_plan.get("api_contracts") or []
            if isinstance(item, dict) and str(item.get("id") or "") == contract_id
        ),
        None,
    )
    if contract is None:
        return [f"项目计划中不存在 API 契约：{contract_id}"], []
    confirmed_ids = {
        str(detail.get("entity_id") or "")
        for detail in project_plan.get("entity_detail_plans") or []
        if isinstance(detail, dict)
        and str(detail.get("status") or "") == "confirmed"
        and str(detail.get("entity_id") or "")
    }
    entity_names = {
        str(entity.get("id") or ""): str(entity.get("name") or "")
        for entity in project_plan.get("entities") or []
        if isinstance(entity, dict) and str(entity.get("id") or "")
    }
    for entity_id in contract.get("entity_ids") or []:
        entity_id_text = str(entity_id or "").strip()
        if not entity_id_text:
            continue
        if entity_id_text not in confirmed_ids:
            errors.append(
                f"接口绑定的实体 {entity_id_text} 尚未完成实体设计并确认，"
                "须先完成该实体设计后才能进入接口/页面详细设计。"
            )
            missing.append(
                {
                    "entity_id": entity_id_text,
                    "entity_name": entity_names.get(entity_id_text) or entity_id_text,
                }
            )
    return errors, missing


def entity_bound_design_errors(
    project_plan: dict[str, Any],
    contract_id: str,
) -> list[str]:
    """校验接口契约绑定的实体是否全部完成实体设计并确认（前置门禁，仅返回错误文案）。"""

    errors, _missing = entity_bound_design_gate(project_plan, contract_id)
    return errors


def entity_field_name_valid(name: str) -> bool:
    """复检实体字段名是否符合 snake_case 契约。"""

    return bool(ENTITY_FIELD_NAME_RE.match(name))
