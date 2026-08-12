from __future__ import annotations

from typing import Any

from app.services.entity_detail_plan import (
    attach_entity_detail_plan,
    create_entity_detail_plan,
)


def confirm_entity_designs(
    project_plan: dict[str, Any],
    *,
    source_type: str = "database",
    entity_ids: list[str] | None = None,
) -> dict[str, Any]:
    """把计划内实体标记为已确认实体设计，供接口/页面/构建链路测试使用。"""

    selected = set(entity_ids or [])
    updated = project_plan
    for entity in project_plan.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("id") or "")
        if selected and entity_id not in selected:
            continue
        detail = create_entity_detail_plan(
            project_plan,
            entity,
            default_datasource_type=source_type,
        )
        detail["status"] = "confirmed"
        detail["approved"] = True
        updated = attach_entity_detail_plan(updated, detail)
    return updated
