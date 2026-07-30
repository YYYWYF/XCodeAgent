from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.messages import AIMessageChunk

from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.project_plan import (
    apply_project_plan_feedback,
    create_project_plan,
)
from app.utils.model_output import extract_json_object


BACKEND_TECH_STACK_REQUIREMENT = (
    "Backend technology stack is fixed and non-negotiable: "
    "development language Java8; framework Springboot; database MySQL8; cache Redis. "
    "ProjectPlan.architecture.backend and architecture.data must explicitly mention this stack, "
    "and api/data planning must not choose Node.js, Python, Go, PostgreSQL, SQLite, MongoDB, "
    "or any alternative backend/database/cache stack."
)


def _planning_prompt(
    requirement_spec: dict[str, Any],
    existing_plan: dict[str, Any] | None = None,
) -> str:
    revision_context = (
        "Update the existing ProjectPlan using planning_adjustment_request from the RequirementSpec. "
        "The latest user feedback overrides conflicting older plan content. Return the complete updated "
        "plan, including full page, data source, and API lists; omitted items are treated as removed.\n"
        f"Existing ProjectPlan:\n{json.dumps(existing_plan, ensure_ascii=False)}\n\n"
        if existing_plan
        else "Create a new complete ProjectPlan.\n"
    )
    return (
        "You are the project-planning model for an app-generation workflow.\n"
        "This is a planning-only boundary. Do not call tools, do not call subagents, "
        "do not delegate tasks, and do not generate or modify code.\n"
        "Create a project-level planning document from the RequirementSpec.\n"
        f"{BACKEND_TECH_STACK_REQUIREMENT}\n"
        "If RequirementSpec.app_info.route_root_path is present and non-empty, treat it as the fixed page root route prefix. "
        "All emitted page paths and all non-empty menu unique_path values must stay under that root prefix.\n"
        "If RequirementSpec.app_info.menu_enabled is true, the application uses menus. In that case, no business page may use the bare root route "
        "or the bare route_root_path as its final page path. Even the home/dashboard page must use an extra leaf segment such as '/home', "
        "'/dashboard', or another concrete business segment under the nearest menu route or under route_root_path.\n"
        "If RequirementSpec.app_info.menu_enabled is false or absent, pages may follow the existing non-menu routing pattern.\n"
        "Route generation is strict. Always emit final absolute routes that start with '/'. "
        "Do not emit relative route fragments such as 'role', 'management/role', or './role'. "
        "Do not emit placeholder routes such as '/menu', '/group', '/temp', or '/page' unless they are real business routes.\n"
        "Menu/page routing rules:\n"
        "A. If route_root_path is '/root' and a menu has a non-empty route for management, emit that menu unique_path as '/root/management'.\n"
        "B. If a page belongs under that routed menu, emit the page path as its final full child route, for example '/root/management/role' or '/root/management/resource'. A page path must not equal an ancestor menu unique_path.\n"
        "C. If a menu is only a visual grouping node and should not own a route, emit unique_path as an empty string ''. In that case child pages must stay directly under the nearest non-empty ancestor route, for example '/root/role'.\n"
        "D. Never duplicate prefixes. If route_root_path is '/root', do not emit '/root/root/management' or '/root/root/role'.\n"
        "E. Child page paths must represent the final user-facing route, not just the leaf segment.\n"
        "F. If menu_enabled is true, the home/dashboard page path cannot be '/root' or '/'. It must be something like '/root/home' or '/root/dashboard'.\n"
        "G. Dynamic/detail pages with route parameters such as ':id' are hidden routable pages and must not by themselves cause a parent menu to be created. If a module has only one visible menu page plus dynamic/detail pages, keep the visible page as the menu leaf instead of wrapping it in a parent menu.\n"
        "H. If clicking a menu should render a page directly, model that clickable item as a page leaf. Do not create a separate menu node with the same route as that page.\n"
        "Route examples to follow strictly:\n"
        "- Valid: route_root_path='/root', menu unique_path='/root/management', page path='/root/management/role'.\n"
        "- Valid: route_root_path='/root', menu unique_path='', page path='/root/role'. The empty-route menu is only a grouping node and does not add '/management' or any other segment.\n"
        "- Valid: route_root_path='/root', one visible page path='/root/management' plus hidden detail page path='/root/management/:id' can appear as page leaves without a parent menu.\n"
        "- Valid: menu_enabled=true, route_root_path='/root', home page path='/root/home'.\n"
        "- Invalid: route_root_path='/root', menu unique_path='/management'. The root prefix is missing.\n"
        "- Invalid: route_root_path='/root', menu unique_path='/root/management', page path='role', 'management/role', or '/root/role' for a page that belongs under that routed menu.\n"
        "- Invalid: route_root_path='/root', menu unique_path='/root/management' with a direct child page path='/root/management'. Use unique_path='' or make the page path a child route like '/root/management/list'.\n"
        "- Invalid: route_root_path='/root', menu unique_path='', page path='/root/management/role' when 'management' is only the menu name and the menu itself has no route.\n"
        "- Invalid: menu_enabled=true, route_root_path='/root', home page path='/root'.\n"
        "- Invalid: menu_enabled=true, home page path='/'.\n"
        "- Invalid: route_root_path='/root', menu unique_path='/root/root/management', page path='/root/root/management/role', or placeholder menu routes like '/menu'.\n"
        "If you are unsure, prefer fewer menu route levels and emit the page's final absolute path directly, but never omit the required root prefix and never emit relative fragments.\n"
        "Return only one JSON object, without markdown fences or commentary.\n"
        "The JSON object must include these top-level keys:\n"
        "- requirements_overview: app goal, roles, modules, flows, acceptance focus\n"
        "- project_acceptance_criteria: whole-requirement acceptance criteria for project completion\n"
        "- architecture: frontend, backend, data, testing\n"
        "- api_contracts: the only source of business field definitions. Every item must use exactly "
        "{id, data_source_id, resource, base_path, authentication, schemas, endpoints}. id and "
        "data_source_id are required non-empty strings; data_source_id must equal one declared "
        "data_sources[].id. Always use the key id, never contract_id or contractId. Each contract "
        "contains compact JSON-Schema-like schemas and endpoints. Endpoints contain stable id, method, path, summary, "
        "parameters [{name, in, required, schema}], request_schema_ref, response_schema_ref, "
        "error_codes, and authentication. CRITICAL schema placement rule: every schema referenced by "
        "an endpoint's request_schema_ref or response_schema_ref MUST be defined inside the SAME "
        "contract's own schemas object, keyed by its bare schema name. NEVER reference a schema that "
        "lives in a different contract. If several endpoints across contracts need the same response "
        "shape (e.g. a shared statistics or summary payload), define a separate copy of that schema "
        "inside EACH contract that uses it, named to match its owner resource (for example a "
        "duty-records statistics response belongs to the duty-records contract, not the personnel "
        "contract). Do not reuse one contract's schemas from another contract.\n"
        "Canonical api_contracts example. Follow this exact shape and reference style; adapt names and fields "
        "to the RequirementSpec:\n"
        "{\n"
        '  "id": "inventory_api",\n'
        '  "data_source_id": "inventory_source",\n'
        '  "resource": "InventoryItem",\n'
        '  "base_path": "/api/inventory",\n'
        '  "authentication": {"required": true, "roles": ["admin", "user"]},\n'
        '  "schemas": {\n'
        '    "InventoryItem": {\n'
        '      "type": "object",\n'
        '      "properties": {"id": {"type": "string"}, "name": {"type": "string"}},\n'
        '      "required": ["id", "name"]\n'
        "    },\n"
        '    "InventoryListResponse": {\n'
        '      "type": "object",\n'
        '      "properties": {"items": {"type": "array", "items": {"$ref": "InventoryItem"}}, "total": {"type": "integer"}},\n'
        '      "required": ["items", "total"]\n'
        "    }\n"
        "  },\n"
        '  "endpoints": [\n'
        '    {"id": "inventory.list", "method": "GET", "path": "/api/inventory", "summary": "List inventory items", '
        '"parameters": [], "request_schema_ref": null, "response_schema_ref": "InventoryListResponse", '
        '"error_codes": [], "authentication": {"required": true, "roles": ["admin", "user"]}}\n'
        "  ]\n"
        "}\n"
        "Schema reference format rule: inside ProjectPlan api_contracts, schema names are bare strings. "
        "Use \"$ref\": \"InventoryItem\", request_schema_ref: \"InventoryItem\", and "
        "response_schema_ref: \"InventoryListResponse\". Do NOT use #/definitions/..., "
        "#/components/schemas/..., components, definitions, schema_definitions, or OpenAPI document wrappers.\n"
        "- frontend_pages: menu tree plus page leaves. A menu node uses exactly {name, unique_path, children}. "
        "Each child may be another menu node or a page leaf. Every page leaf must still include unique non-empty "
        "pageId, unique path, module_id, description, and references {permissions, endpoint_dependencies "
        "[{endpoint_id, usage, trigger, required_for_initial_load}], navigation_targets "
        "[{targetPageId, trigger}]}. Menu nodes must never contain pageId/path/module_id/description/references. "
        "If a menu node owns a real route, its unique_path must be the final absolute route, not a relative segment. "
        "If a menu unique_path is non-empty, every descendant page path must extend from that menu path with at least one child segment; it must not equal the menu unique_path. "
        "If the application has a root route prefix, every non-empty menu unique_path and every page path must extend from that root prefix. "
        "If a menu unique_path is empty, treat that menu as a pure grouping node and let child pages remain directly under the nearest non-empty ancestor route. "
        "Dynamic/detail pages with path parameters are hidden routable pages and do not count as visible menu leaves when deciding whether a parent menu is needed. "
        "Do not emit duplicate root permissions, endpoint_dependencies, navigation_targets, data_dependencies, "
        "or states fields\n"
        "- data_sources: data source list with entities, schema_refs, and seed strategy; never duplicate fields\n"
        "- permission_model: roles, page access, operation permissions\n"
        "- risks: planning risks and items to refine later\n\n"
        "API contracts are the canonical backend/frontend boundary. The first generated ProjectPlan must "
        "already satisfy the following non-negotiable dependency contract:\n"
        "0. If data_sources is non-empty, api_contracts must also be non-empty and every data source must "
        "be represented by at least one contract.\n"
        "1. Every page leaf inside frontend_pages has a non-empty and globally unique pageId.\n"
        "2. Every page leaf inside frontend_pages has a non-empty and globally unique path.\n"
        "2.1 Every non-empty menu node unique_path is globally unique among menu nodes.\n"
        "2.1.1 Every non-empty menu unique_path is an absolute final route string beginning with '/'.\n"
        "2.2 If a menu unique_path is non-empty, every descendant page path must start with menu unique_path + '/' and must not equal that menu unique_path.\n"
        "2.3 If route_root_path is present, every non-empty menu unique_path and every page path must equal route_root_path or start with route_root_path + '/'.\n"
        "2.4 If a menu unique_path is empty, descendant page paths must start from the nearest non-empty ancestor route or directly from route_root_path.\n"
        "2.5 If menu_enabled is true, no page path may equal '/' or route_root_path itself; every page path must contain at least one business leaf segment beyond the nearest menu route or route_root_path.\n"
        "3. Every data-backed page declares endpoint_dependencies using only endpoint_id values declared in "
        "api_contracts. Do not leave API selection to page design.\n"
        "4. Every navigation_targets[].targetPageId names another declared frontend pageId.\n"
        "5. Do not emit data_dependencies as an independent source of truth; the backend derives it from "
        "endpoint_dependencies through API contracts.\n"
        "6. Every endpoint request_schema_ref and response_schema_ref resolves to a schema defined in "
        "the SAME contract that owns the endpoint. Cross-contract schema references are forbidden; "
        "duplicate the schema into each contract that needs it instead.\n"
        "Before returning, perform this dependency audit yourself and correct the plan in the same response. "
        "A later page-design stage will copy permissions, endpoint_dependencies, and navigation_targets "
        "verbatim and is forbidden from adding dependencies.\n"
        "Pages may only reference contract endpoints and valid targetPageId values, and must not define "
        "additional fields. Define reusable "
        "project-level endpoints here rather than inventing them per page. Keep ids stable and reuse "
        "ids from RequirementSpec whenever possible. Before returning, internally audit whether the "
        "plan contains the information needed to derive API contracts, page inventory, data-source "
        "inventory, dependencies, roles, flows, and acceptance criteria. Resolve ordinary omissions "
        "with explicit assumptions and risks in this one plan; do not return questions or defer them "
        "to later confirmation rounds.\n"
        f"{revision_context}RequirementSpec:\n"
        f"{json.dumps(requirement_spec, ensure_ascii=False)}"
    )


def _invoke_live_chat_model(
    requirement_spec: dict[str, Any],
    *,
    existing_plan: dict[str, Any] | None = None,
    settings: Settings | None = None,
    on_token: Callable[[str], None] | None = None,
) -> str:
    active_settings = settings or Settings.from_env()
    model = create_chat_model(active_settings)
    if on_token is None:
        result = model.invoke(
            _planning_prompt(requirement_spec, existing_plan)
        )
        content = getattr(result, "content", "")
        return content if isinstance(content, str) else str(content)

    accumulated_text = ""
    for chunk in model.stream(_planning_prompt(requirement_spec, existing_plan)):
        if isinstance(chunk, AIMessageChunk):
            token = chunk.content
            if isinstance(token, str) and token:
                accumulated_text += token
                on_token(token)
    return accumulated_text


def plan_project_with_chat_model(
    requirement_spec: dict[str, Any],
    *,
    existing_plan: dict[str, Any] | None = None,
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Use a direct chat-model call to produce a ProjectPlan."""

    settings = Settings.from_env()
    agent_note = _invoke_live_chat_model(
        requirement_spec,
        existing_plan=existing_plan,
        settings=settings,
        on_token=on_token,
    )
    planning_source = "direct_chat_model"

    plan = create_project_plan(
        requirement_spec,
        agent_note=agent_note,
        planning_source=planning_source,
        agent_plan=extract_json_object(agent_note),
        authoritative_agent_plan=True,
    )
    return plan


def revise_project_plan_with_chat_model(
    existing_plan: dict[str, Any],
    user_feedback: str,
    *,
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    requirement_spec = {
        "version": existing_plan.get("requirement_spec_version", "0.1.0"),
        "app_info": {
            "name": existing_plan.get("app", {}).get("name", "未命名应用"),
            "summary": existing_plan.get("app", {}).get("summary", user_feedback),
            "target": existing_plan.get("requirements_overview", {}).get(
                "target",
                "生成一个可在本地运行的前后端应用工程。",
            ),
            "route_root_path": existing_plan.get("app", {}).get("route_root_path", ""),
            "menu_enabled": existing_plan.get("app", {}).get("menu_enabled", False),
        },
        "requirements_overview": existing_plan.get("requirements_overview", {}),
        "user_roles": existing_plan.get("permission_model", {}).get("roles")
        or existing_plan.get("requirements_overview", {}).get("roles", []),
        "feature_modules": existing_plan.get("requirements_overview", {}).get(
            "modules",
            [],
        ),
        "pages": existing_plan.get("frontend_pages", []),
        "data_sources": existing_plan.get("data_sources", []),
        "business_flows": existing_plan.get("business_flows", []),
        "acceptance_criteria": existing_plan.get("acceptance_criteria", []),
        "planning_adjustment_request": user_feedback,
    }
    revised = plan_project_with_chat_model(
        requirement_spec,
        existing_plan=existing_plan,
        on_token=on_token,
    )
    revised = apply_project_plan_feedback(revised, user_feedback)
    revised["planning_source"] = "direct_chat_model_revision"
    revised["confirmation_status"] = "pending_user_confirmation"
    return revised
