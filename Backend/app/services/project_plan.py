from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _agent_section(agent_plan: dict[str, Any] | None, key: str) -> Any:
    if not isinstance(agent_plan, dict):
        return None
    return agent_plan.get(key)


def _merge_agent_items(
    default_items: list[dict[str, Any]],
    agent_plan: dict[str, Any] | None,
    key: str,
    *,
    authoritative: bool = False,
) -> list[dict[str, Any]]:
    agent_items = _agent_section(agent_plan, key)
    if not isinstance(agent_items, list):
        return default_items

    agent_items = [
        item for item in agent_items if isinstance(item, dict) and item.get("id")
    ]
    if authoritative:
        defaults_by_id = {
            str(item["id"]): item for item in default_items if item.get("id")
        }
        return [
            _merge_agent_item(defaults_by_id.get(str(item["id"]), {}), item)
            for item in agent_items
        ]

    by_id = {
        str(item["id"]): item
        for item in agent_items
        if isinstance(item, dict) and item.get("id")
    }
    return [
        _merge_agent_item(item, by_id.get(str(item["id"]), {}))
        for item in default_items
    ]


def _merge_agent_item(
    default_item: dict[str, Any],
    agent_item: dict[str, Any],
) -> dict[str, Any]:
    merged = {**default_item, **agent_item}
    for key, default_value in default_item.items():
        agent_value = agent_item.get(key)
        if isinstance(default_value, list) and not isinstance(agent_value, list):
            merged[key] = default_value
        elif isinstance(default_value, dict) and not isinstance(agent_value, dict):
            merged[key] = default_value
    return merged


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _normalize_data_sources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for item in items:
        normalized.append(
            {
                **item,
                "name": str(item.get("name") or item.get("id") or "数据源"),
                "type": str(item.get("type") or "mock"),
                "entities": _string_items(item.get("entities")),
                "schema": (
                    item.get("schema") if isinstance(item.get("schema"), dict) else {}
                ),
            }
        )
    return normalized


def _normalize_api_contracts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for item in items:
        normalized.append(
            {
                **item,
                "resource": str(item.get("resource") or item.get("id") or "Resource"),
                "base_path": str(item.get("base_path") or "/api/resource"),
                "endpoints": _dict_items(item.get("endpoints")),
            }
        )
    return normalized


def _normalize_frontend_pages(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for item in items:
        normalized.append(
            {
                **item,
                "name": str(item.get("name") or item.get("id") or "页面"),
                "path": str(item.get("path") or "/"),
                "module_id": str(item.get("module_id") or "core"),
                "description": str(
                    item.get("description") or item.get("name") or "业务页面"
                ),
                "data_dependencies": _string_items(item.get("data_dependencies")),
                "states": _string_items(item.get("states")),
                "permissions": _string_items(item.get("permissions")),
            }
        )
    return normalized


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
        page_id = str(page.get("id") or "page")
        page_name = str(page.get("name") or page_id)
        module_id = str(page.get("module_id") or "core")
        related_sources = [
            source_id
            for source_id in data_source_ids
            if module_id in source_id or module_id == "access_control"
        ]
        pages.append(
            {
                "id": page_id,
                "name": page_name,
                "path": str(page.get("path") or "/"),
                "module_id": module_id,
                "description": str(page.get("description") or page_name or "业务页面"),
                "data_dependencies": related_sources,
                "states": ["loading", "empty", "error", "ready"],
                "permissions": (
                    ["admin", "user"] if page.get("path") != "/login" else ["anonymous"]
                ),
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


def _task_inputs(
    plan_pages: list[dict[str, Any]], plan_sources: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "frontend": [
            {
                "task_id": f"page:{page['id']}",
                "page_id": page["id"],
                "description": f"生成页面 {page['name']}（{page['path']}）。",
                "depends_on": [
                    f"data_source:{source_id}"
                    for source_id in page["data_dependencies"]
                ],
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


def _requirements_overview(
    spec: dict[str, Any],
    agent_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    overview = {
        "summary": spec["app_info"]["summary"],
        "target": spec["app_info"].get(
            "target", "生成一个可在本地运行的前后端应用工程。"
        ),
        "roles": spec["user_roles"],
        "modules": spec["feature_modules"],
        "business_flows": spec["business_flows"],
        "acceptance_focus": spec["acceptance_criteria"],
    }
    agent_overview = _agent_section(agent_plan, "requirements_overview")
    if isinstance(agent_overview, dict):
        overview.update(agent_overview)
    for key, fallback in {
        "roles": spec["user_roles"],
        "modules": spec["feature_modules"],
        "business_flows": spec["business_flows"],
        "acceptance_focus": spec["acceptance_criteria"],
    }.items():
        if key == "acceptance_focus":
            if not _string_items(overview.get(key)):
                overview[key] = fallback
        elif not _dict_items(overview.get(key)):
            overview[key] = fallback
    return overview


def _project_acceptance_criteria(
    spec: dict[str, Any],
    agent_plan: dict[str, Any] | None,
) -> list[str]:
    agent_criteria = _agent_section(agent_plan, "project_acceptance_criteria")
    if isinstance(agent_criteria, list) and agent_criteria:
        criteria = [str(item) for item in agent_criteria if str(item).strip()]
        if criteria:
            return criteria
    return list(spec["acceptance_criteria"])


def _page_data_dependencies(
    plan_pages: list[dict[str, Any]],
    api_contracts: list[dict[str, Any]],
    agent_plan: dict[str, Any] | None,
    *,
    authoritative: bool = False,
) -> list[dict[str, Any]]:
    contract_by_source = {
        contract["data_source_id"]: contract["id"]
        for contract in api_contracts
        if contract.get("data_source_id")
    }
    dependencies = [
        {
            "page_id": page["id"],
            "page_name": page["name"],
            "path": page["path"],
            "data_source_ids": page["data_dependencies"],
            "api_contract_ids": [
                contract_by_source[source_id]
                for source_id in page["data_dependencies"]
                if source_id in contract_by_source
            ],
            "usage": "read",
        }
        for page in plan_pages
    ]
    agent_dependencies = _dict_items(
        _agent_section(agent_plan, "page_data_dependencies")
    )
    if not agent_dependencies:
        return dependencies

    if authoritative:
        return [
            {
                **item,
                "data_source_ids": _string_items(item.get("data_source_ids")),
                "api_contract_ids": _string_items(item.get("api_contract_ids")),
            }
            for item in agent_dependencies
            if item.get("page_id")
        ]

    by_page_id = {
        str(item["page_id"]): item for item in agent_dependencies if item.get("page_id")
    }
    return [
        {
            **item,
            **by_page_id.get(str(item["page_id"]), {}),
            "data_source_ids": _string_items(
                by_page_id.get(str(item["page_id"]), {}).get(
                    "data_source_ids",
                    item["data_source_ids"],
                )
            ),
            "api_contract_ids": _string_items(
                by_page_id.get(str(item["page_id"]), {}).get(
                    "api_contract_ids",
                    item["api_contract_ids"],
                )
            ),
        }
        for item in dependencies
    ]


def _permission_model(
    spec: dict[str, Any],
    plan_pages: list[dict[str, Any]],
    agent_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    role_ids = [role["id"] for role in spec["user_roles"]]
    model = {
        "roles": spec["user_roles"],
        "page_access": [
            {
                "page_id": page["id"],
                "path": page["path"],
                "allowed_roles": page["permissions"],
            }
            for page in plan_pages
        ],
        "operation_permissions": [
            {
                "role_id": role_id,
                "operations": (
                    ["read", "create", "update", "delete"]
                    if role_id == "admin"
                    else ["read"]
                ),
            }
            for role_id in role_ids
        ],
        "default_policy": "deny_unlisted",
    }
    agent_model = _agent_section(agent_plan, "permission_model")
    if isinstance(agent_model, dict):
        model.update(agent_model)
    return model


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


def _normalized_coordination_plan(agent_plan: dict[str, Any] | None) -> dict[str, Any]:
    defaults = _coordination_plan()
    agent_coordination = _agent_section(agent_plan, "coordination_plan")
    if not isinstance(agent_coordination, dict):
        return defaults

    normalized: dict[str, Any] = {}
    for stage in dict.fromkeys([*defaults, *agent_coordination]):
        default_item = defaults.get(stage, {})
        agent_item = agent_coordination.get(stage)
        if not isinstance(agent_item, dict):
            agent_item = {}
        outputs = agent_item.get("outputs", default_item.get("outputs", []))
        normalized[stage] = {
            **default_item,
            **agent_item,
            "owner": str(
                agent_item.get("owner") or default_item.get("owner") or "main-agent"
            ),
            "strategy": str(
                agent_item.get("strategy")
                or default_item.get("strategy")
                or f"Coordinate the {stage} stage."
            ),
            "outputs": _string_items(outputs),
        }
    return normalized


def create_project_plan(
    spec: dict[str, Any],
    agent_note: str = "live main-agent project planning",
    planning_source: str = "main_agent_live",
    agent_plan: dict[str, Any] | None = None,
    authoritative_agent_plan: bool = False,
) -> dict[str, Any]:
    data_sources = _normalize_data_sources(
        _merge_agent_items(
            _planned_data_sources(spec),
            agent_plan,
            "data_sources",
            authoritative=authoritative_agent_plan,
        )
    )
    api_contracts = _normalize_api_contracts(
        _merge_agent_items(
            _api_contracts(spec),
            agent_plan,
            "api_contracts",
            authoritative=authoritative_agent_plan,
        )
    )
    frontend_pages = _normalize_frontend_pages(
        _merge_agent_items(
            _frontend_pages(spec),
            agent_plan,
            "frontend_pages",
            authoritative=authoritative_agent_plan,
        )
    )
    task_inputs = _task_inputs(frontend_pages, data_sources)

    agent_architecture = _agent_section(agent_plan, "architecture")
    architecture = {
        "frontend": "Single Page Application with generated pages and API client.",
        "backend": "Local API service exposing generated resource endpoints.",
        "data": "Mock or database-backed data sources based on RequirementSpec.",
        "testing": "Unit, contract, integration, and smoke checks before acceptance.",
    }
    if isinstance(agent_architecture, dict):
        architecture.update(agent_architecture)

    return {
        "version": "0.1.0",
        "status": "draft",
        "generated_at": datetime.now(UTC).isoformat(),
        "requirement_spec_version": spec["version"],
        "app": {
            "name": spec["app_info"]["name"],
            "summary": spec["app_info"]["summary"],
        },
        "requirements_overview": _requirements_overview(spec, agent_plan),
        "project_acceptance_criteria": _project_acceptance_criteria(
            spec,
            agent_plan,
        ),
        "architecture": architecture,
        "api_contracts": api_contracts,
        "frontend_pages": frontend_pages,
        "data_sources": data_sources,
        "page_data_dependencies": _page_data_dependencies(
            frontend_pages,
            api_contracts,
            agent_plan,
            authoritative=authoritative_agent_plan,
        ),
        "permission_model": _permission_model(spec, frontend_pages, agent_plan),
        "business_flows": (
            _dict_items(_agent_section(agent_plan, "business_flows"))
            if authoritative_agent_plan
            and _dict_items(_agent_section(agent_plan, "business_flows"))
            else spec["business_flows"]
        ),
        "acceptance_criteria": (
            _string_items(_agent_section(agent_plan, "acceptance_criteria"))
            if authoritative_agent_plan
            and _string_items(_agent_section(agent_plan, "acceptance_criteria"))
            else spec["acceptance_criteria"]
        ),
        "task_inputs": task_inputs,
        "coordination_plan": _normalized_coordination_plan(agent_plan),
        "risks": (
            _string_items(_agent_section(agent_plan, "risks"))
            if authoritative_agent_plan
            and isinstance(_agent_section(agent_plan, "risks"), list)
            else [
                "字段模型仍是初版，需要在单页面/单数据源细节确认阶段细化。",
                "权限规则当前按角色粗粒度规划，后续需要确认到页面和操作级别。",
            ]
        ),
        "agent_note": agent_note,
        "planning_source": planning_source,
        "agent_plan_used": isinstance(agent_plan, dict),
        "approved": True,
    }
