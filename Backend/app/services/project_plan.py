from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any

from app.services.api_contract_repair import repair_cross_contract_schema_refs
from app.services.api_contracts import (
    normalize_api_contracts,
    schema_refs_for_data_source,
)
from app.services.frontend_page_tree import (
    apply_frontend_page_route_hierarchy,
    _module_display_name,
    flatten_frontend_pages,
    group_pages_into_menu_tree,
    rebuild_frontend_page_tree,
)
from app.services.page_dependencies import normalize_page_dependencies


BACKEND_TECH_STACK = {
    "language": "Java8",
    "framework": "Springboot",
    "database": "MySQL8",
    "cache": "Redis",
}


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
    """按业务主键合并模型补充项；页面统一使用 pageId。"""

    agent_items = _agent_section(agent_plan, key)
    if key == "frontend_pages" and agent_items is not None:
        agent_items = flatten_frontend_pages(agent_items)
    if not isinstance(agent_items, list):
        return default_items

    identity_key = "pageId" if key == "frontend_pages" else "id"
    agent_items = [
        _normalize_agent_item_aliases(item, key)
        for item in agent_items
        if isinstance(item, dict)
    ]
    agent_items = [item for item in agent_items if item.get(identity_key)]
    if authoritative:
        # 模型显式返回空契约时保留由数据源确定性生成的契约，避免业务计划静默退化为空数组。
        if key == "api_contracts" and not agent_items and default_items:
            return default_items
        defaults_by_id = {
            str(item[identity_key]): item
            for item in default_items
            if item.get(identity_key)
        }
        defaults_by_source = {
            str(item.get("data_source_id")): item
            for item in default_items
            if item.get("data_source_id")
        }
        return [
            _merge_agent_item(
                defaults_by_id.get(str(item[identity_key]))
                or defaults_by_source.get(str(item.get("data_source_id") or ""))
                or (
                    default_items[0]
                    if key == "api_contracts"
                    and len(default_items) == 1
                    and len(agent_items) == 1
                    else {}
                ),
                item,
            )
            for item in agent_items
        ]

    by_id = {
        str(item[identity_key]): item
        for item in agent_items
        if isinstance(item, dict) and item.get(identity_key)
    }
    return [
        _merge_agent_item(item, by_id.get(str(item[identity_key]), {}))
        for item in default_items
    ]


def _normalize_agent_item_aliases(item: dict[str, Any], key: str) -> dict[str, Any]:
    """把模型常见字段别名转换为 ProjectPlan 的唯一内部命名。"""

    normalized = dict(item)
    if key != "api_contracts":
        return normalized
    if not normalized.get("id"):
        contract_id = normalized.get("contract_id") or normalized.get("contractId")
        if contract_id:
            normalized["id"] = contract_id
    if not normalized.get("data_source_id"):
        data_source_id = normalized.get("dataSourceId") or normalized.get("source_id")
        if data_source_id:
            normalized["data_source_id"] = data_source_id
    normalized.pop("contract_id", None)
    normalized.pop("contractId", None)
    normalized.pop("dataSourceId", None)
    normalized.pop("source_id", None)
    return normalized


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
    return [
        str(item)
        for item in value
        if str(item).strip() and str(item).strip() not in {"无", "none", "None"}
    ]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return re.sub(r"[\s_\-:/\\，,。；;（）()\[\]【】'\"`]+", "", _text(value).lower())


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _path_from_pageId(pageId: str) -> str:
    """根据 pageId 生成稳定路由，避免缺省页面统一落到根路径。"""

    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(pageId or "page")).strip("-_")
    route = normalized.replace("_", "-").lower() or "page"
    if route.endswith("-page") and route != "dashboard-page":
        route = route[: -len("-page")] or route
    return "/" if route in {"dashboard", "dashboard-page", "home", "index"} else f"/{route}"


def _normalize_data_sources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for item in items:
        source = {key: value for key, value in item.items() if key != "schema"}
        normalized.append(
            {
                **source,
                "name": str(item.get("name") or item.get("id") or "数据源"),
                "type": str(item.get("type") or "mock"),
                "entities": _string_items(item.get("entities")),
                "schema_refs": _string_items(item.get("schema_refs")),
            }
        )
    return normalized


def _normalize_api_contracts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return normalize_api_contracts(items)


def normalize_project_plan(project_plan: dict[str, Any]) -> dict[str, Any]:
    """规范化 ProjectPlan 的内部结构，并保持 frontend_pages 的菜单树兼容。"""

    normalized = dict(project_plan)
    route_root_path = _route_root_path_from_plan(normalized)
    if "api_contracts" in normalized:
        normalized["api_contracts"] = _normalize_api_contracts(
            _dict_items(normalized.get("api_contracts"))
        )
    if "frontend_pages" in normalized:
        flat_pages = _normalize_frontend_pages(
            flatten_frontend_pages(normalized.get("frontend_pages"))
        )
        if "api_contracts" in normalized:
            flat_pages = normalize_page_dependencies(
                flat_pages,
                _dict_items(normalized.get("api_contracts")),
            )
        normalized["frontend_pages"] = rebuild_frontend_page_tree(
            normalized.get("frontend_pages"),
            flat_pages,
            root_route_prefix=route_root_path,
        )
    return normalized


def _route_root_path_from_plan(project_plan: dict[str, Any]) -> str:
    """优先从正式计划 app 信息中读取页面根路由。"""

    app = project_plan.get("app") if isinstance(project_plan.get("app"), dict) else {}
    return str(app.get("route_root_path") or "").strip()


def _normalize_frontend_pages(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规范化页面叶子字段，并为后续菜单树回挂提供稳定页面对象。"""

    normalized = []
    used_paths: set[str] = set()
    for item in items:
        pageId = str(item.get("pageId") or "page")
        path = _unique_page_path(
            str(item.get("path") or _path_from_pageId(pageId)),
            pageId,
            used_paths,
        )
        normalized.append(
            {
                **item,
                "name": str(item.get("name") or pageId or "页面"),
                "path": path,
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


def _unique_page_path(path: str, pageId: str, used_paths: set[str]) -> str:
    """把重复或空路由确定性改成由 pageId 派生的唯一业务路由。"""

    normalized = path.strip() or _path_from_pageId(pageId)
    if normalized != "/" and not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if normalized not in used_paths:
        used_paths.add(normalized)
        return normalized

    candidate = _path_from_pageId(pageId)
    if candidate == "/" or candidate in used_paths:
        base = candidate if candidate != "/" else f"/{_route_slug(pageId)}"
        suffix = 2
        candidate = base
        while candidate in used_paths:
            candidate = f"{base}-{suffix}"
            suffix += 1
    used_paths.add(candidate)
    return candidate


def _route_slug(value: str) -> str:
    """把页面标识转换为路由片段。"""

    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "page")).strip("-_")
    return normalized.replace("_", "-").lower() or "page"


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


def _api_contracts(data_sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for data_source in data_sources:
        entity = _entity_name_from_source(data_source)
        route_base = _route_base(data_source)
        entity_schema = _entity_schema()
        create_schema = _write_schema(entity_schema, partial=False)
        update_schema = _write_schema(entity_schema, partial=True)
        contracts.append(
            {
                "id": f"{data_source['id']}_api",
                "data_source_id": data_source["id"],
                "resource": entity,
                "base_path": f"/api/{route_base}",
                "authentication": {"required": True, "roles": ["admin", "user"]},
                "schemas": {
                    entity: entity_schema,
                    f"{entity}CreateInput": create_schema,
                    f"{entity}UpdateInput": update_schema,
                    f"{entity}ListOutput": {
                        "type": "object",
                        "properties": {
                            "items": {"type": "array", "items": {"$ref": entity}},
                            "total": {"type": "integer"},
                            "page": {"type": "integer"},
                            "page_size": {"type": "integer"},
                        },
                        "required": ["items", "total", "page", "page_size"],
                    },
                },
                "endpoints": [
                    {
                        "id": f"{data_source['id']}_api.list",
                        "method": "GET",
                        "path": f"/api/{route_base}",
                        "summary": f"查询{data_source['name']}列表。",
                        "parameters": [
                            {
                                "name": "page",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "integer", "default": 1},
                            },
                            {
                                "name": "page_size",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "integer", "default": 20},
                            },
                        ],
                        "response_schema_ref": f"{entity}ListOutput",
                        "error_codes": ["UNAUTHORIZED"],
                    },
                    {
                        "id": f"{data_source['id']}_api.detail",
                        "method": "GET",
                        "path": f"/api/{route_base}/{{id}}",
                        "summary": f"查询单条{data_source['name']}详情。",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                        ],
                        "response_schema_ref": entity,
                        "error_codes": ["NOT_FOUND"],
                    },
                    {
                        "id": f"{data_source['id']}_api.create",
                        "method": "POST",
                        "path": f"/api/{route_base}",
                        "summary": f"创建{data_source['name']}。",
                        "request_schema_ref": f"{entity}CreateInput",
                        "response_schema_ref": entity,
                        "error_codes": ["VALIDATION_ERROR"],
                    },
                    {
                        "id": f"{data_source['id']}_api.update",
                        "method": "PATCH",
                        "path": f"/api/{route_base}/{{id}}",
                        "summary": f"更新{data_source['name']}。",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                        ],
                        "request_schema_ref": f"{entity}UpdateInput",
                        "response_schema_ref": entity,
                        "error_codes": ["VALIDATION_ERROR", "NOT_FOUND"],
                    },
                    {
                        "id": f"{data_source['id']}_api.delete",
                        "method": "DELETE",
                        "path": f"/api/{route_base}/{{id}}",
                        "summary": f"删除{data_source['name']}。",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                        ],
                        "error_codes": ["NOT_FOUND"],
                    },
                ],
            }
        )
    return contracts


def _entity_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "status": {"type": "string"},
            "created_at": {"type": "string", "format": "date-time"},
        },
        "required": ["id", "name"],
    }


def _write_schema(entity_schema: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    properties = {
        key: value
        for key, value in entity_schema["properties"].items()
        if key not in {"id", "created_at"}
    }
    required = (
        []
        if partial
        else [key for key in entity_schema["required"] if key in properties]
    )
    return {"type": "object", "properties": properties, "required": required}


def _frontend_pages(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """根据 RequirementSpec 构造平铺页面叶子，供后续生成菜单树。"""

    data_source_ids = [source["id"] for source in spec["data_sources"]]
    pages = []
    used_paths: set[str] = set()
    for page in spec["pages"]:
        pageId = str(page.get("pageId") or "page")
        page_name = str(page.get("name") or pageId)
        module_id = str(page.get("module_id") or "core")
        path = _unique_page_path(
            str(page.get("path") or _path_from_pageId(pageId)),
            pageId,
            used_paths,
        )
        related_sources = [
            source_id
            for source_id in data_source_ids
            if module_id in source_id or module_id == "access_control"
        ]
        if not related_sources:
            # RequirementSpec 的模块 id 与数据源 id 不一定同名；首次规划仍需给业务页面绑定可解析 API。
            related_sources = [
                source_id
                for source_id in data_source_ids
                if source_id not in {"user_source", "auth_source"}
            ][:1] or data_source_ids[:1]
        pages.append(
            {
                "pageId": pageId,
                "name": page_name,
                "path": path,
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


def _module_name_map(spec: dict[str, Any]) -> dict[str, str]:
    """从需求模块中提取模块名，供菜单目录缺省命名使用。"""

    result: dict[str, str] = {}
    for module in _dict_items(spec.get("feature_modules")):
        module_id = str(module.get("id") or "").strip()
        if not module_id:
            continue
        result[module_id] = (
            str(module.get("name") or module.get("title") or "").strip()
            or _module_display_name(module_id)
        )
    return result


def _planned_data_sources(spec: dict[str, Any]) -> list[dict[str, Any]]:
    planned_sources = []
    for source in spec["data_sources"]:
        planned_sources.append(
            {
                "id": source["id"],
                "name": source["name"],
                "type": source["type"],
                "entities": source["entities"],
                "schema_refs": [],
                "seed_strategy": "demo_records",
            }
        )
    return planned_sources


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


def apply_project_plan_feedback(
    plan: dict[str, Any],
    user_feedback: str,
) -> dict[str, Any]:
    """Deterministically merge common ProjectPlan confirmation feedback.

    The planning model still handles broad revisions. This deterministic pass only
    applies stable project-level edits that do not create page/API bindings.
    """

    if not _text(user_feedback):
        return plan

    updated = {
        **plan,
        "frontend_pages": normalize_project_plan(plan).get("frontend_pages", []),
        "data_sources": [
            dict(source) for source in _dict_items(plan.get("data_sources"))
        ],
        "api_contracts": [
            dict(contract) for contract in _dict_items(plan.get("api_contracts"))
        ],
    }
    # task_inputs 是旧版派生字段，确认反馈后不再继续持久化。
    updated.pop("task_inputs", None)

    applied = False
    if _mentions_database(user_feedback):
        for source in updated["data_sources"]:
            source["type"] = "database"
        architecture = (
            updated.get("architecture")
            if isinstance(updated.get("architecture"), dict)
            else {}
        )
        updated["architecture"] = {
            **architecture,
            "data": "Database-backed data sources based on ProjectPlan confirmation feedback.",
        }
        applied = True

    if applied:
        updated.setdefault("plan_feedback_updates", []).append(
            {
                "source": "project_plan_confirmation_feedback",
                "feedback": user_feedback,
            }
        )
    return normalize_project_plan(updated)


def _mentions_database(feedback: str) -> bool:
    normalized = _norm(feedback)
    return "database" in normalized or "数据库" in normalized


def _permission_model(
    spec: dict[str, Any],
    plan_pages: list[dict[str, Any]],
    agent_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    """基于页面叶子构造权限模型，避免菜单目录节点进入访问控制表。"""

    role_ids = [role["id"] for role in spec["user_roles"]]
    model = {
        "roles": spec["user_roles"],
        "page_access": [
            {
                "pageId": page["pageId"],
                "path": page["path"],
                "allowed_roles": page.get("references", {}).get("permissions", []),
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


def create_project_plan(
    spec: dict[str, Any],
    agent_note: str = "live main-agent project planning",
    planning_source: str = "main_agent_live",
    agent_plan: dict[str, Any] | None = None,
    authoritative_agent_plan: bool = False,
) -> dict[str, Any]:
    """生成 ProjectPlan，并把页面叶子组织成带菜单层级的 frontend_pages 树。"""

    route_root_path = str(
        (
            spec.get("app_info")
            if isinstance(spec.get("app_info"), dict)
            else {}
        ).get("route_root_path")
        or ""
    ).strip()
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
            _api_contracts(data_sources),
            agent_plan,
            "api_contracts",
            authoritative=authoritative_agent_plan,
        )
    )
    data_sources = [
        {
            **source,
            "schema_refs": schema_refs_for_data_source(
                api_contracts,
                str(source.get("id") or ""),
            ),
        }
        for source in data_sources
    ]
    frontend_page_leaves = _normalize_frontend_pages(
        _merge_agent_items(
            _frontend_pages(spec),
            agent_plan,
            "frontend_pages",
            authoritative=authoritative_agent_plan,
        )
    )
    frontend_page_leaves = normalize_page_dependencies(frontend_page_leaves, api_contracts)
    frontend_pages = rebuild_frontend_page_tree(
        _agent_section(agent_plan, "frontend_pages"),
        frontend_page_leaves,
        module_names=_module_name_map(spec),
        root_route_prefix=route_root_path,
    )

    agent_architecture = _agent_section(agent_plan, "architecture")
    architecture = {
        "frontend": "基于单页应用生成页面、路由和 API 客户端。",
        "backend": "后端技术栈固定为 Java8 + Springboot，提供本地 API 服务、资源接口和业务契约实现。",
        "data": "数据库固定使用 MySQL8，缓存固定使用 Redis。",
        "backend_tech_stack": dict(BACKEND_TECH_STACK),
        "testing": "交付前执行单元、契约、集成和冒烟检查。",
    }
    if isinstance(agent_architecture, dict):
        architecture.update(agent_architecture)
    # 后端技术栈属于项目规划阶段的硬性约束，模型输出即使遗漏或写偏也必须回写为固定值。
    architecture["backend"] = "后端技术栈固定为 Java8 + Springboot，提供本地 API 服务、资源接口和业务契约实现。"
    architecture["data"] = "数据库固定使用 MySQL8，缓存固定使用 Redis。"
    architecture["backend_tech_stack"] = dict(BACKEND_TECH_STACK)

    plan: dict[str, Any] = {
        "version": "0.1.0",
        "status": "draft",
        "generated_at": datetime.now(UTC).isoformat(),
        "requirement_spec_version": spec["version"],
        "app": {
            "name": spec["app_info"]["name"],
            "summary": spec["app_info"]["summary"],
            **({"route_root_path": route_root_path} if route_root_path else {}),
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
        "permission_model": _permission_model(
            spec,
            frontend_page_leaves,
            agent_plan,
        ),
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
        "risks": (
            _string_items(_agent_section(agent_plan, "risks"))
            if authoritative_agent_plan
            and isinstance(_agent_section(agent_plan, "risks"), list)
            else [
                "API 契约字段模型仍是初版；细节确认若发现缺口，必须回到项目计划调整并重新确认。",
                "权限规则当前按角色粗粒度规划，后续需要确认到页面和操作级别。",
            ]
        ),
        "agent_note": agent_note,
        "planning_source": planning_source,
        "agent_plan_used": isinstance(agent_plan, dict),
        "approved": True,
    }

    # 兜底：大模型可能把某个契约的 Schema 错放到另一个契约，导致 Endpoint 跨契约引用。
    # 在计划落地前自动归位，避免问题延迟到细节确认阶段才以 Workflow failed 暴露。
    plan, schema_repairs = repair_cross_contract_schema_refs(plan)
    if schema_repairs:
        plan.setdefault("plan_feedback_updates", []).append(
            {
                "source": "api_contract_repair",
                "summary": "自动归位跨契约引用的 Schema。",
                "actions": schema_repairs,
            }
        )
    return normalize_project_plan(plan)
