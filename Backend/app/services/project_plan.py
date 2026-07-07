from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _entity_name_from_source(data_source: dict[str, Any]) -> str:
    entities = data_source.get("entities") or []
    if entities:
        return str(entities[0])
    return "".join(part.title() for part in data_source["id"].split("_"))


def _route_base(data_source: dict[str, Any]) -> str:
    source_id = data_source["id"]
    if source_id.endswith("_source"):
        source_id = source_id[: -len("_source")]
    return source_id.replace("_", "-")


def _api_contracts(spec: dict[str, Any]) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for data_source in spec["data_sources"]:
        entity = _entity_name_from_source(data_source)
        route_base = _route_base(data_source)
        contracts.append(
            {
                "id": f"{data_source['id']}_api",
                "data_source_id": data_source["id"],
                "resource": entity,
                "base_path": f"/api/{route_base}",
                "endpoints": [
                    {
                        "method": "GET",
                        "path": f"/api/{route_base}",
                        "description": f"查询{data_source['name']}列表。",
                        "response": {"items": [entity], "total": "number"},
                    },
                    {
                        "method": "GET",
                        "path": f"/api/{route_base}/{{id}}",
                        "description": f"查询单条{data_source['name']}详情。",
                        "response": entity,
                    },
                ],
            }
        )
    return contracts


def _frontend_pages(spec: dict[str, Any]) -> list[dict[str, Any]]:
    data_source_ids = [source["id"] for source in spec["data_sources"]]
    pages = []
    for page in spec["pages"]:
        related_sources = [
            source_id
            for source_id in data_source_ids
            if page["module_id"] in source_id or page["module_id"] == "access_control"
        ]
        pages.append(
            {
                "id": page["id"],
                "name": page["name"],
                "path": page["path"],
                "module_id": page["module_id"],
                "description": page["description"],
                "data_dependencies": related_sources,
                "states": ["loading", "empty", "error", "ready"],
                "permissions": ["admin", "user"]
                if page["path"] != "/login"
                else ["anonymous"],
            }
        )
    return pages


def _planned_data_sources(spec: dict[str, Any]) -> list[dict[str, Any]]:
    planned_sources = []
    for source in spec["data_sources"]:
        entity = _entity_name_from_source(source)
        planned_sources.append(
            {
                "id": source["id"],
                "name": source["name"],
                "type": source["type"],
                "entities": source["entities"],
                "schema": {
                    "entity": entity,
                    "fields": [
                        {"name": "id", "type": "string", "required": True},
                        {"name": "name", "type": "string", "required": True},
                        {"name": "status", "type": "string", "required": False},
                        {"name": "createdAt", "type": "datetime", "required": False},
                    ],
                },
                "seed_strategy": "demo_records",
            }
        )
    return planned_sources


def _task_inputs(plan_pages: list[dict[str, Any]], plan_sources: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "frontend": [
            {
                "task_id": f"page:{page['id']}",
                "page_id": page["id"],
                "description": f"生成页面 {page['name']}（{page['path']}）。",
                "depends_on": [f"data_source:{source_id}" for source_id in page["data_dependencies"]],
            }
            for page in plan_pages
        ],
        "data_source": [
            {
                "task_id": f"data_source:{source['id']}",
                "data_source_id": source["id"],
                "description": f"生成数据源 {source['name']} 及对应 API。",
                "depends_on": [],
            }
            for source in plan_sources
        ],
    }


def _coordination_plan() -> dict[str, Any]:
    return {
        "detail_confirmation": {
            "owner": "main-agent",
            "strategy": "Confirm each frontend page and data source before code generation.",
            "outputs": ["page_execution_plans", "data_source_execution_plans"],
        },
        "build": {
            "owner": "main-agent",
            "strategy": "Dispatch approved page tasks to frontend agent and data-source tasks to data-source agent.",
            "outputs": ["task_results", "changed_files", "command_evidence"],
        },
        "testing": {
            "owner": "test-agent",
            "strategy": "Run contract, integration, and smoke checks, then report defects back to Main Agent.",
            "outputs": ["test_report", "defect_tasks"],
        },
    }


def create_project_plan(
    spec: dict[str, Any],
    agent_note: str = "live main-agent project planning",
    planning_source: str = "main_agent_live",
) -> dict[str, Any]:
    frontend_pages = _frontend_pages(spec)
    data_sources = _planned_data_sources(spec)

    return {
        "version": "0.1.0",
        "status": "draft",
        "generated_at": datetime.now(UTC).isoformat(),
        "requirement_spec_version": spec["version"],
        "app": {
            "name": spec["app_info"]["name"],
            "summary": spec["app_info"]["summary"],
        },
        "architecture": {
            "frontend": "Single Page Application with generated pages and API client.",
            "backend": "Local API service exposing generated resource endpoints.",
            "data": "Mock or database-backed data sources based on RequirementSpec.",
            "testing": "Unit, contract, integration, and smoke checks before acceptance.",
        },
        "api_contracts": _api_contracts(spec),
        "frontend_pages": frontend_pages,
        "data_sources": data_sources,
        "business_flows": spec["business_flows"],
        "acceptance_criteria": spec["acceptance_criteria"],
        "task_inputs": _task_inputs(frontend_pages, data_sources),
        "coordination_plan": _coordination_plan(),
        "risks": [
            "字段模型仍是初版，需要在单页面/单数据源细节确认阶段细化。",
            "权限规则当前按角色粗粒度规划，后续需要确认到页面和操作级别。",
        ],
        "agent_note": agent_note,
        "planning_source": planning_source,
        "approved": True,
    }
