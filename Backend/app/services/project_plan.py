from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import re
from typing import Any

from app.services.api_contract_repair import repair_cross_contract_schema_refs
from app.services.api_contracts import (
    normalize_api_contracts,
    schema_refs_for_entities,
)
from app.services.data_source_policy import (
    CANONICAL_DATASOURCE_TYPES,
    DatasourceType,
    EnabledDatasourceType,
    apply_authoritative_datasource_type,
    datasource_type_from_artifact,
    ensure_enabled_datasource_type,
)
from app.services.entity_definitions import (
    contract_data_source_id,
    data_source_type_label,
    entity_ids,
    entity_json_schema,
    entity_table_name,
    normalize_data_source_type,
    normalize_entity,
    normalize_entities,
    plan_data_sources,
    validate_entity_definitions,
)
from app.services.frontend_page_tree import (
    apply_frontend_page_route_hierarchy,
    _module_display_name,
    flatten_frontend_pages,
    group_pages_into_menu_tree,
    rebuild_frontend_page_tree,
)
from app.services.page_dependencies import normalize_page_dependencies
from app.services.requirement_spec import product_acceptance_criteria
from app.services.authorization_manifest import compile_authorization_manifest


BACKEND_TECH_STACK = {
    "language": "Java8",
    "framework": "Springboot",
    "database": "MySQL8",
    "cache": "Redis",
}

STATIC_BACKEND_TECH_STACK = {
    "language": "Java8",
    "framework": "Springboot",
}

TECHNICAL_PLAN_ARTIFACT_TYPE = "technical-plan"


def _architecture_for_datasource_type(
    datasource_type: EnabledDatasourceType,
) -> dict[str, Any]:
    """按数据源类型生成不可被模型改写的项目架构边界。"""

    if datasource_type == "static":
        return {
            "frontend": "基于单页应用生成页面、路由和前端内存 Mock 数据访问模块。",
            "backend": "保留 Java8 + Springboot 基础工程边界，不生成数据源业务 Endpoint。",
            "data": "业务数据由前端内存 Mock 提供，仅用于开发测试，不使用数据库或缓存。",
            "backend_tech_stack": dict(STATIC_BACKEND_TECH_STACK),
            "data_contract": "前端 Mock 数据契约，不代表真实 HTTP 后端。",
            "testing": "交付前执行前端单元、契约、集成和冒烟检查。",
        }
    return {
        "frontend": "基于单页应用生成页面、路由和 API 客户端。",
        "backend": "后端技术栈固定为 Java8 + Springboot，提供真实 HTTP API、资源接口和业务契约实现。",
        "data": "数据库固定使用 MySQL8，缓存固定使用 Redis。",
        "backend_tech_stack": dict(BACKEND_TECH_STACK),
        "data_contract": "真实 HTTP API 契约。",
        "testing": "交付前执行单元、契约、集成和冒烟检查。",
    }


def _architecture_for_sources(
    data_sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """按数据源类型集合聚合架构边界，支持数据库/外部 API/静态数据混合。"""

    source_types = {
        str(source.get("type") or "")
        for source in data_sources
        if isinstance(source, dict) and str(source.get("type") or "").strip()
    }
    if source_types and source_types <= {"static"}:
        return _architecture_for_datasource_type("static")
    architecture = _architecture_for_datasource_type("database")
    if "external_api" in source_types:
        architecture = {
            **architecture,
            "backend": (
                "后端技术栈固定为 Java8 + Springboot，提供真实 HTTP API、资源接口、"
                "业务契约实现和第三方接口对接。"
            ),
            "data": (
                "数据库数据源固定使用 MySQL8，缓存固定使用 Redis；外部 API 数据源"
                "通过后端 HTTP 客户端访问第三方接口。"
            ),
        }
    if "static" in source_types:
        architecture = {
            **architecture,
            "frontend": (
                "基于单页应用生成页面、路由和 API 客户端；静态数据源使用前端内存 "
                "Mock 数据模块，仅用于开发测试。"
            ),
        }
    return architecture


def apply_project_plan_datasource_policy(
    project_plan: dict[str, Any],
    datasource_type: DatasourceType | None = None,
) -> dict[str, Any]:
    """按已确认实体设计的数据源集合聚合架构边界。

    应用级不再有数据源类型；实体设计确认前数据源清单为空时，
    架构使用通用 Java8 + Springboot 默认边界。
    """

    projected = deepcopy(project_plan)
    architecture = (
        dict(projected.get("architecture"))
        if isinstance(projected.get("architecture"), dict)
        else {}
    )
    if projected.get("artifact_type") == TECHNICAL_PLAN_ARTIFACT_TYPE:
        effective_type = (
            ensure_enabled_datasource_type(datasource_type)
            if datasource_type is not None
            else datasource_type_from_artifact(projected, fallback="database")
        )
        policy = _architecture_for_datasource_type(effective_type)
        for key in ("backend", "data"):
            architecture[key] = policy[key]
        architecture.setdefault("frontend", policy["frontend"])
        architecture = {
            key: architecture.get(key, policy[key])
            for key in ("frontend", "backend", "data")
        }
        projected["architecture"] = architecture
        return projected

    data_sources = plan_data_sources(projected)
    if not data_sources:
        # 实体设计确认前保留计划生成时的架构边界，避免用默认值覆盖静态/数据库架构。
        architecture.pop("orm", None)
        architecture.pop("migration", None)
        projected["architecture"] = architecture
        return projected
    policy = _architecture_for_sources(data_sources)
    source_types = {
        str(source.get("type") or "") for source in data_sources
    }
    if source_types and source_types <= {"static"}:
        # Static 只保留允许的架构字段，避免模型通过 orm/migration 等扩展键带回数据库实现。
        policy["testing"] = architecture.get("testing") or policy["testing"]
        projected["architecture"] = policy
        return projected
    for key in ("backend", "data", "backend_tech_stack", "data_contract"):
        architecture[key] = policy[key]
    architecture["frontend"] = policy["frontend"]
    architecture.setdefault("testing", policy["testing"])
    projected["architecture"] = architecture
    return projected


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
    if key == "data_sources":
        # 实体优先：模型既可能输出顶层 entities（规范形状，字段名以模型为准），
        # 也可能输出 data_sources（旧形状）。两者都作为实体字段定义来源合并，
        # 顶层 entities 追加在最后，保证其字段定义覆盖旧形状中的同名实体。
        agent_sources: list[dict[str, Any]] = []
        if isinstance(agent_items, list):
            agent_sources.extend(
                _normalize_agent_item_aliases(item, key)
                for item in agent_items
                if isinstance(item, dict) and item.get("id")
            )
        top_level_entities = _agent_section(agent_plan, "entities")
        if isinstance(top_level_entities, list):
            for item in top_level_entities:
                if not isinstance(item, dict):
                    continue
                source_type = normalize_data_source_type(item.get("data_source"))
                agent_sources.append(
                    {
                        "id": source_type,
                        "type": source_type,
                        "entities": [item],
                    }
                )
        if not agent_sources:
            return default_items
        # 数据源以模型声明为骨架：模型选择的源 id/类型保留，需求实体回填不丢失。
        return _merge_planned_data_sources(default_items, agent_sources)
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
        # ProjectPlan.frontend_pages 不能为空；若模型遗漏页面或只输出空目录壳，回退到需求文档派生的默认页面。
        if key == "frontend_pages" and not agent_items and default_items:
            return default_items
        # 模型显式返回空契约时保留由数据源确定性生成的契约，避免业务计划静默退化为空数组。
        if key == "api_contracts" and not agent_items and default_items:
            return default_items

        if key == "frontend_pages":
            # 页面集合来自需求文档（default_items），不得增删改。
            # 模型只负责往已有 pageId 上补充 references/依赖标注，不负责发明新页面。
            agent_by_id = {
                str(item[identity_key]): item
                for item in agent_items
                if item.get(identity_key)
            }
            seen_ids: set[str] = set()
            result: list[dict[str, Any]] = []
            for default in default_items:
                page_id = str(default.get(identity_key, ""))
                if not page_id or page_id in seen_ids:
                    continue
                seen_ids.add(page_id)
                agent_supplement = agent_by_id.get(page_id, {})
                result.append(_merge_agent_item(default, agent_supplement))
            return result

        defaults_by_id = {
            str(item[identity_key]): item
            for item in default_items
            if item.get(identity_key)
        }
        merged: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in agent_items:
            item_id = str(item[identity_key])
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            merged.append(
                _merge_agent_item(
                    defaults_by_id.get(item_id)
                    or (
                        default_items[0]
                        if key == "api_contracts"
                        and len(default_items) == 1
                        and len(agent_items) == 1
                        else {}
                    ),
                    item,
                )
            )
        return merged

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
    normalized.pop("contract_id", None)
    normalized.pop("contractId", None)
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


def _merge_planned_data_sources(
    default_sources: list[dict[str, Any]],
    agent_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按实体分配数据源类型：模型指定类型优先，未指定实体用默认类型。"""

    entity_type: dict[str, str] = {}
    agent_entities: list[tuple[str, dict[str, Any]]] = []
    for agent in agent_sources:
        source_type = normalize_data_source_type(
            agent.get("type") or agent.get("id")
        )
        for entity in normalize_entities(agent.get("entities"), with_types=True):
            entity_id = str(entity.get("id") or "")
            if not entity_id:
                continue
            entity_type.setdefault(entity_id, source_type)
            agent_entities.append((entity_id, entity))
    default_type = (
        normalize_data_source_type(default_sources[0].get("type"))
        if default_sources
        else "database"
    )
    result: list[dict[str, Any]] = []
    covered_entity_ids: set[str] = set()
    agent_entity_by_id = {
        entity_id: entity for entity_id, entity in agent_entities
    }
    for default in default_sources:
        for entity in normalize_entities(default.get("entities"), with_types=True):
            entity_id = str(entity.get("id") or "")
            if not entity_id or entity_id in covered_entity_ids:
                continue
            covered_entity_ids.add(entity_id)
            source_type = entity_type.get(entity_id) or default_type
            merged_entity = _merge_entity_with_agent_fields(
                entity,
                agent_entity_by_id.get(entity_id),
            )
            result.append(
                {
                    "id": source_type,
                    "name": data_source_type_label(source_type),
                    "type": source_type,
                    "entities": [merged_entity],
                    "schema_refs": [],
                    "seed_strategy": "demo_records",
                }
            )
    for entity_id, entity in agent_entities:
        if entity_id in covered_entity_ids:
            continue
        covered_entity_ids.add(entity_id)
        source_type = entity_type.get(entity_id) or default_type
        result.append(
            {
                "id": source_type,
                "name": data_source_type_label(source_type),
                "type": source_type,
                "entities": [entity],
                "schema_refs": [],
                "seed_strategy": "demo_records",
            }
        )
    return result


def _merge_entity_with_agent_fields(
    default_entity: dict[str, Any],
    agent_entity: dict[str, Any] | None,
) -> dict[str, Any]:
    """合并实体时以模型字段为完整集合；模型未提供字段时才回退需求展示项。"""

    if agent_entity is None:
        return default_entity
    agent_fields = [
        field
        for field in (agent_entity.get("fields") or [])
        if isinstance(field, dict)
    ]
    # 模型已生成字段定义时以其为准：需求层的展示项没有字段名，回填会把中文标签
    # 兜底成 field_N，破坏“字段名以模型 name 为准”的约束。
    merged_fields = agent_fields or list(default_entity.get("fields") or [])
    return {
        **default_entity,
        **agent_entity,
        "fields": merged_fields,
    }


def _ensure_contract_sources(
    project_plan: dict[str, Any],
    default_type: str | None = None,
) -> dict[str, Any]:
    """以实体为契约唯一绑定：计划阶段不生成 data_source，契约数据源由实体设计反查。"""

    del default_type
    updated = deepcopy(project_plan)
    entities = _dict_items(updated.get("entities"))
    contracts = _dict_items(updated.get("api_contracts"))
    if not contracts:
        return updated
    entity_to_source: dict[str, str] = {}
    for entity in entities:
        entity_id = str(entity.get("id") or "")
        raw_source = entity.get("data_source")
        if entity_id and raw_source is not None and str(raw_source).strip():
            source_type = normalize_data_source_type(raw_source)
            if source_type:
                entity_to_source[entity_id] = source_type
    added_entities: list[dict[str, Any]] = []
    for contract in contracts:
        entity_ids_list = _string_items(contract.get("entity_ids"))
        if not entity_ids_list:
            continue
        resolved_entity_ids: list[str] = []
        for entity_id in entity_ids_list:
            if entity_id not in entity_to_source:
                entity_to_source.setdefault(entity_id, "")
                if not any(str(item.get("id") or "") == entity_id for item in entities):
                    added_entities.append(
                        {
                            "id": entity_id,
                            "name": entity_id,
                            "description": "",
                            "fields": [],
                        }
                    )
            resolved_entity_ids.append(entity_id)
        contract["entity_ids"] = resolved_entity_ids
    if added_entities:
        updated["entities"] = [*entities, *added_entities]
    return updated


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


def _menu_leaf_path_from_pageId(pageId: str) -> str:
    """在启用菜单时，为首页类页面生成非根节点的稳定叶子路由。"""

    route = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(pageId or "home")).strip("-_")
    route = route.replace("_", "-").lower() or "home"
    if route.endswith("-page") and route != "dashboard-page":
        route = route[: -len("-page")] or route
    if route in {"dashboard", "dashboard-page", "home", "index"}:
        route = "home"
    return f"/{route}"


def _normalize_data_sources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规范化 ProjectPlan 数据源业务字段，不接受 mock 等旧类型。"""

    normalized = []
    for item in items:
        source = {key: value for key, value in item.items() if key != "schema"}
        source_type = ensure_enabled_datasource_type(
            str(item.get("type") or "")  # type: ignore[arg-type]
        )
        normalized.append(
            {
                **source,
                "name": str(item.get("name") or item.get("id") or "数据源"),
                "description": str(item.get("description") or ""),
                "type": source_type,
                "entities": normalize_entities(
                    item.get("entities"),
                    with_types=True,
                ),
                "schema_refs": _string_items(item.get("schema_refs")),
            }
        )
    return normalized


def _normalize_api_contracts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return normalize_api_contracts(items)


def normalize_project_plan(project_plan: dict[str, Any]) -> dict[str, Any]:
    """规范化主工作流 ProjectPlan 的内部结构与菜单树。"""

    normalized = dict(project_plan)
    route_root_path = _route_root_path_from_plan(normalized)
    menu_enabled = _menu_enabled_from_plan(normalized)
    if "api_contracts" in normalized:
        normalized["api_contracts"] = _normalize_api_contracts(
            _dict_items(normalized.get("api_contracts"))
        )
    if "entities" in normalized:
        normalized["entities"] = normalize_entities(
            normalized.get("entities"),
            with_types=True,
        )
    # 实体优先：数据源绑定在实体内，不单独持久化顶层 data_sources。
    normalized.pop("data_sources", None)
    if "frontend_pages" in normalized:
        flat_pages = _normalize_frontend_pages(
            flatten_frontend_pages(normalized.get("frontend_pages"))
        )
        flat_pages = _apply_menu_home_route_rule(
            flat_pages,
            menu_enabled=menu_enabled,
            route_root_path=route_root_path,
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
            menu_enabled=menu_enabled,
        )
    normalized = _ensure_contract_sources(normalized)
    return normalized


def _route_root_path_from_plan(project_plan: dict[str, Any]) -> str:
    """优先从正式计划 app 信息中读取页面根路由。"""

    app = project_plan.get("app") if isinstance(project_plan.get("app"), dict) else {}
    return str(app.get("route_root_path") or "").strip()


def _menu_enabled_from_plan(project_plan: dict[str, Any]) -> bool:
    """读取当前计划是否启用了菜单模式。"""

    app = project_plan.get("app") if isinstance(project_plan.get("app"), dict) else {}
    return bool(app.get("menu_enabled"))


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


def _apply_menu_home_route_rule(
    pages: list[dict[str, Any]],
    *,
    menu_enabled: bool,
    route_root_path: str,
) -> list[dict[str, Any]]:
    """启用菜单时，避免首页类页面继续占用根路由。"""

    if not menu_enabled:
        return [dict(page) for page in pages]
    normalized_root = route_root_path.rstrip("/") if route_root_path and route_root_path != "/" else ""
    adjusted: list[dict[str, Any]] = []
    for page in pages:
        current = dict(page)
        page_id = str(current.get("pageId") or current.get("id") or "").strip()
        path = str(current.get("path") or "").strip()
        if path == "/" or (normalized_root and path == normalized_root):
            current["path"] = (
                f"{normalized_root}{_menu_leaf_path_from_pageId(page_id)}"
                if normalized_root
                else _menu_leaf_path_from_pageId(page_id)
            )
        adjusted.append(current)
    return adjusted


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


def _business_text(value: Any) -> str:
    """递归提取 RequirementSpec 业务文本，供契约操作判断使用。"""

    if isinstance(value, dict):
        return " ".join(_business_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_business_text(item) for item in value)
    return str(value or "")


def _source_contract_context(
    spec: dict[str, Any],
    data_source: dict[str, Any],
) -> str:
    """汇总与单个数据源相关的页面、模块、流程和验收信息。"""

    source_id = str(data_source.get("id") or "")
    source_terms = {
        str(item).strip()
        for item in [
            source_id.removesuffix("_source"),
            str(data_source.get("name") or "").replace("数据源", ""),
            *entity_ids(data_source.get("entities")),
        ]
        if str(item).strip()
    }
    related_modules = [
        module
        for module in _dict_items(spec.get("feature_modules"))
        if str(module.get("id") or "") in source_id
        or any(term in _business_text(module) for term in source_terms)
    ]
    module_ids = {str(module.get("id") or "") for module in related_modules}
    related_pages = [
        page
        for page in _dict_items(spec.get("pages"))
        if str(page.get("module_id") or "") in module_ids
        or any(term in _business_text(page) for term in source_terms)
    ]
    relation_terms = source_terms | {
        str(module.get("name") or "").strip()
        for module in related_modules
        if str(module.get("name") or "").strip()
    }
    related_flows = [
        flow
        for flow in _dict_items(spec.get("business_flows"))
        if any(term in _business_text(flow) for term in relation_terms)
    ]
    related_acceptance = [
        item
        for item in _string_items(spec.get("acceptance_criteria"))
        if any(term in item for term in relation_terms)
    ]
    return " ".join(
        [
            _business_text(data_source),
            _business_text(related_modules),
            _business_text(related_pages),
            _business_text(related_flows),
            _business_text(related_acceptance),
        ]
    )


def _required_contract_operations(context: str) -> list[str]:
    """根据业务描述确定契约所需操作，避免无条件生成完整 CRUD。"""

    operation_keywords = {
        "list": ("列表", "查询", "搜索", "筛选", "分页", "展示", "查看", "list", "search"),
        "detail": ("详情", "明细", "单条", "detail"),
        "create": ("创建", "新增", "添加", "录入", "提交", "create", "add"),
        "update": ("更新", "修改", "编辑", "状态流转", "审批", "update", "edit"),
        "delete": ("删除", "移除", "注销", "delete", "remove"),
    }
    lowered = context.lower()
    operations = [
        operation
        for operation, keywords in operation_keywords.items()
        if any(keyword in lowered for keyword in keywords)
    ]
    return operations or ["list"]


def _api_contracts(
    data_sources: list[dict[str, Any]],
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """按实体生成最小必要数据访问契约，契约只绑定实体 id。"""

    contracts: list[dict[str, Any]] = []
    for data_source in data_sources:
        data_source_type = str(data_source.get("type") or "database")
        operations = _required_contract_operations(
            _source_contract_context(spec, data_source)
        )
        for entity_object in normalize_entities(
            data_source.get("entities"),
            with_types=True,
        ):
            entity = str(entity_object.get("id") or "")
            entity_name = str(entity_object.get("name") or entity)
            route_base = entity_table_name(entity) or _entity_route_slug(entity_name)
            contract_id = f"{route_base}_api"
            entity_schema = _entity_schema(entity_object)
            schemas: dict[str, Any] = {entity: entity_schema}
            endpoints: list[dict[str, Any]] = []
            if "list" in operations:
                schemas[f"{entity}ListOutput"] = {
                    "type": "object",
                    "properties": {
                        "total": {"type": "integer"},
                        "pageSize": {"type": "integer"},
                        "current": {"type": "integer"},
                        "list": {"type": "array", "items": {"$ref": entity}},
                    },
                    "required": ["total", "pageSize", "current", "list"],
                }
                endpoints.append({
                    "id": f"{contract_id}.list",
                    "method": "GET",
                    "path": f"/api/{route_base}",
                    "summary": f"查询{entity_name}列表。",
                    "parameters": [
                        {"name": "current", "in": "query", "required": False, "schema": {"type": "integer", "default": 1}},
                        {"name": "pageSize", "in": "query", "required": False, "schema": {"type": "integer", "default": 20}},
                    ],
                    "response_schema_ref": f"{entity}ListOutput",
                    "error_codes": ["UNAUTHORIZED"],
                })
            if "detail" in operations:
                endpoints.append({
                    "id": f"{contract_id}.detail",
                    "method": "GET",
                    "path": f"/api/{route_base}/{{id}}",
                    "summary": f"查询单条{entity_name}详情。",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "response_schema_ref": entity,
                    "error_codes": ["NOT_FOUND"],
                })
            if "create" in operations:
                schemas[f"{entity}CreateInput"] = _write_schema(entity_schema, partial=False)
                endpoints.append({
                    "id": f"{contract_id}.create",
                    "method": "POST",
                    "path": f"/api/{route_base}",
                    "summary": f"创建{entity_name}。",
                    "request_schema_ref": f"{entity}CreateInput",
                    "response_schema_ref": entity,
                    "error_codes": ["VALIDATION_ERROR"],
                })
            if "update" in operations:
                schemas[f"{entity}UpdateInput"] = _write_schema(entity_schema, partial=True)
                endpoints.append({
                    "id": f"{contract_id}.update",
                    "method": "PATCH",
                    "path": f"/api/{route_base}/{{id}}",
                    "summary": f"更新{entity_name}。",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "request_schema_ref": f"{entity}UpdateInput",
                    "response_schema_ref": entity,
                    "error_codes": ["VALIDATION_ERROR", "NOT_FOUND"],
                })
            if "delete" in operations:
                endpoints.append({
                    "id": f"{contract_id}.delete",
                    "method": "DELETE",
                    "path": f"/api/{route_base}/{{id}}",
                    "summary": f"删除{entity_name}。",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "error_codes": ["NOT_FOUND"],
                })
            contracts.append(
                {
                    "id": contract_id,
                    "entity_ids": [entity],
                    "base_path": f"/api/{route_base}",
                    "authentication": {"required": True},
                    "schemas": schemas,
                    "endpoints": endpoints,
                }
            )
    return contracts


def _entity_route_slug(entity_name: str) -> str:
    """把实体展示名称转换为 snake_case 路由片段。"""

    slug = re.sub(r"[^a-z0-9]+", "_", str(entity_name or "").lower()).strip("_")
    return slug or "entity"


def _entity_schema(entity: dict[str, Any]) -> dict[str, Any]:
    """从已确认业务实体字段派生 API 契约 Schema，id 作为隐式主键。"""

    return entity_json_schema(entity)


def _write_schema(entity_schema: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    properties = {
        key: value
        for key, value in entity_schema["properties"].items()
        if key != "id"
    }
    required = (
        []
        if partial
        else [key for key in entity_schema["required"] if key in properties]
    )
    return {"type": "object", "properties": properties, "required": required}


def _frontend_pages(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """根据 RequirementSpec 与实体构造平铺页面叶子，按实体名绑定接口。"""

    pages = []
    used_paths: set[str] = set()
    entity_names = {
        str(entity.get("id") or "")
        for entity in _dict_items(spec.get("entities"))
        if str(entity.get("id") or "").strip()
    }
    for page in spec["pages"]:
        pageId = str(page.get("pageId") or "page")
        page_name = str(page.get("name") or pageId)
        module_id = str(page.get("module_id") or "core")
        path = _unique_page_path(
            str(page.get("path") or _path_from_pageId(pageId)),
            pageId,
            used_paths,
        )
        if module_id == "access_control":
            related_entities: list[str] = []
        else:
            related_entities = [
                entity_name
                for entity_name in [_plan_entity_name_from_module(module_id)]
                if entity_name in entity_names
            ]
            if not related_entities:
                # 概览等非业务页面兜底绑定第一个业务实体，保证可解析读取接口。
                related_entities = [
                    entity_name
                    for entity_name in entity_names
                    if entity_name not in {"User", "Role"}
                ][:1] or list(entity_names)[:1]
        pages.append(
            {
                "pageId": pageId,
                "name": page_name,
                "path": path,
                "module_id": module_id,
                "description": str(page.get("description") or page_name or "业务页面"),
                "data_dependencies": related_entities,
                "states": ["loading", "empty", "error", "ready"],
                "permissions": (
                    ["admin", "user"] if page.get("path") != "/login" else ["anonymous"]
                ),
            }
        )
    return pages


def _plan_entity_name_from_module(module_id: str) -> str:
    """从模块 id 派生实体名，去掉 management 等后缀得到干净实体名。"""

    parts = [part for part in str(module_id or "").split("_") if part]
    if parts and parts[-1] == "management":
        parts = parts[:-1]
    return "".join(part.title() for part in parts) or "Core"


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


def _planned_data_sources(
    spec: dict[str, Any],
    default_type: str = "database",
) -> list[dict[str, Any]]:
    """把需求实体归并为数据源：一实体一源，类型默认应用默认类型。"""

    planned_sources: list[dict[str, Any]] = []
    for entity in _dict_items(spec.get("entities")):
        source_type = normalize_data_source_type(entity.get("data_source"))
        if source_type == "database" and default_type in {"database", "static", "external_api"}:
            source_type = default_type
        planned_sources.append(
            {
                "id": source_type,
                "name": data_source_type_label(source_type),
                "description": str(entity.get("description") or "业务数据。"),
                "type": source_type,
                "entities": [entity],
                "schema_refs": [],
                "seed_strategy": "demo_records",
            }
        )
    return planned_sources


def _entities_from_sources(
    data_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把规划数据源列表转换为顶层实体列表；计划阶段不写入 data_source。"""

    entities: list[dict[str, Any]] = []
    for source in data_sources:
        for entity in normalize_entities(source.get("entities"), with_types=True):
            entities.append(entity)
    return entities


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
        criteria = product_acceptance_criteria(agent_criteria)
        if criteria:
            return criteria
    return product_acceptance_criteria(spec["acceptance_criteria"])


def apply_project_plan_feedback(
    plan: dict[str, Any],
    user_feedback: str,
    datasource_type: DatasourceType | None = None,
) -> dict[str, Any]:
    """清理旧派生字段，并保持反馈不能改变应用数据源类型。"""

    if plan.get("artifact_type") == TECHNICAL_PLAN_ARTIFACT_TYPE:
        return dict(plan)
    if not _text(user_feedback):
        return plan

    updated = {
        **plan,
        "frontend_pages": normalize_project_plan(plan).get("frontend_pages", []),
        "entities": _dict_items(plan.get("entities")),
        "api_contracts": [
            dict(contract) for contract in _dict_items(plan.get("api_contracts"))
        ],
    }
    # task_inputs 是旧版派生字段，确认反馈后不再继续持久化。
    updated.pop("task_inputs", None)

    effective_type = (
        ensure_enabled_datasource_type(datasource_type)
        if datasource_type is not None
        else datasource_type_from_artifact(updated, fallback="database")
    )
    return normalize_project_plan(
        apply_project_plan_datasource_policy(updated, effective_type)
    )


def validate_project_plan_datasource_policy(
    project_plan: dict[str, Any],
    datasource_type: DatasourceType | None = None,
) -> list[str]:
    """按源校验 ProjectPlan 数据源类型、契约引用和架构实现边界。

    计划阶段实体不携带 data_source；实体设计确认前数据源清单为空时，
    契约数据源引用校验延后到实体设计阶段，不在此处误报。
    """

    del datasource_type
    errors: list[str] = []
    sources = plan_data_sources(project_plan)
    source_ids = {str(source.get("id") or "") for source in sources}
    designed_entity_ids = {
        str(entity.get("id") or "")
        for source in sources
        for entity in normalize_entities(source.get("entities"))
        if str(entity.get("id") or "").strip()
    }
    for source in sources:
        source_type = str(source.get("type") or "").strip()
        if source_type not in CANONICAL_DATASOURCE_TYPES:
            errors.append(
                f"ProjectPlan 数据源 {source.get('id') or 'unknown'} 类型非法："
                f"{source_type or '空'}。"
            )
    for contract in _dict_items(project_plan.get("api_contracts")):
        contract_entity_ids = set(_string_items(contract.get("entity_ids")))
        if not sources or not contract_entity_ids:
            continue
        # 实体尚未完成实体设计时允许契约暂缺数据源，实体设计确认后再校验。
        if not contract_entity_ids <= designed_entity_ids:
            continue
        resolved_source_id = contract_data_source_id(project_plan, contract)
        if not resolved_source_id or resolved_source_id not in source_ids:
            errors.append(
                f"数据契约 {contract.get('id') or 'unknown'} 引用了不存在的数据源。"
            )
    architecture = (
        project_plan.get("architecture")
        if isinstance(project_plan.get("architecture"), dict)
        else {}
    )
    stack = (
        architecture.get("backend_tech_stack")
        if isinstance(architecture.get("backend_tech_stack"), dict)
        else {}
    )
    architecture_text = _business_text(architecture).lower()
    if project_plan.get("artifact_type") == TECHNICAL_PLAN_ARTIFACT_TYPE:
        if set(architecture) != {"frontend", "backend", "data"}:
            errors.append("TechnicalPlan architecture 必须且只能包含 frontend、backend、data。")
        if not all(isinstance(architecture.get(key), str) and architecture[key].strip() for key in ("frontend", "backend", "data")):
            errors.append("TechnicalPlan architecture 的 frontend、backend、data 必须是非空字符串。")
        if "java8" not in architecture_text or "springboot" not in architecture_text:
            errors.append("TechnicalPlan backend 必须体现 Java8 和 Springboot。")
        if "mysql8" not in architecture_text or "redis" not in architecture_text:
            errors.append("TechnicalPlan data 必须体现 MySQL8 和 Redis。")
        return errors
    source_types = {
        str(source.get("type") or "") for source in sources
    }
    if source_types and source_types <= {"static"}:
        forbidden = ("mysql", "redis", "mybatis", "数据库迁移", "database migration")
        if any(item in architecture_text for item in forbidden):
            errors.append("Static ProjectPlan 不得声明数据库、缓存、MyBatis 或数据库迁移。")
        if any(key in stack for key in ("database", "cache", "orm", "migration")):
            errors.append("Static ProjectPlan 后端技术栈不得包含数据库实现字段。")
    elif stack.get("database") != "MySQL8" or stack.get("cache") != "Redis":
        errors.append("Database ProjectPlan 必须保留 MySQL8 和 Redis。")
    return errors


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


def _engineering_design(
    agent_plan: dict[str, Any] | None,
    *,
    architecture: dict[str, Any],
) -> dict[str, Any]:
    """规范主工作流 ProjectPlan 的工程设计，不混入产品或视觉决策。"""

    value = _agent_section(agent_plan, "engineering_design")
    design = dict(value) if isinstance(value, dict) else {}
    defaults: dict[str, Any] = {
        "module_boundaries": [],
        "data_models": [],
        "storage_and_indexes": [],
        "cache_strategy": [],
        "transactions_and_idempotency": [],
        "error_model": [],
        "security_controls": [],
        "observability": [],
        "performance_targets": [],
        "deployment_constraints": [],
        "test_strategy": [str(architecture.get("testing") or "执行契约、集成和冒烟检查。")],
    }
    for key, fallback in defaults.items():
        current = design.get(key)
        if isinstance(current, list):
            defaults[key] = [item for item in current if isinstance(item, (str, dict))]
    return defaults


def _technical_engineering_design(
    agent_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    """只保留 TechnicalPlan 的模块边界和数据模型。"""

    value = _agent_section(agent_plan, "engineering_design")
    design = value if isinstance(value, dict) else {}
    return {
        key: [item for item in design.get(key, []) if isinstance(item, (str, dict))]
        if isinstance(design.get(key), list)
        else []
        for key in ("module_boundaries", "data_models")
    }


def _technical_entities(
    spec: dict[str, Any],
    agent_plan: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """沿用旧 ProjectPlan 语义，把需求实体补全为 TechnicalPlan 权威字段定义。"""

    requirement_entities = normalize_entities(spec.get("entities"))
    planned_entities = {
        str(item.get("id") or ""): item
        for item in _dict_items(_agent_section(agent_plan, "entities"))
        if str(item.get("id") or "").strip()
    }
    result: list[dict[str, Any]] = []
    for index, requirement_entity in enumerate(requirement_entities):
        entity_id = str(requirement_entity.get("id") or "")
        supplement = planned_entities.get(entity_id, {})
        fields = (
            supplement.get("fields")
            if isinstance(supplement.get("fields"), list)
            and supplement.get("fields")
            else requirement_entity.get("fields", [])
        )
        result.append(
            normalize_entity(
                {
                    **requirement_entity,
                    "fields": fields,
                },
                index,
                with_types=True,
            )
        )
    return result


def _technical_plan_pages(
    spec: dict[str, Any],
    agent_plan: dict[str, Any] | None,
    api_contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """只保存页面身份和 TechnicalPlan 新增的 endpoint 技术引用。"""

    product_plan = (
        spec.get("confirmed_product_plan")
        if isinstance(spec.get("confirmed_product_plan"), dict)
        else {}
    )
    source_pages = _dict_items(product_plan.get("pages")) or _dict_items(spec.get("pages"))
    raw_agent_pages = _agent_section(agent_plan, "pages")
    agent_pages = {
        str(page.get("pageId") or ""): page
        for page in _dict_items(raw_agent_pages)
        if page.get("pageId")
    }
    normalized = normalize_page_dependencies(
        [
            {
                "pageId": str(page.get("pageId") or ""),
                "references": (
                    agent_pages.get(str(page.get("pageId") or ""), {}).get("references")
                    if isinstance(
                        agent_pages.get(str(page.get("pageId") or ""), {}).get("references"),
                        dict,
                    )
                    else agent_pages.get(str(page.get("pageId") or ""), {})
                ),
            }
            for page in source_pages
            if page.get("pageId")
        ],
        api_contracts,
        include_action_implementations=True,
    )
    return [
        {
            "pageId": str(page.get("pageId") or ""),
            "references": {
                "endpoint_dependencies": [
                    {
                        "endpoint_id": str(item.get("endpoint_id") or ""),
                        "usage": str(item.get("usage") or "read"),
                        "trigger": str(item.get("trigger") or "页面交互触发"),
                        "required_for_initial_load": bool(
                            item.get("required_for_initial_load")
                        ),
                    }
                    for item in _dict_items(
                        (page.get("references") or {}).get("endpoint_dependencies")
                    )
                    if item.get("endpoint_id")
                ],
                "action_implementations": _technical_action_implementations(
                    (page.get("references") or {}).get("action_implementations")
                ),
            },
        }
        for page in normalized
    ]


def _technical_action_implementations(value: Any) -> list[dict[str, Any]]:
    """只保留 ProductPlan 业务 action 到 endpoint 的最小引用。"""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _dict_items(value):
        action_id = str(item.get("actionId") or "").strip()
        if not action_id or action_id in seen:
            continue
        step_bindings = [
            {
                "stepId": str(binding.get("stepId") or ""),
                "endpointId": str(binding.get("endpointId") or ""),
            }
            for binding in _dict_items(item.get("stepBindings"))
            if binding.get("stepId") and binding.get("endpointId")
        ]
        endpoint_id = str(item.get("endpointId") or "").strip()
        if step_bindings:
            result.append({"actionId": action_id, "stepBindings": step_bindings})
        elif endpoint_id:
            result.append({"actionId": action_id, "endpointId": endpoint_id})
        else:
            continue
        seen.add(action_id)
    return result


def _technical_api_contracts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤模型多余键，只持久化 API Contract 的开发边界字段。"""

    normalized = _normalize_api_contracts(items)
    contract_keys = (
        "id",
        "entity_ids",
        "base_path",
        "authentication",
        "schemas",
        "endpoints",
    )
    endpoint_keys = (
        "id",
        "method",
        "path",
        "summary",
        "parameters",
        "request_schema_ref",
        "response_schema_ref",
        "error_codes",
        "authentication",
    )
    return [
        {
            key: (
                [
                    {
                        endpoint_key: deepcopy(endpoint.get(endpoint_key))
                        for endpoint_key in endpoint_keys
                    }
                    for endpoint in _dict_items(contract.get("endpoints"))
                ]
                if key == "endpoints"
                else deepcopy(contract.get(key))
            )
            for key in contract_keys
        }
        for contract in normalized
    ]


def validate_technical_plan_model_authentication(agent_plan: Any) -> list[str]:
    """在归一化前校验模型认证字段，避免非法授权语义被静默丢弃。"""

    if not isinstance(agent_plan, dict):
        return []
    errors: list[str] = []
    for contract in _dict_items(agent_plan.get("api_contracts")):
        contract_id = str(contract.get("id") or "unknown")
        authentication = contract.get("authentication")
        if authentication is not None and (
            not isinstance(authentication, dict)
            or set(authentication) != {"required"}
            or not isinstance(authentication.get("required"), bool)
        ):
            errors.append(
                f"TechnicalPlan 模型输出 API Contract {contract_id} 的 authentication 只能包含 boolean required。"
            )
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_authentication = endpoint.get("authentication")
            if endpoint_authentication is not None and (
                not isinstance(endpoint_authentication, dict)
                or set(endpoint_authentication) != {"required"}
                or not isinstance(endpoint_authentication.get("required"), bool)
            ):
                errors.append(
                    f"TechnicalPlan 模型输出 Endpoint {endpoint.get('id') or 'unknown'} 的 authentication 只能包含 boolean required。"
                )
    return errors


def validate_technical_plan_model_authorization(agent_plan: Any) -> list[str]:
    """在归一化前拒绝模型越权输出的权限资源或数据权限字段。"""

    if not isinstance(agent_plan, dict):
        return []
    forbidden = {
        "authorization_manifest",
        "authorization_data_bindings",
        "dataRules",
        "dataPolicyBindings",
        "dataRuleKey",
        "policyKey",
        "permission_model",
        "permissionBindings",
        "resourceKey",
        "roles",
    }
    detected = sorted(set(agent_plan).intersection(forbidden))
    return [
        "TechnicalPlan 模型不得输出权限资源、角色授权或数据权限字段："
        + "、".join(detected)
    ] if detected else []


def _attach_technical_entity_field_refs(
    contracts: list[dict[str, Any]],
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """为确定性生成的同名 Schema 字段补充实体字段引用。"""

    fields_by_entity = {
        str(entity.get("id") or ""): {
            str(field.get("name") or "")
            for field in _dict_items(entity.get("fields"))
            if str(field.get("name") or "").strip()
        }
        for entity in entities
        if str(entity.get("id") or "").strip()
    }

    def attach(schema: Any, entity_ids: set[str]) -> Any:
        if not isinstance(schema, dict):
            return schema
        updated = deepcopy(schema)
        properties = updated.get("properties")
        if isinstance(properties, dict):
            for name, field_schema in properties.items():
                if not isinstance(field_schema, dict):
                    continue
                if "entity_field_ref" not in field_schema:
                    candidates = [
                        entity_id
                        for entity_id in entity_ids
                        if str(name) in fields_by_entity.get(entity_id, set())
                        or (str(name) == "id" and entity_id in fields_by_entity)
                    ]
                    if candidates:
                        updated_schema = dict(field_schema)
                        updated_schema["entity_field_ref"] = f"{candidates[0]}.{name}"
                        properties[name] = updated_schema
                        field_schema = updated_schema
                properties[name] = attach(field_schema, entity_ids)
        if isinstance(updated.get("items"), dict):
            updated["items"] = attach(updated["items"], entity_ids)
        for key in ("allOf", "anyOf", "oneOf"):
            if isinstance(updated.get(key), list):
                updated[key] = [attach(item, entity_ids) for item in updated[key]]
        return updated

    attached: list[dict[str, Any]] = []
    for contract in contracts:
        item = deepcopy(contract)
        entity_ids = {
            str(value).strip()
            for value in item.get("entity_ids", [])
            if str(value).strip()
        }
        if isinstance(item.get("schemas"), dict):
            item["schemas"] = {
                schema_id: attach(schema, entity_ids)
                for schema_id, schema in item["schemas"].items()
            }
        attached.append(item)
    return attached


_API_SCHEMA_PAGINATION_FIELDS = {"total", "pageSize", "current", "list"}
_API_SCHEMA_TRANSPORT_FIELDS = {
    "code",
    "count",
    "data",
    "detail",
    "error",
    "errors",
    "has_more",
    "message",
    "next_cursor",
    "request_id",
    "result",
    "results",
    "success",
    "trace_id",
}


def _technical_schema_field_refs(
    schema: Any,
    *,
    contract_id: str,
    schema_id: str,
    bound_entity_ids: set[str],
    entity_fields: dict[str, set[str]],
    path: str = "",
) -> list[str]:
    """校验 API Schema 业务属性到绑定实体字段的显式关联。"""

    if not isinstance(schema, dict):
        return []
    errors: list[str] = []
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for field_name, field_schema in properties.items():
            name = str(field_name or "").strip()
            current_path = f"{path}.{name}" if path else name
            if name in _API_SCHEMA_PAGINATION_FIELDS or name in _API_SCHEMA_TRANSPORT_FIELDS:
                errors.extend(
                    _technical_schema_field_refs(
                        field_schema,
                        contract_id=contract_id,
                        schema_id=schema_id,
                        bound_entity_ids=bound_entity_ids,
                        entity_fields=entity_fields,
                        path=current_path,
                    )
                )
                continue
            reference = field_schema.get("entity_field_ref") if isinstance(field_schema, dict) else None
            if reference is not None and (not isinstance(reference, str) or "." not in reference):
                errors.append(
                    f"TechnicalPlan Schema {contract_id}.{schema_id}.{current_path} 的 entity_field_ref 格式非法。"
                )
            elif isinstance(reference, str):
                reference_entity, reference_field = reference.split(".", 1)
                if reference_entity not in bound_entity_ids:
                    errors.append(
                        f"TechnicalPlan Schema {contract_id}.{schema_id}.{current_path} 引用了未绑定实体 {reference_entity}。"
                    )
                elif reference_field != "id" and reference_field not in entity_fields.get(reference_entity, set()):
                    errors.append(
                        f"TechnicalPlan Schema {contract_id}.{schema_id}.{current_path} 引用了未知字段 {reference}。"
                    )
            errors.extend(
                _technical_schema_field_refs(
                    field_schema,
                    contract_id=contract_id,
                    schema_id=schema_id,
                    bound_entity_ids=bound_entity_ids,
                    entity_fields=entity_fields,
                    path=current_path,
                )
            )
    items = schema.get("items")
    errors.extend(
        _technical_schema_field_refs(
            items,
            contract_id=contract_id,
            schema_id=schema_id,
            bound_entity_ids=bound_entity_ids,
            entity_fields=entity_fields,
            path=f"{path}[]" if path else "[]",
        )
    )
    for key in ("allOf", "anyOf", "oneOf"):
        variants = schema.get(key)
        if isinstance(variants, list):
            for variant in variants:
                errors.extend(
                    _technical_schema_field_refs(
                        variant,
                        contract_id=contract_id,
                        schema_id=schema_id,
                        bound_entity_ids=bound_entity_ids,
                        entity_fields=entity_fields,
                        path=path,
                    )
                )
    return errors


def _schema_for_contract_ref(contract: dict[str, Any], reference: Any) -> dict[str, Any] | None:
    """解析契约内的本地 Schema 引用。"""

    if not isinstance(reference, str) or not isinstance(contract.get("schemas"), dict):
        return None
    return contract["schemas"].get(reference) if isinstance(contract["schemas"].get(reference), dict) else None


def _validate_technical_pagination(
    contract: dict[str, Any],
    endpoint: dict[str, Any],
) -> list[str]:
    """校验列表接口的统一分页参数和响应结构。"""

    endpoint_id = str(endpoint.get("id") or "unknown")
    if not endpoint_id.endswith(".list"):
        return []
    errors: list[str] = []
    parameter_names = {
        str(parameter.get("name") or "")
        for parameter in _dict_items(endpoint.get("parameters"))
        if str(parameter.get("in") or "query") == "query"
    }
    for name in ("current", "pageSize"):
        if name not in parameter_names:
            errors.append(f"TechnicalPlan 列表 Endpoint {endpoint_id} 缺少分页参数 {name}。")
    response = _schema_for_contract_ref(contract, endpoint.get("response_schema_ref"))
    properties = response.get("properties") if isinstance(response, dict) else None
    required = set(response.get("required", [])) if isinstance(response, dict) and isinstance(response.get("required"), list) else set()
    if not isinstance(properties, dict):
        return errors + [f"TechnicalPlan 列表 Endpoint {endpoint_id} 的响应 Schema 必须是对象。"]
    pagination_fields = {"total", "pageSize", "current", "list"}
    if set(properties) != pagination_fields:
        extra_fields = sorted(set(properties) - pagination_fields)
        missing_fields = sorted(pagination_fields - set(properties))
        details = []
        if extra_fields:
            details.append("多余同级字段：" + "、".join(extra_fields))
        if missing_fields:
            details.append("缺少字段：" + "、".join(missing_fields))
        errors.append(
            f"TechnicalPlan 列表 Endpoint {endpoint_id} 响应同级字段必须且只能是 total、pageSize、current、list（{'；'.join(details)}）。"
        )
    if required != pagination_fields:
        errors.append(f"TechnicalPlan 列表 Endpoint {endpoint_id} 的 total、pageSize、current、list 必须全部为必填字段。")
    for name in ("total", "pageSize", "current"):
        if isinstance(properties.get(name), dict) and properties[name].get("type") != "integer":
            errors.append(f"TechnicalPlan 列表 Endpoint {endpoint_id} 的 {name} 必须是 integer。")
    if isinstance(properties.get("list"), dict) and properties["list"].get("type") != "array":
        errors.append(f"TechnicalPlan 列表 Endpoint {endpoint_id} 的 list 必须是 array。")
    return errors


def validate_technical_plan_api_contracts(
    plan: dict[str, Any],
    requirement_spec: dict[str, Any],
) -> list[str]:
    """校验 API Contract 的实体绑定、Schema 引用和分页契约。"""

    raw_entities = _dict_items(plan.get("entities"))
    known_entity_ids = {
        str(entity.get("id") or "")
        for entity in raw_entities
        if str(entity.get("id") or "").strip()
    }
    if not known_entity_ids:
        known_entity_ids = {
            str(entity.get("id") or "")
            for entity in normalize_entities(requirement_spec.get("entities"))
            if str(entity.get("id") or "").strip()
        }
    source_entities = raw_entities or normalize_entities(requirement_spec.get("entities"))
    entity_fields = {
        str(entity.get("id") or ""): {
            str(field.get("name") or "")
            for field in _dict_items(entity.get("fields"))
            if str(field.get("name") or "").strip()
        }
        for entity in source_entities
        if str(entity.get("id") or "").strip()
    }
    errors: list[str] = []
    for contract in _dict_items(plan.get("api_contracts")):
        contract_id = str(contract.get("id") or "unknown")
        allowed_keys = {
            "authentication",
            "base_path",
            "endpoints",
            "entity_ids",
            "id",
            "schemas",
        }
        required_keys = allowed_keys - {"authentication"}
        unexpected = sorted(set(contract) - allowed_keys)
        missing = sorted(required_keys - set(contract))
        if unexpected:
            errors.append(f"TechnicalPlan API Contract {contract_id} 包含非法字段：{'、'.join(unexpected)}。")
        if missing:
            errors.append(f"TechnicalPlan API Contract {contract_id} 缺少必需字段：{'、'.join(missing)}。")
        authentication = contract.get("authentication")
        if authentication is not None and (
            not isinstance(authentication, dict)
            or set(authentication) != {"required"}
            or not isinstance(authentication.get("required"), bool)
        ):
            errors.append(
                f"TechnicalPlan API Contract {contract_id} 的 authentication 只能包含 boolean required。"
            )
        raw_ids = contract.get("entity_ids")
        ids = [str(value).strip() for value in raw_ids if str(value).strip()] if isinstance(raw_ids, list) else []
        if not ids:
            errors.append(f"TechnicalPlan API Contract {contract_id} 必须声明非空 entity_ids。")
            continue
        if len(ids) != len(raw_ids) or len(ids) != len(set(ids)):
            errors.append(f"TechnicalPlan API Contract {contract_id} 的 entity_ids 必须非空且不重复。")
        unknown = sorted(set(ids) - known_entity_ids)
        if unknown:
            errors.append(f"TechnicalPlan API Contract {contract_id} 引用了未知实体：{'、'.join(unknown)}。")
        schemas = contract.get("schemas") if isinstance(contract.get("schemas"), dict) else {}
        for schema_id, schema in schemas.items():
            errors.extend(
                _technical_schema_field_refs(
                    schema,
                    contract_id=contract_id,
                    schema_id=str(schema_id),
                    bound_entity_ids=set(ids),
                    entity_fields=entity_fields,
                )
            )
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_authentication = endpoint.get("authentication")
            if endpoint_authentication is not None and (
                not isinstance(endpoint_authentication, dict)
                or set(endpoint_authentication) != {"required"}
                or not isinstance(endpoint_authentication.get("required"), bool)
            ):
                errors.append(
                    f"TechnicalPlan Endpoint {endpoint.get('id') or 'unknown'} 的 authentication 只能包含 boolean required。"
                )
            for key in ("request_schema_ref", "response_schema_ref"):
                reference = endpoint.get(key)
                if reference and _schema_for_contract_ref(contract, reference) is None:
                    errors.append(f"TechnicalPlan Endpoint {endpoint.get('id') or 'unknown'} 的 {key} 未解析到本契约 Schema。")
            errors.extend(_validate_technical_pagination(contract, endpoint))
    return errors


def create_technical_plan(
    spec: dict[str, Any],
    *,
    agent_plan: dict[str, Any] | None = None,
    datasource_type: DatasourceType | None = None,
) -> dict[str, Any]:
    """生成只包含开发新增事实的 TechnicalPlan。"""

    authentication_errors = validate_technical_plan_model_authentication(agent_plan)
    authorization_errors = validate_technical_plan_model_authorization(agent_plan)
    if authentication_errors or authorization_errors:
        raise ValueError("；".join([*authentication_errors, *authorization_errors]))
    effective_datasource_type: EnabledDatasourceType = (
        ensure_enabled_datasource_type(datasource_type)
        if datasource_type is not None
        else datasource_type_from_artifact(spec, fallback="database")
    )
    entities = _technical_entities(spec, agent_plan)
    contract_sources = [
        {
            "id": "unbound",
            "type": "database",
            "entities": entities,
        }
    ]
    api_contracts = _technical_api_contracts(
        _attach_technical_entity_field_refs(
            _merge_agent_items(
                _api_contracts(contract_sources, {**spec, "entities": entities}),
                agent_plan,
                "api_contracts",
                authoritative=True,
            ),
            entities,
        )
    )
    policy = _architecture_for_datasource_type(effective_datasource_type)
    architecture = {
        key: deepcopy(policy[key])
        for key in ("frontend", "backend", "data")
    }
    agent_architecture = _agent_section(agent_plan, "architecture")
    if isinstance(agent_architecture, dict):
        for key in architecture:
            if key in agent_architecture:
                architecture[key] = deepcopy(agent_architecture[key])
    for key in ("backend", "data"):
        architecture[key] = deepcopy(policy[key])
    if effective_datasource_type == "static":
        architecture["frontend"] = deepcopy(policy["frontend"])
    pages = _technical_plan_pages(spec, agent_plan, api_contracts)
    product_plan = (
        spec.get("confirmed_product_plan")
        if isinstance(spec.get("confirmed_product_plan"), dict)
        else {"authorizationTargets": {"pageRules": [], "operationRules": []}}
    )
    plan = {
        "artifact_type": TECHNICAL_PLAN_ARTIFACT_TYPE,
        "architecture": architecture,
        "entities": entities,
        "api_contracts": api_contracts,
        "pages": pages,
        "authorization_manifest": compile_authorization_manifest(
            spec,
            product_plan,
            api_contracts,
            pages,
        ),
    }
    repaired, _ = repair_cross_contract_schema_refs(plan)
    return repaired


def create_project_plan(
    spec: dict[str, Any],
    agent_note: str = "live main-agent project planning",
    planning_source: str = "main_agent_live",
    agent_plan: dict[str, Any] | None = None,
    authoritative_agent_plan: bool = False,
    datasource_type: DatasourceType | None = None,
) -> dict[str, Any]:
    """生成 ProjectPlan，并把页面叶子组织成带菜单层级的 frontend_pages 树。"""

    effective_datasource_type: EnabledDatasourceType = (
        ensure_enabled_datasource_type(datasource_type)
        if datasource_type is not None
        else datasource_type_from_artifact(spec, fallback="database")
    )

    route_root_path = str(
        (
            spec.get("app_info")
            if isinstance(spec.get("app_info"), dict)
            else {}
        ).get("route_root_path")
        or ""
    ).strip()
    menu_enabled = bool(
        (
            spec.get("app_info")
            if isinstance(spec.get("app_info"), dict)
            else {}
        ).get("menu_enabled")
    )
    planned_data_sources = _merge_agent_items(
            _planned_data_sources(spec, default_type=effective_datasource_type),
            agent_plan,
            "data_sources",
            authoritative=authoritative_agent_plan,
        )
    planned_data_sources = apply_authoritative_datasource_type(
        {"data_sources": planned_data_sources},
        effective_datasource_type,
    )["data_sources"]
    data_sources = _normalize_data_sources(planned_data_sources)
    api_contracts = _normalize_api_contracts(
        _merge_agent_items(
            _api_contracts(data_sources, spec),
            agent_plan,
            "api_contracts",
            authoritative=authoritative_agent_plan,
        )
    )
    data_sources = [
        {
            **source,
            "schema_refs": schema_refs_for_entities(
                api_contracts,
                entity_ids(source.get("entities")),
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
    frontend_page_leaves = _apply_menu_home_route_rule(
        frontend_page_leaves,
        menu_enabled=menu_enabled,
        route_root_path=route_root_path,
    )
    frontend_page_leaves = normalize_page_dependencies(frontend_page_leaves, api_contracts)
    frontend_pages = rebuild_frontend_page_tree(
        _agent_section(agent_plan, "frontend_pages"),
        frontend_page_leaves,
        module_names=_module_name_map(spec),
        root_route_prefix=route_root_path,
        menu_enabled=menu_enabled,
    )

    agent_architecture = _agent_section(agent_plan, "architecture")
    architecture = _architecture_for_sources(data_sources)
    if isinstance(agent_architecture, dict):
        architecture.update(agent_architecture)
    # 数据源实现边界属于项目规划阶段硬约束，模型输出遗漏或写偏时必须确定性恢复。
    architecture_policy = _architecture_for_sources(data_sources)
    for key in ("backend", "data", "backend_tech_stack", "data_contract"):
        architecture[key] = architecture_policy[key]
    source_types = {
        str(item.get("type") or "") for item in data_sources
    }
    if source_types and source_types <= {"static"}:
        architecture["frontend"] = architecture_policy["frontend"]

    plan: dict[str, Any] = {
        "version": "0.1.0",
        "status": "draft",
        "generated_at": datetime.now(UTC).isoformat(),
        "requirement_spec_version": spec["version"],
        "app": {
            "name": spec["app_info"]["name"],
            "summary": spec["app_info"]["summary"],
            **({"route_root_path": route_root_path} if route_root_path else {}),
            "menu_enabled": menu_enabled,
        },
        "requirements_overview": _requirements_overview(spec, agent_plan),
        "project_acceptance_criteria": _project_acceptance_criteria(
            spec,
            agent_plan,
        ),
        "architecture": architecture,
        "api_contracts": api_contracts,
        "frontend_pages": frontend_pages,
        "entities": _entities_from_sources(data_sources),
        "permission_model": _permission_model(
            spec,
            frontend_page_leaves,
            agent_plan,
        ),
        "engineering_design": _engineering_design(
            agent_plan,
            architecture=architecture,
        ),
        "business_flows": (
            _dict_items(_agent_section(agent_plan, "business_flows"))
            if authoritative_agent_plan
            and _dict_items(_agent_section(agent_plan, "business_flows"))
            else spec["business_flows"]
        ),
        "acceptance_criteria": (
            product_acceptance_criteria(_agent_section(agent_plan, "acceptance_criteria"))
            if authoritative_agent_plan
            and product_acceptance_criteria(_agent_section(agent_plan, "acceptance_criteria"))
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
    return normalize_project_plan(
        apply_project_plan_datasource_policy(plan, effective_datasource_type)
    )
