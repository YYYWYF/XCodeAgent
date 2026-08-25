"""EntitySourceBinding 用户交互投影与显式确认。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.entity_detail_plan import refresh_entity_detail_table_design
from app.services.entity_design import (
    entity_design_selection_summary,
    entity_design_stage,
    entity_design_summary,
)


ENTITY_BINDING_EDITABLE_FIELDS = {
    "data_source_type",
    "database_design",
    "external_api_design",
    "static_design",
    "business_rules",
    "relationships",
    "acceptance_criteria",
    "risks",
}


def entity_source_binding_payload(
    project_plan: dict[str, Any],
    *,
    selected_entity_id: str,
    detail_target_type: str = "entity",
) -> dict[str, Any]:
    """只投射当前实体的数据源绑定交互。"""

    plan = deepcopy(project_plan)
    entities = _entity_binding_items(plan, selected_entity_id=selected_entity_id)
    missing = not entities
    return {
        "mode": "entity_source_binding",
        "status": "requires_user_input",
        "question_schema": "xcodeagent.entity_source_binding.v1",
        "questions": [],
        "message": (
            f"实体 `{selected_entity_id}` 尚未开始数据源绑定，请先选择数据库、外部 API 或静态数据。"
            if missing
            else f"请确认实体 `{selected_entity_id}` 的数据源与物理字段绑定。"
        ),
        "review": {
            "pages": [],
            "endpoints": [],
            "entities": entities,
            "summary": {
                "page_count": 0,
                "endpoint_count": 0,
                "entity_count": len(entities),
                "api_contract_count": 0,
                "missingSelectedPagePlan": False,
                "missingSelectedEndpointPlan": False,
                "missingSelectedEntityPlan": missing,
                "selectedPageId": None,
                "selectedApiContractId": None,
                "selectedEndpointId": None,
                "selectedEntityId": selected_entity_id,
                "entityDesign": _entity_binding_summary(
                    plan,
                    selected_entity_id,
                    entities,
                ),
                "detailTargetType": detail_target_type,
            },
        },
    }


def apply_entity_source_binding_submission(
    project_plan: dict[str, Any],
    submission: dict[str, Any],
    *,
    selected_entity_id: str,
) -> dict[str, Any]:
    """确认当前实体绑定，且只允许修改 EntitySourceBinding 自有字段。"""

    if submission.get("review_status") != "confirmed":
        raise ValueError("EntitySourceBinding 必须显式确认。")
    updated = deepcopy(project_plan)
    details = updated.get("entity_detail_plans", [])
    for patch in submission.get("target_changes", []):
        if not isinstance(patch, dict):
            continue
        if str(patch.get("target_type") or "") != "entity":
            raise ValueError("EntitySourceBinding 只能修改实体绑定字段。")
        target_id = str(patch.get("target_id") or "")
        changes = patch.get("changes")
        if target_id != selected_entity_id or not isinstance(changes, dict):
            raise ValueError("EntitySourceBinding 提交目标与当前实体不一致。")
        unknown = set(changes) - ENTITY_BINDING_EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"EntitySourceBinding 包含不可编辑字段：{sorted(unknown)}")
        target = _entity_detail(details, selected_entity_id)
        for key, value in changes.items():
            target[key] = deepcopy(value)

    detail = _entity_detail(details, selected_entity_id)
    refresh_entity_detail_table_design(detail)
    detail["status"] = "confirmed"
    detail["approved"] = True
    updated["entities"] = [
        {**entity, "detail_status": "confirmed"}
        if isinstance(entity, dict)
        and str(entity.get("id") or "") == selected_entity_id
        else entity
        for entity in updated.get("entities", [])
    ]
    updated["confirmation_status"] = "confirmed"
    updated["entity_source_binding"] = {
        "status": "confirmed",
        "entity_id": selected_entity_id,
        "changed_target_count": len(submission.get("target_changes", [])),
        "overall_note": str(submission.get("overall_note") or "").strip(),
    }
    return updated


def _entity_detail(details: Any, entity_id: str) -> dict[str, Any]:
    """从绑定列表中读取当前实体产物，缺失时返回可定位错误。"""

    target = next(
        (
            detail
            for detail in details
            if isinstance(detail, dict)
            and str(detail.get("entity_id") or "") == entity_id
        ),
        None,
    ) if isinstance(details, list) else None
    if target is None:
        raise ValueError(f"实体 {entity_id} 尚未生成数据源绑定。")
    return target


def _entity_binding_items(
    project_plan: dict[str, Any],
    *,
    selected_entity_id: str,
) -> list[dict[str, Any]]:
    """构造当前实体绑定的前端审核对象。"""

    items: list[dict[str, Any]] = []
    for detail in project_plan.get("entity_detail_plans", []):
        if not isinstance(detail, dict) or str(detail.get("entity_id") or "") != selected_entity_id:
            continue
        items.append(
            {
                "target_type": "entity",
                "target_id": selected_entity_id,
                "name": detail.get("entity_name") or selected_entity_id,
                "entity_id": selected_entity_id,
                "description": detail.get("description"),
                "module_id": detail.get("module_id"),
                "data_source_id": detail.get("data_source_id"),
                "data_source_type": detail.get("data_source_type"),
                "design_stage": entity_design_stage(detail),
                "fields": _list_value(detail.get("fields")),
                "table_design": _dict_value(detail.get("table_design")),
                "database_design": _dict_value(detail.get("database_design")),
                "external_api_design": _dict_value(detail.get("external_api_design")),
                "static_design": _dict_value(detail.get("static_design")),
                "database_execution": _dict_value(detail.get("database_execution")),
                "table_operations_executed": bool(detail.get("table_operations_executed")),
                "business_rules": _list_value(detail.get("business_rules")),
                "relationships": _list_value(detail.get("relationships")),
                "acceptance_criteria": _list_value(detail.get("acceptance_criteria")),
                "risks": _list_value(detail.get("risks")),
            }
        )
    return items


def _entity_binding_summary(
    project_plan: dict[str, Any],
    selected_entity_id: str,
    entities: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """构造数据源选择或当前绑定方案摘要。"""

    if entities:
        item = entities[0]
        return entity_design_summary(
            {
                "entity_id": item.get("entity_id"),
                "entity_name": item.get("name"),
                "fields": _list_value(item.get("fields")),
                "data_source_type": item.get("data_source_type"),
                "status": "pending_user_confirmation",
                "design_stage": item.get("design_stage"),
                "database_design": item.get("database_design"),
                "external_api_design": item.get("external_api_design"),
                "static_design": item.get("static_design"),
                "database_execution": item.get("database_execution"),
            }
        )
    entity = next(
        (
            item
            for item in project_plan.get("entities", [])
            if isinstance(item, dict) and str(item.get("id") or "") == selected_entity_id
        ),
        None,
    )
    return (
        entity_design_selection_summary(entity)
        if isinstance(entity, dict)
        else {"stage": "data_source_selection", "entity_id": selected_entity_id}
    )


def _dict_value(value: Any) -> dict[str, Any]:
    """只接受对象字段。"""

    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    """只接受数组字段。"""

    return value if isinstance(value, list) else []
