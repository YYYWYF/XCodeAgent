from __future__ import annotations

from typing import Any

from app.services.entity_detail_plan import (
    attach_entity_detail_plan,
    create_entity_detail_plan,
)
from app.services.entity_design import entity_related_endpoints


def _current_external_api_design(
    project_plan: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any]:
    """为测试实体构造只包含当前多操作契约的有效公开 API 绑定。"""

    entity_id = str(detail.get("entity_id") or "entity")
    required_fields = [
        str(field.get("name") or "")
        for field in detail.get("fields") or []
        if isinstance(field, dict)
        and bool(field.get("required"))
        and str(field.get("name") or "")
    ]
    endpoint_refs = [
        {
            "api_contract_id": endpoint["api_contract_id"],
            "endpoint_id": endpoint["endpoint_id"],
        }
        for endpoint in entity_related_endpoints(project_plan, entity_id)
    ]
    return {
        "connection": {
            "base_url": "https://api.example.com",
            "base_url_config_key": f"integrations.{entity_id.lower()}.base-url",
            "timeout_ms": 10000,
            "headers": [],
        },
        "operations": [
            {
                "operation_id": f"{entity_id.lower()}-default",
                "name": f"{entity_id} 默认上游操作",
                "endpoint_refs": endpoint_refs,
                "api_info": {
                    "method": "GET",
                    "path": f"/{entity_id.lower()}",
                    "parameters": [],
                    "headers": [],
                    "request_body": None,
                    "response_body": {
                        field_name: f"示例-{field_name}"
                        for field_name in required_fields
                    },
                },
                "response_handling": {
                    "entity_payload": True,
                    "cardinality": "object",
                    "payload_path": "",
                    "success_status_codes": [200],
                },
                "field_mappings": [
                    {
                        "entity_field": field_name,
                        "source_field": field_name,
                        "rule": "same_name",
                    }
                    for field_name in required_fields
                ],
            }
        ],
    }


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
        if source_type == "external_api":
            detail["external_api_design"] = _current_external_api_design(
                project_plan,
                detail,
            )
        detail["status"] = "confirmed"
        detail["approved"] = True
        updated = attach_entity_detail_plan(updated, detail)
    return updated
