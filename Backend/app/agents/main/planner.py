from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Callable

from langchain_core.messages import AIMessageChunk

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.frontend_page_tree import flatten_frontend_pages
from app.services.data_source_policy import DatasourceType
from app.services.project_plan import (
    apply_project_plan_feedback,
    create_project_plan,
    create_technical_plan,
)
from app.utils.model_output import extract_json_object


BACKEND_TECH_STACK_REQUIREMENT = (
    "Backend technology stack is fixed and non-negotiable: "
    "development language Java8; framework Springboot; database MySQL8; cache Redis. "
    "ProjectPlan.architecture.backend and architecture.data must explicitly mention this stack, "
    "and api/data planning must not choose Node.js, Python, Go, PostgreSQL, SQLite, MongoDB, "
    "or any alternative backend/database/cache stack."
)
DATASOURCE_PENDING_REQUIREMENT = (
    "The application has no app-level data source type. Do NOT emit data_source on any entity and "
    "do NOT emit a data_sources section in ProjectPlan; every entity's data source is decided and "
    "confirmed later during the entity-design stage. API contracts still bind entity_ids and keep "
    "schemas, operations, endpoint ids, methods and paths. Keep the default Java8 + Springboot + "
    "MySQL8 + Redis architecture boundary in ProjectPlan.architecture."
)


def _technical_planning_prompt(
    requirement_spec: dict[str, Any],
    existing_plan: dict[str, Any] | None,
) -> str:
    """构造字段边界明确且按上下文拆分的 TechnicalPlan 提示词。"""

    product_plan = (
        requirement_spec.get("confirmed_product_plan")
        if isinstance(requirement_spec.get("confirmed_product_plan"), dict)
        else {}
    )
    pages = [item for item in product_plan.get("pages", []) if isinstance(item, dict)]
    entity = {
        "id": "Order",
        "name": "Order",
        "description": "Order business entity",
        "fields": [{"name": "order_number"}],
    }
    entity_id = str(entity.get("id") or "Order")
    entity_fields = [item for item in entity.get("fields", []) if isinstance(item, dict)]
    field_name = str((entity_fields[0] if entity_fields else {}).get("name") or "order_number")
    page_id = str((pages[0] if pages else {}).get("pageId") or "order_list_page")
    contract_id = f"{entity_id.lower()}_api"
    list_schema_id = f"{entity_id}ListOutput"
    item_schema_id = f"{entity_id}ListItem"
    response_example = {
        "architecture": {
            "frontend": "A React single-page administration client communicates with the service through REST JSON APIs.",
            "backend": "A Java8 and Springboot service exposes REST APIs organized by business capability.",
            "data": "MySQL8 provides persistence and Redis provides caching for hot data.",
        },
        "entities": [
            {
                "id": entity_id,
                "name": str(entity.get("name") or entity_id),
                "description": str(entity.get("description") or "Business entity"),
                "fields": [
                    {
                        "name": field_name,
                        "label": "Business field",
                        "description": "The business meaning of this field.",
                        "type": "text",
                        "required": True,
                    }
                ],
            }
        ],
        "api_contracts": [
            {
                "id": contract_id,
                "entity_ids": [entity_id],
                "base_path": f"/api/{entity_id.lower()}",
                "authentication": {"required": True},
                "schemas": {
                    item_schema_id: {
                        "type": "object",
                        "properties": {
                            "displayValue": {
                                "type": "string",
                                "description": "A value formatted for list display.",
                                "entity_field_ref": f"{entity_id}.{field_name}",
                            }
                        },
                        "required": ["displayValue"],
                    },
                    list_schema_id: {
                        "type": "object",
                        "properties": {
                            "total": {"type": "integer"},
                            "pageSize": {"type": "integer"},
                            "current": {"type": "integer"},
                            "list": {"type": "array", "items": {"$ref": item_schema_id}},
                        },
                        "required": ["total", "pageSize", "current", "list"],
                    },
                },
                "endpoints": [
                    {
                        "id": f"{contract_id}.list",
                        "method": "GET",
                        "path": f"/api/{entity_id.lower()}",
                        "summary": "Query a paginated list.",
                        "parameters": [
                            {"name": "current", "in": "query", "required": False, "schema": {"type": "integer", "default": 1}},
                            {"name": "pageSize", "in": "query", "required": False, "schema": {"type": "integer", "default": 20}},
                        ],
                        "request_schema_ref": None,
                        "response_schema_ref": list_schema_id,
                        "error_codes": ["UNAUTHORIZED"],
                        "authentication": {"required": True},
                    },
                ],
            }
        ],
        "pages": [
            {
                "pageId": page_id,
                "references": {
                    "endpoint_dependencies": [
                        {
                            "endpoint_id": f"{contract_id}.list",
                            "usage": "page_load",
                            "trigger": "Page entry or filter submission",
                            "required_for_initial_load": True,
                        }
                    ],
                    "action_implementations": [],
                },
            }
        ],
    }
    product_goal_context = {
        "app": {
            key: product_plan.get("app", {}).get(key)
            for key in ("name", "summary", "route_root_path", "menu_enabled")
            if isinstance(product_plan.get("app"), dict)
            and product_plan["app"].get(key) is not None
        },
        "product_acceptance_criteria": product_plan.get("product_acceptance_criteria", []),
    }
    authorization_context = {
        "enabled": (requirement_spec.get("authorization_requirements") or {}).get("enabled") is True,
        "authorizationTargets": product_plan.get("authorizationTargets", {}),
    }
    flow_context = {"business_flows": product_plan.get("business_flows", [])}
    page_context = {
        "pages": [
            {
                key: page.get(key)
                for key in ("pageId", "goal", "information_items")
                if page.get(key) is not None
            }
            for page in pages
        ]
    }
    action_context = {
        "page_actions": [
            {
                "pageId": page.get("pageId"),
                "actions": page.get("actions", []),
            }
            for page in pages
            if page.get("pageId")
        ]
    }
    revision_context = (
        "Revise the existing TechnicalPlan according to planning_adjustment_request and return the complete five-part object.\n"
        f"Existing TechnicalPlan:\n{json.dumps(existing_plan, ensure_ascii=False)}\n\n"
        if existing_plan
        else "Create a new TechnicalPlan.\n"
    )
    return (
        "You are the technical-planning model in an application-generation workflow. Return exactly one JSON object.\n"
        "The object has exactly four sections: architecture, entities, api_contracts, and pages.\n\n"
        "Field definitions:\n"
        "1. architecture is a technical summary. frontend describes the client form and communication style; "
        "backend describes the Java8/Springboot service boundary; data describes MySQL8 persistence and Redis caching.\n"
        "2. entities is the authoritative business-entity field specification. Generate entities "
        "from the business model implied by pages, feature modules, and business flows. Each entity "
        "has a stable id, name, description, and fields. Each field contains name, label, description, "
        "type, and required; enum fields also contain enum_values. type is one of text, long_text, "
        "number, decimal, date, datetime, enum, or boolean. Field names use snake_case.\n"
        "3. api_contracts is the interface contract collection. Each contract contains id, entity_ids, base_path, "
        "authentication, schemas, and endpoints. entity_ids identifies every related business entity. A business "
        "Schema properties may use interface-specific names. Add entity_field_ref=<EntityId>.<field_name> when a "
        "property is directly sourced from an entity field; computed, aggregated, and transport properties may omit "
        "the mapping. Structural properties organize the response. A paginated list response object has exactly four "
        "same-level properties: total, pageSize, current, and list. It has no other sibling properties. Its query "
        "parameters use current and pageSize, while fields inside list items follow the item Schema. "
        "Schema references resolve to names in the same contract. Each Endpoint contains id, method, path, summary, "
        "parameters, request_schema_ref, response_schema_ref, error_codes, and authentication. Decide whether a request "
        "body exists from the operation semantics, never from the HTTP method alone. If the operation consumes body "
        "fields, request_schema_ref is a non-empty bare schema name and that schema is defined in the same contract. "
        "If path/query parameters plus authentication context fully describe a command, request_schema_ref is null even "
        "for POST, PUT, or PATCH. Never invent an empty request object merely to satisfy a method convention. Before "
        "returning, verify every non-null request_schema_ref and response_schema_ref resolves inside its own contract.\n"
        "Request-body semantics examples (illustrative Endpoint fragments, not extra required endpoints):\n"
        '- command-with-body: {"id":"photo_api.rename","method":"PATCH","path":"/api/photos/{photoId}",'
        '"request_schema_ref":"PhotoRenameInput","response_schema_ref":"PhotoOutput"}; PhotoRenameInput must be '
        "defined in photo_api.schemas because the operation consumes a new name.\n"
        '- command-without-body: {"id":"photo_api.like","method":"POST","path":"/api/photos/{photoId}/like",'
        '"request_schema_ref":null,"response_schema_ref":"PhotoActionOutput"}; the path plus authenticated user '
        "fully describes the command.\n"
        "4. pages contains the technical references from each ProductPlan page to selected endpoints. Each item has "
        "pageId and references. references contains endpoint_dependencies and action_implementations. Endpoint "
        "dependencies contain endpoint_id, usage, trigger, and required_for_initial_load. A direct business action "
        "uses {actionId, endpointId}; a business sequence uses {actionId, stepBindings:[{stepId, endpointId}]}. "
        "Every selected endpointId exists in api_contracts and also appears in that page's endpoint_dependencies. "
        "The page set covers every upstream ProductPlan pageId.\n"
        "Do not emit authorization_manifest, resourceKey, roles, permission bindings, dataRules, policyKey, data-policy bindings, SQL, or executable authorization rules. The platform deterministically compiles all V1 page/action/system resources and Endpoint ANY-OF bindings after your output passes validation.\n\n"
        "Complete result example:\n"
        f"{json.dumps(response_example, ensure_ascii=False, indent=2)}\n\n"
        "Dynamic context sections:\n"
        "- Entity generation boundary: derive business entities exclusively from the confirmed ProductPlan "
        "pages, information items, actions, and business flows. RequirementSpec entities are not provided "
        "to or consumed by this stage.\n\n"
        "- Product goal context: application purpose and product-level acceptance outcomes. Use it to shape the architecture and endpoint scope.\n"
        f"{json.dumps(product_goal_context, ensure_ascii=False)}\n\n"
        "- Authorization context: confirmed ProductPlan target identities. Do not generate any authorization field; the system compiles all resources and bindings.\n"
        f"{json.dumps(authorization_context, ensure_ascii=False)}\n\n"
        "- Business-flow context: confirmed business flows. Use it to preserve cross-page and multi-step behavior.\n"
        f"{json.dumps(flow_context, ensure_ascii=False)}\n\n"
        "- Page context: page goals and information items. Use it to determine read models and page dependencies.\n"
        f"{json.dumps(page_context, ensure_ascii=False)}\n\n"
        "- Business-action context: page-scoped ProductPlan actions. Use it to select endpoint implementations only for business actions and business steps.\n"
        f"{json.dumps(action_context, ensure_ascii=False)}\n\n"
        f"{revision_context}"
        f"planning_adjustment_request:\n{str(requirement_spec.get('planning_adjustment_request') or '').strip()}\n"
    )


def _planning_prompt(
    requirement_spec: dict[str, Any],
    existing_plan: dict[str, Any] | None = None,
    datasource_type: DatasourceType | None = None,
) -> str:
    """构造项目规划提示；数据源不属于应用级，实体数据源由实体设计阶段决定。"""

    if isinstance(requirement_spec.get("confirmed_product_plan"), dict):
        return _technical_planning_prompt(
            requirement_spec,
            existing_plan,
        )
    datasource_requirement = (
        f"{DATASOURCE_PENDING_REQUIREMENT}\n{BACKEND_TECH_STACK_REQUIREMENT}"
    )
    revision_context = (
        "Update the existing ProjectPlan using planning_adjustment_request from the RequirementSpec. "
        "The latest user feedback overrides conflicting older plan content. Return the complete updated "
        "plan, including full page, data source, and API lists; omitted items are treated as removed.\n"
        f"Existing ProjectPlan:\n{json.dumps(existing_plan, ensure_ascii=False)}\n\n"
        if existing_plan
        else "Create a new complete ProjectPlan.\n"
    )
    return (
        "You are the technical-planning model for an app-generation workflow.\n"
        "This is a planning-only boundary. Do not call tools, do not call subagents, "
        "do not delegate tasks, and do not generate or modify code.\n"
        "Create a developer-facing TechnicalPlan from the confirmed RequirementSpec. When the input "
        "contains confirmed_product_plan and confirmed_ui_design_manifest, they are immutable upstream "
        "product decisions. Do not redesign layouts, components, page goals, product actions, or visual "
        "states. Design only architecture, API contracts, schemas, data sources, permissions, and the "
        "technical references needed to implement the confirmed UI.\n"
        f"{datasource_requirement}\n"
        "Never assign, infer, or persist a data source type on entities or as a top-level "
        "data_sources section; entities stay source-free until the entity-design stage.\n"
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
        "- project_acceptance_criteria: user-visible product outcomes for the generated application only; "
        "never include XCodeAgent workflow stages, preview availability, code generation, build/compile/"
        "lint/typecheck status, automated or integration tests, quality gates, or conditions for entering "
        "user acceptance\n"
        "- architecture: frontend, backend, data, testing\n"
        "- api_contracts: the only source of business field definitions. Every item must use exactly "
        "{id, entity_ids, resource, base_path, authentication, schemas, endpoints}. id and "
        "entity_ids are required; entity_ids is a non-empty array of RequirementSpec entity ids "
        "and is the only contract binding (one contract may involve multiple entities, for "
        "example an order contract references the order, customer and product entities). Never "
        "emit data_source_id: contracts bind entities only; later stages resolve each entity's "
        "confirmed EntityDesign when data-source context is required. Always use the key id, never "
        "contract_id or contractId. Each contract "
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
        '  "entity_ids": ["Inventory"],\n'
        '  "resource": "InventoryItem",\n'
        '  "base_path": "/api/inventory",\n'
        '  "authentication": {"required": true},\n'
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
        '"error_codes": [], "authentication": {"required": true}}\n'
        "  ]\n"
        "}\n"
        "Schema reference format rule: inside ProjectPlan api_contracts, schema names are bare strings. "
        "Use \"$ref\": \"InventoryItem\", request_schema_ref: \"InventoryItem\", and "
        "response_schema_ref: \"InventoryListResponse\". Do NOT use #/definitions/..., "
        "#/components/schemas/..., components, definitions, schema_definitions, or OpenAPI document wrappers.\n"
        "- frontend_pages: menu tree plus page leaves. CRITICAL: the page leaf set (every pageId) MUST come from "
        "RequirementSpec.pages and match them one-to-one. You may NOT invent, merge, rename, or omit any page. "
        "Your only job is to wrap them with a menu tree and attach references {permissions, endpoint_dependencies, "
        "navigation_targets, action_implementations}. Never change an existing pageId, name, module_id, or path. "
        "A menu node uses exactly {name, unique_path, children}. "
        "Each child may be another menu node or a page leaf. Every page leaf must still include unique non-empty "
        "pageId, unique path, module_id, description, and references {permissions, endpoint_dependencies "
        "[{endpoint_id, usage, trigger, required_for_initial_load}], navigation_targets "
        "[{targetPageId, trigger}], action_implementations}. Menu nodes must never contain pageId/path/module_id/description/references. "
        "ProductPlan already owns whether an action is business, navigation, interface, external, or sequence; "
        "UiManifest already owns control mappings and local UI effects. Do not classify or restate those decisions. "
        "action_implementations contains ONLY technical endpoint choices needed by product behavior.type=business. "
        "For a direct business action use {actionId, endpointId}. For a sequence containing business steps use "
        "{actionId, stepBindings:[{stepId,endpointId}]}, covering each and only each business stepId. "
        "Do not emit action_implementations for navigation, interface, or external actions/steps. Never emit "
        "bindingType, targetPageId, localEffect, externalTarget, UI behavior, button behavior, or visual feedback "
        "inside TechnicalPlan. Every selected endpointId must also appear in that page's endpoint_dependencies. "
        "If a menu node owns a real route, its unique_path must be the final absolute route, not a relative segment. "
        "If a menu unique_path is non-empty, every descendant page path must extend from that menu path with at least one child segment; it must not equal the menu unique_path. "
        "If the application has a root route prefix, every non-empty menu unique_path and every page path must extend from that root prefix. "
        "If a menu unique_path is empty, treat that menu as a pure grouping node and let child pages remain directly under the nearest non-empty ancestor route. "
        "Dynamic/detail pages with path parameters are hidden routable pages and do not count as visible menu leaves when deciding whether a parent menu is needed. "
        "Do not emit duplicate root permissions, endpoint_dependencies, navigation_targets, action_implementations, data_dependencies, "
        "or states fields\n"
        "- entities: omit this field. Entities belong to the technical planning stage; do not "
        "generate or copy entities in the ProjectPlan. Do not emit a top-level data_sources field.\n"
        "- permission_model: roles, page access, operation permissions\n"
        "- engineering_design: developer-only decisions using exactly these arrays: module_boundaries and "
        "data_models. Use concrete technical objects or concise engineering statements to name module ownership "
        "and model entities, relations, fields, constraints, and indexes. Do not put product behavior, UI layout, "
        "navigation, dialogs, tabs, button semantics, or stakeholder-facing acceptance prose here.\n"
        "- risks: planning risks and items to refine later\n\n"
        "API contracts are the canonical backend/frontend boundary. The first generated ProjectPlan must "
        "derive each contract from pages, "
        "page operations, feature modules, business flows and acceptance criteria. Do not emit generic "
        "full CRUD for every data source; include only operations required by the business plan.\n"
        "already satisfy the following non-negotiable dependency contract:\n"
        "0. If api_contracts is non-empty, every contract must bind to pages and feature modules.\n"
        "1. Every page leaf inside frontend_pages has a non-empty and globally unique pageId.\n"
        "2. Every page leaf inside frontend_pages has a non-empty and globally unique path.\n"
        "2.1 Every non-empty menu node unique_path is globally unique among menu nodes.\n"
        "2.1.1 Every non-empty menu unique_path is an absolute final route string beginning with '/'.\n"
        "2.2 If a menu unique_path is non-empty, every descendant page path must start with menu unique_path + '/' and must not equal that menu unique_path.\n"
        "2.3 If route_root_path is present, every non-empty menu unique_path and every page path must equal route_root_path or start with route_root_path + '/'.\n"
        "2.4 If a menu unique_path is empty, descendant page paths must start from the nearest non-empty ancestor route or directly from route_root_path.\n"
        "2.5 If menu_enabled is true, no page path may equal '/' or route_root_path itself; every page path must contain at least one business leaf segment beyond the nearest menu route or route_root_path.\n"
        "3. Every data-backed page declares endpoint_dependencies using only endpoint_id values declared in "
        "api_contracts. Do not leave API selection to a later stage.\n"
        "3.1 Every ProductPlan business action and every business step inside a sequence has exactly one "
        "endpoint implementation. ProductPlan navigation/external decisions and UiManifest interface effects "
        "must not be repeated in TechnicalPlan.\n"
        "4. Every navigation_targets[].targetPageId names another declared frontend pageId.\n"
        "5. Do not emit data_dependencies as an independent source of truth; the backend derives it from "
        "endpoint_dependencies through API contracts.\n"
        "6. Every endpoint request_schema_ref and response_schema_ref resolves to a schema defined in "
        "the SAME contract that owns the endpoint. Cross-contract schema references are forbidden; "
        "duplicate the schema into each contract that needs it instead.\n"
        "Before returning, perform this dependency audit yourself and correct the plan in the same response. "
        "The deterministic PageImplementationContract compiler will copy permissions, endpoint_dependencies, "
        "navigation_targets, confirmed product actions, and UI manifest references verbatim. There is no later "
        "page-detail design stage.\n"
        "Pages may only reference contract endpoints and valid targetPageId values, and must not define "
        "additional fields. Define reusable "
        "project-level endpoints here rather than inventing them per page. Keep ids stable and reuse "
        "ids from RequirementSpec whenever possible. Before returning, internally audit whether the "
        "plan contains the information needed to derive API contracts, page inventory, entity "
        "bindings, dependencies, roles, flows, and acceptance criteria. Resolve ordinary omissions "
        "with explicit assumptions and risks in this one plan; do not return questions or defer them "
        "to later confirmation rounds.\n"
        f"{revision_context}RequirementSpec:\n"
        f"{json.dumps(requirement_spec, ensure_ascii=False)}"
    )


def _invoke_live_chat_model(
    requirement_spec: dict[str, Any],
    *,
    existing_plan: dict[str, Any] | None = None,
    datasource_type: DatasourceType | None = None,
    settings: Settings | None = None,
    on_token: Callable[[str], None] | None = None,
) -> str:
    """调用项目规划模型，并透传数据源实现边界。

    GLM-5.2 默认开启深度思考，thinking 与正文共享 max_tokens。ProjectPlan JSON
    体量大，thinking 会挤占输出预算，常在写完前被截断，流式投到前端的原始 JSON
    也随之截断、解析校验失败。与产品/UI 设计生成一致，关闭 thinking 释放输出预算。
    """

    active_settings = settings or Settings.from_env()
    return _invoke_prompt_with_chat_model(
        _planning_prompt(requirement_spec, existing_plan, datasource_type),
        settings=active_settings,
        on_token=on_token,
    )


def _invoke_prompt_with_chat_model(
    prompt: str,
    *,
    settings: Settings,
    on_token: Callable[[str], None] | None = None,
) -> str:
    """调用无工具聊天模型执行给定规划提示，并按需转发流式文本。"""

    model = create_chat_model(
        settings,
        extra_model_kwargs={"thinking": {"type": "disabled"}},
    )
    if on_token is None:
        result = model.invoke(prompt)
        content = getattr(result, "content", "")
        return _coerce_content_text(content) or ""

    accumulated_text = ""
    for chunk in model.stream(prompt):
        if isinstance(chunk, AIMessageChunk):
            token = chunk.content
            if isinstance(token, str) and token:
                accumulated_text += token
                on_token(token)
    return accumulated_text


def _technical_contract_ids_for_errors(
    existing_plan: dict[str, Any],
    validation_errors: list[str],
) -> list[str]:
    """依据 Contract、Endpoint 和 Schema 标识定位需要修复的 API Contract。"""

    error_text = "\n".join(str(error) for error in validation_errors)
    target_ids: list[str] = []
    for contract in existing_plan.get("api_contracts", []):
        if not isinstance(contract, dict):
            continue
        contract_id = str(contract.get("id") or "").strip()
        if not contract_id:
            continue
        endpoint_ids = [
            str(endpoint.get("id") or "").strip()
            for endpoint in contract.get("endpoints", [])
            if isinstance(endpoint, dict)
        ]
        markers = [contract_id, *[value for value in endpoint_ids if value]]
        if any(marker in error_text for marker in markers):
            target_ids.append(contract_id)
    return target_ids


def technical_plan_contract_repair_applicable(
    existing_plan: dict[str, Any],
    validation_errors: list[str],
    contract_validation_errors: list[str],
) -> bool:
    """仅在全部错误均来自 Contract 定义校验时允许定向修复。"""

    contract_error_set = {
        str(error).strip()
        for error in contract_validation_errors
        if str(error).strip()
    }
    return (
        bool(validation_errors)
        and all(str(error).strip() in contract_error_set for error in validation_errors)
        and bool(_technical_contract_ids_for_errors(existing_plan, validation_errors))
    )


def _technical_page_endpoint_ids(page: dict[str, Any]) -> set[str]:
    """汇总页面依赖和业务动作实现中的 Endpoint，供 Contract 修复定位关联动作。"""

    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    endpoint_ids = {
        str(dependency.get("endpoint_id") or "")
        for dependency in references.get("endpoint_dependencies", [])
        if isinstance(dependency, dict) and str(dependency.get("endpoint_id") or "").strip()
    }
    for implementation in references.get("action_implementations", []):
        if not isinstance(implementation, dict):
            continue
        endpoint_id = str(implementation.get("endpointId") or "").strip()
        if endpoint_id:
            endpoint_ids.add(endpoint_id)
        endpoint_ids.update(
            str(binding.get("endpointId") or "").strip()
            for binding in implementation.get("stepBindings", [])
            if isinstance(binding, dict) and str(binding.get("endpointId") or "").strip()
        )
    return endpoint_ids


def _technical_contract_repair_prompt(
    requirement_spec: dict[str, Any],
    existing_plan: dict[str, Any],
    validation_errors: list[str],
    contract_ids: list[str],
) -> str:
    """只投射失败 Contract 及其关联实体、页面动作，构造定向修复提示词。"""

    target_id_set = set(contract_ids)
    target_contracts = [
        contract
        for contract in existing_plan.get("api_contracts", [])
        if isinstance(contract, dict) and contract.get("id") in target_id_set
    ]
    bound_entity_ids = {
        str(entity_id)
        for contract in target_contracts
        for entity_id in contract.get("entity_ids", [])
        if str(entity_id).strip()
    }
    entity_context = [
        entity
        for entity in existing_plan.get("entities", [])
        if isinstance(entity, dict) and str(entity.get("id") or "") in bound_entity_ids
    ]
    target_endpoint_ids = {
        str(endpoint.get("id") or "")
        for contract in target_contracts
        for endpoint in contract.get("endpoints", [])
        if isinstance(endpoint, dict) and str(endpoint.get("id") or "").strip()
    }
    related_page_ids = {
        str(page.get("pageId") or "")
        for page in existing_plan.get("pages", [])
        if isinstance(page, dict)
        and bool(_technical_page_endpoint_ids(page) & target_endpoint_ids)
    }
    product_plan = requirement_spec.get("confirmed_product_plan")
    product_actions = [
        {"pageId": page.get("pageId"), "actions": page.get("actions", [])}
        for page in (product_plan or {}).get("pages", [])
        if isinstance(page, dict) and str(page.get("pageId") or "") in related_page_ids
    ]
    return (
        "You repair API Contracts inside an existing TechnicalPlan. Return exactly one JSON object with the sole "
        "top-level key api_contracts. Return complete replacement objects for exactly the requested contract ids; "
        "do not return architecture, entities, pages, markdown, or commentary. Preserve stable contract ids, endpoint "
        "ids, paths, and unrelated valid semantics. Resolve every schema reference inside the same contract. Decide "
        "whether a request body exists from operation semantics, not HTTP method: bodyless commands may use null "
        "request_schema_ref; operations that consume body fields must define and reference a real request schema. "
        "Never add an empty request schema only to silence validation.\n\n"
        f"Requested contract ids:\n{json.dumps(contract_ids, ensure_ascii=False)}\n\n"
        f"Validation errors:\n{json.dumps(validation_errors[:12], ensure_ascii=False)}\n\n"
        f"Contracts to repair:\n{json.dumps(target_contracts, ensure_ascii=False)}\n\n"
        f"Bound entities:\n{json.dumps(entity_context, ensure_ascii=False)}\n\n"
        f"Related confirmed product actions:\n{json.dumps(product_actions, ensure_ascii=False)}\n"
    )


def repair_technical_plan_api_contracts_with_chat_model(
    requirement_spec: dict[str, Any],
    existing_plan: dict[str, Any],
    validation_errors: list[str],
    *,
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """让模型只替换报错 API Contract，并确定性合并回完整 TechnicalPlan。"""

    contract_ids = _technical_contract_ids_for_errors(existing_plan, validation_errors)
    if not contract_ids:
        raise ValueError("TechnicalPlan 校验错误无法定位到具体 API Contract。")
    settings = Settings.from_env()
    response_text = _invoke_prompt_with_chat_model(
        _technical_contract_repair_prompt(
            requirement_spec,
            existing_plan,
            validation_errors,
            contract_ids,
        ),
        settings=settings,
        on_token=on_token,
    )
    response = extract_json_object(response_text)
    repaired_contracts = response.get("api_contracts")
    if not isinstance(repaired_contracts, list):
        raise ValueError("TechnicalPlan Contract 修复结果缺少 api_contracts 数组。")
    repaired_by_id = {
        str(contract.get("id") or ""): contract
        for contract in repaired_contracts
        if isinstance(contract, dict) and str(contract.get("id") or "").strip()
    }
    if set(repaired_by_id) != set(contract_ids):
        raise ValueError("TechnicalPlan Contract 修复结果必须完整且只能包含指定 Contract。")
    merged_contracts = [
        deepcopy(repaired_by_id.get(str(contract.get("id") or ""), contract))
        for contract in existing_plan.get("api_contracts", [])
        if isinstance(contract, dict)
    ]
    return create_technical_plan(
        requirement_spec,
        agent_plan={
            "architecture": deepcopy(existing_plan.get("architecture", {})),
            "entities": deepcopy(existing_plan.get("entities", [])),
            "api_contracts": merged_contracts,
            "pages": deepcopy(existing_plan.get("pages", [])),
        },
    )


def plan_project_with_chat_model(
    requirement_spec: dict[str, Any],
    *,
    existing_plan: dict[str, Any] | None = None,
    datasource_type: DatasourceType | None = None,
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """直接调用聊天模型生成 ProjectPlan 或创建流程的 TechnicalPlan。"""

    settings = Settings.from_env()
    agent_note = _invoke_live_chat_model(
        requirement_spec,
        existing_plan=existing_plan,
        datasource_type=datasource_type,
        settings=settings,
        on_token=on_token,
    )
    planning_source = "direct_chat_model"

    agent_plan = extract_json_object(agent_note)
    if isinstance(requirement_spec.get("confirmed_product_plan"), dict):
        return create_technical_plan(
            requirement_spec,
            agent_plan=agent_plan,
            datasource_type=datasource_type,
        )
    plan = create_project_plan(
        requirement_spec,
        agent_note=agent_note,
        planning_source=planning_source,
        agent_plan=agent_plan,
        authoritative_agent_plan=True,
        datasource_type=datasource_type,
    )
    # 保留技术规划模型来源，便于会话恢复与产物审计时识别生成边界。
    plan["planned_by"] = {
        "agent": "chat-model",
        "mode": "direct",
        "model": settings.model_name,
        "source": planning_source,
    }
    return plan


def _plan_pages_for_repair(existing_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """从已有计划中提取扁平页面列表，供修复路径当作 requirement_spec.pages 使用。

    TechnicalPlan 的当前页面事实已经是扁平 pages；旧 ProjectPlan 才需要从
    frontend_pages 菜单树中提取叶子，避免把菜单节点误当成业务页面。
    """
    if existing_plan.get("artifact_type") == "technical-plan":
        return [page for page in existing_plan.get("pages", []) if isinstance(page, dict)]
    pages = existing_plan.get("frontend_pages", [])
    flat = flatten_frontend_pages(pages)
    if flat:
        return flat
    return pages


def revise_project_plan_with_chat_model(
    existing_plan: dict[str, Any],
    user_feedback: str,
    *,
    datasource_type: DatasourceType | None = None,
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """按最新反馈修订 ProjectPlan，并保持实体数据源由实体设计负责。"""
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
        "pages": _plan_pages_for_repair(existing_plan),
        "entities": existing_plan.get("entities", []),
        "business_flows": existing_plan.get("business_flows", []),
        "acceptance_criteria": existing_plan.get("acceptance_criteria", []),
        "planning_adjustment_request": user_feedback,
    }
    revised = plan_project_with_chat_model(
        requirement_spec,
        existing_plan=existing_plan,
        datasource_type=datasource_type,
        on_token=on_token,
    )
    revised = apply_project_plan_feedback(
        revised,
        user_feedback,
    )
    revised["planning_source"] = "direct_chat_model_revision"
    revised["confirmation_status"] = "pending_user_confirmation"
    return revised
