from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.entity_definitions import (
    MYSQL_TYPE_BY_FIELD_TYPE,
    data_source_type_label,
    entity_mysql_target_table,
    normalize_data_source_type,
    normalize_entity,
    plan_data_sources,
)


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """只保留列表中的字典项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _entity_data_source_id(project_plan: dict[str, Any], entity: dict[str, Any]) -> str:
    """从已确认实体设计的数据源列表反查实体所属数据源 id；未设计时返回空。"""

    entity_id = str(entity.get("id") or "")
    for source in plan_data_sources(project_plan):
        source_id = str(source.get("id") or "")
        for item in _dict_items(source.get("entities")):
            if str(item.get("id") or "") == entity_id:
                return source_id
    return ""


def _field_column_type(field_type: str) -> str:
    """把实体语义字段类型转换为 MySQL 列类型。"""

    return MYSQL_TYPE_BY_FIELD_TYPE.get(field_type, "VARCHAR(255)")


def _derived_business_rules(
    project_plan: dict[str, Any],
    entity: dict[str, Any],
    fields: list[dict[str, Any]],
    data_source_type: str,
) -> list[dict[str, str]]:
    """从实体字段确定性推导业务规则，不发明计划外约束。"""

    del project_plan
    rules: list[dict[str, str]] = [
        {
            "rule_type": "primary_key",
            "name": "主键自增",
            "description": "目标表使用 id 作为自增主键，由数据库生成。",
        }
    ]
    if data_source_type != "database":
        return rules
    for field in fields:
        field_name = str(field.get("name") or "")
        field_label = str(field.get("label") or field_name)
        if field.get("required"):
            rules.append(
                {
                    "rule_type": "required",
                    "name": f"必填约束：{field_label}",
                    "description": f"字段 `{field_name}` 不允许为空。",
                }
            )
        if field.get("type") == "enum" and field.get("enum_values"):
            rules.append(
                {
                    "rule_type": "enum",
                    "name": f"枚举约束：{field_label}",
                    "description": (
                        f"字段 `{field_name}` 取值限定为："
                        f"{'、'.join(str(item) for item in field['enum_values'])}。"
                    ),
                }
            )
        if any(
            marker in field_label.lower() or marker in field_name.lower()
            for marker in ("code", "编码", "编号", "单号")
        ):
            rules.append(
                {
                    "rule_type": "unique",
                    "name": f"唯一约束：{field_label}",
                    "description": f"字段 `{field_name}` 建立唯一索引，避免业务编码重复。",
                }
            )
    return rules


def _derived_acceptance_criteria(
    entity: dict[str, Any],
    fields: list[dict[str, Any]],
    data_source_type: str,
) -> list[str]:
    """确定性生成实体详细设计的验收标准。"""

    entity_name = str(entity.get("name") or entity.get("id") or "实体")
    criteria = [
        f"{entity_name} 的字段清单与已确认 ProjectPlan 实体定义完全一致，不新增、删除或改名。",
    ]
    if data_source_type == "database":
        criteria.append(
            f"{entity_name} 的目标表结构与字段设计一致，主键、必填列和唯一编码列均按设计落库。"
        )
    else:
        criteria.append(
            f"{entity_name} 在 {data_source_type_label(data_source_type)} 数据源中可完整承载全部字段。"
        )
    if any(field.get("type") == "enum" and field.get("enum_values") for field in fields):
        criteria.append("所有枚举字段的取值均限定在声明的枚举值集合内。")
    criteria.append(
        "接口详细设计只能引用已确认实体字段；如接口需要新字段，必须先回到项目计划修订并重新确认实体。"
    )
    return criteria


def _derived_risks(entity: dict[str, Any]) -> list[str]:
    """确定性生成实体详细设计的风险与待确认事项。"""

    entity_name = str(entity.get("name") or entity.get("id") or "实体")
    return [
        f"{entity_name} 的实体间关系（一对多/多对多）未在项目计划中声明，本轮按无关系处理；"
        "后续需要关系时回到项目计划补充并重新确认。",
        f"{entity_name} 的索引策略仅覆盖编码唯一性；其余查询索引在接口详细设计阶段按实际查询补充。",
    ]


def create_entity_detail_plan(
    project_plan: dict[str, Any],
    entity: Any,
    user_request: str = "",
    default_datasource_type: str | None = None,
) -> dict[str, Any]:
    """从已确认实体定义确定性组装 EntityDetail；数据源在实体设计中选定并确认。"""

    normalized = normalize_entity(entity, 0, with_types=True)
    entity_id = str(normalized.get("id") or "")
    if not entity_id:
        raise ValueError("实体详细设计必须提供有效的实体 id。")
    data_source_type = (
        default_datasource_type
        if default_datasource_type in {"database", "external_api", "static"}
        else normalize_data_source_type("")
    )
    data_source_id = _entity_data_source_id(project_plan, normalized) or data_source_type
    fields: list[dict[str, Any]] = []
    for field in normalized.get("fields", []):
        field_design = {
            "name": field["name"],
            "label": field.get("label") or field["name"],
            "type": field.get("type") or "text",
            "required": bool(field.get("required")),
            "description": field.get("description") or "",
            "column_type": _field_column_type(str(field.get("type") or "text")),
        }
        if field.get("enum_values"):
            field_design["enum_values"] = list(field["enum_values"])
        fields.append(field_design)

    feedback = str(user_request or "").strip()
    acceptance_criteria = _derived_acceptance_criteria(
        normalized,
        fields,
        data_source_type,
    )
    risks = _derived_risks(normalized)
    if feedback:
        risks.append(f"用户反馈：{feedback}；如与本设计冲突，请回到项目计划修订实体后重新确认。")

    detail: dict[str, Any] = {
        "id": f"entity_detail:{entity_id}",
        "entity_id": entity_id,
        "entity_name": str(normalized.get("name") or entity_id),
        "description": str(normalized.get("description") or ""),
        "module_id": str(normalized.get("module_id") or ""),
        "data_source_id": data_source_id,
        "data_source_type": data_source_type,
        "fields": fields,
        "table_design": (
            entity_mysql_target_table(normalized)
            if data_source_type == "database"
            else None
        ),
        "business_rules": _derived_business_rules(
            project_plan,
            normalized,
            fields,
            data_source_type,
        ),
        "relationships": [],
        "acceptance_criteria": acceptance_criteria,
        "risks": risks,
        "status": "pending_user_confirmation",
        "approved": False,
        "design_source": "deterministic_entity_design",
    }
    return detail


def attach_entity_detail_plan(
    project_plan: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any]:
    """把 EntityDetail 挂回 ProjectPlan，按实体 id 去重并保留其他实体详情。"""

    entity_id = str(detail.get("entity_id") or "")
    details = [
        item
        for item in _dict_items(project_plan.get("entity_detail_plans"))
        if str(item.get("entity_id") or "") != entity_id
    ]
    details.append(deepcopy(detail))
    return {**project_plan, "entity_detail_plans": details}


def refresh_entity_detail_table_design(detail: dict[str, Any]) -> dict[str, Any]:
    """按实体设计当前数据源类型重建目标表结构，不覆盖用户编辑的其他字段。"""

    entity_id = str(detail.get("entity_id") or "")
    data_source_type = str(detail.get("data_source_type") or "")
    fields = [
        {
            "label": field.get("label") or field.get("name") or "",
            "name": field.get("name") or "",
            "type": field.get("type") or "text",
            "required": bool(field.get("required")),
            "description": field.get("description") or "",
        }
        for field in _dict_items(detail.get("fields"))
        if field.get("name")
    ]
    detail["table_design"] = (
        entity_mysql_target_table(
            {
                "id": entity_id,
                "name": detail.get("entity_name") or entity_id,
                "description": detail.get("description") or "",
                "fields": fields,
            }
        )
        if data_source_type == "database"
        else None
    )
    return detail
