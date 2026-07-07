from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any


def _find_by_id(items: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
    for item in items:
        if item["id"] == item_id:
            return item
    raise ValueError(f"Unknown item id: {item_id}")


def _related_api_contracts(
    project_plan: dict[str, Any],
    data_dependencies: list[str],
) -> list[dict[str, Any]]:
    dependency_set = set(data_dependencies)
    return [
        contract
        for contract in project_plan["api_contracts"]
        if contract["data_source_id"] in dependency_set
    ]


def create_page_detail_plan(
    project_plan: dict[str, Any],
    confirmed_page_spec: dict[str, Any],
    agent_note: str = "live main-agent page detail design",
) -> dict[str, Any]:
    page_id = confirmed_page_spec["page_id"]
    page = _find_by_id(project_plan["frontend_pages"], page_id)
    data_source_ids = confirmed_page_spec.get("data_source_ids", page["data_dependencies"])
    api_contracts = _related_api_contracts(project_plan, data_source_ids)
    layout = confirmed_page_spec.get("layout", {})

    return {
        "id": f"page_detail:{page['id']}",
        "type": "page",
        "page_id": page["id"],
        "page_name": page["name"],
        "path": page["path"],
        "status": "confirmed",
        "confirmed_at": datetime.now(UTC).isoformat(),
        "source_page_spec": confirmed_page_spec,
        "page_goal": confirmed_page_spec["page_goal"],
        "basic_layout": {
            "structure": layout.get(
                "structure",
                ["页面标题区", "主要内容区", "操作区", "状态反馈区"],
            ),
            "states": confirmed_page_spec.get("states", page["states"]),
            "responsive": layout.get(
                "responsive",
                "默认支持桌面端布局，后续可扩展移动端适配。",
            ),
        },
        "interactions": confirmed_page_spec["interactions"],
        "data_sources": [
            {
                "id": contract["data_source_id"],
                "api_contract_id": contract["id"],
                "base_path": contract["base_path"],
                "endpoints": contract["endpoints"],
            }
            for contract in api_contracts
        ],
        "permissions": confirmed_page_spec["permissions"],
        "acceptance_criteria": [
            f"用户可以访问 {page['path']} 并看到 {page['name']} 的主要内容。",
            "页面具备 loading、empty、error、ready 四类基础状态。",
            "页面只访问用户确认的 PageSpec 中声明的数据源和对应 API 契约。",
            "页面权限与用户确认的 PageSpec 保持一致。",
        ],
        "agent_note": agent_note,
        "approved": True,
    }


def attach_page_detail_plan(
    project_plan: dict[str, Any],
    detail_plan: dict[str, Any],
) -> dict[str, Any]:
    updated_plan = deepcopy(project_plan)
    existing_details = {
        item["page_id"]: item for item in updated_plan.get("page_detail_plans", [])
    }
    existing_details[detail_plan["page_id"]] = detail_plan
    updated_plan["page_detail_plans"] = list(existing_details.values())

    for page in updated_plan["frontend_pages"]:
        if page["id"] == detail_plan["page_id"]:
            page["detail_status"] = "confirmed"
            page["detail_plan_id"] = detail_plan["id"]

    updated_plan["detail_confirmation_summary"] = {
        "confirmed_pages": len(updated_plan["page_detail_plans"]),
        "total_pages": len(updated_plan["frontend_pages"]),
        "latest_page_id": detail_plan["page_id"],
    }
    return updated_plan
