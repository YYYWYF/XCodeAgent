from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from typing import Any

from app.services.api_contracts import (
    contract_endpoints_for_dependencies,
    normalize_page_api_dependencies,
    normalize_response_bindings,
)
from app.services.page_dependencies import page_design_references


def detail_design_targets(project_plan: dict[str, Any]) -> list[dict[str, Any]]:
    page_targets = [
        {
            "id": page.get("id"),
            "type": "page",
            "label": f"页面：{page.get('name') or page.get('id') or '未命名页面'}",
            "name": page.get("name") or page.get("id") or "未命名页面",
            "description": (
                f"{page.get('path') or '/'}，"
                f"{page.get('description') or page.get('name') or '待补充页面目标'}"
            ),
        }
        for page in project_plan.get("frontend_pages", [])
        if isinstance(page, dict) and page.get("id")
    ]
    data_source_targets = [
        {
            "id": source.get("id"),
            "type": "data_source",
            "label": f"数据源：{source.get('name') or source.get('id') or '未命名数据源'}",
            "name": source.get("name") or source.get("id") or "未命名数据源",
            "description": f"实体 {source.get('entities', [])}，类型 {source.get('type', '')}",
        }
        for source in project_plan.get("data_sources", [])
        if isinstance(source, dict) and source.get("id")
    ]
    return page_targets + data_source_targets


def resolve_detail_design_target(
    project_plan: dict[str, Any],
    request: str,
    selected_page_id: str | None = None,
    selected_data_source_id: str | None = None,
) -> dict[str, Any] | None:
    targets = detail_design_targets(project_plan)
    for target in targets:
        if target["type"] == "page" and target["id"] == selected_page_id:
            return target
        if target["type"] == "data_source" and target["id"] == selected_data_source_id:
            return target

    request_text = request.strip()
    if not request_text:
        return None

    for target in targets:
        candidates = [
            target["id"],
            target["label"],
            target["name"],
        ]
        if any(candidate and candidate in request_text for candidate in candidates):
            return target

    return None


def _find_by_id(items: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
    for item in items:
        if item.get("id") == item_id:
            return item
    raise ValueError(f"Unknown item id: {item_id}")


def _text_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text_item(item) for item in value if _text_item(item)]


def _text_item(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "label", "title", "description", "id"):
            if value.get(key):
                return str(value[key])
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _normalize_basic_layout(value: Any) -> dict[str, Any]:
    layout = value if isinstance(value, dict) else {}
    return {
        **layout,
        "structure": _text_items(layout.get("structure")),
        "states": _text_items(layout.get("states")),
    }


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _api_endpoints_for_contracts(
    api_contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    for contract in api_contracts:
        contract_id = str(contract.get("id") or "")
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_id = str(endpoint.get("id") or "")
            if not endpoint_id:
                continue
            endpoints.append(
                {
                    "api_contract_id": contract_id,
                    "endpoint_id": endpoint_id,
                    "method": str(endpoint.get("method") or "GET").upper(),
                    "path": str(endpoint.get("path") or ""),
                    "summary": str(endpoint.get("summary") or endpoint_id),
                    "request_schema_ref": endpoint.get("request_schema_ref") or "",
                    "response_schema_ref": endpoint.get("response_schema_ref") or "",
                }
            )
    return endpoints


def _selected_api_dependencies(
    api_endpoints: list[dict[str, Any]],
    selected_value: Any,
    *,
    page_name: str,
    page_path: str,
) -> list[dict[str, Any]]:
    available_by_endpoint = {
        str(item.get("endpoint_id")): item
        for item in api_endpoints
        if item.get("endpoint_id")
    }
    selected: list[dict[str, Any]] = []
    for item in _dict_items(selected_value):
        endpoint_id = str(item.get("endpoint_id") or "")
        available = available_by_endpoint.get(endpoint_id)
        if not available:
            continue
        selected.append(
            {
                **available,
                "usage": str(item.get("usage") or _endpoint_usage(available)),
                "trigger": str(item.get("trigger") or ""),
                "required_for_initial_load": _initial_load_required(
                    item,
                    available,
                    str(item.get("usage") or _endpoint_usage(available)),
                ),
                "binds_to": _text_items(item.get("binds_to"))
                or _default_endpoint_bindings(available),
            }
        )
    if selected:
        return selected

    page_text = f"{page_name} {page_path}".lower()
    is_detail = "详情" in page_name or "detail" in page_text or "{id}" in page_path
    is_form = any(marker in page_text for marker in ("create", "edit", "new")) or any(
        marker in page_name for marker in ("新增", "编辑", "表单")
    )
    defaults = [
        endpoint
        for endpoint in api_endpoints
        if str(endpoint.get("endpoint_id") or "").endswith(
            ".detail" if is_detail else ".list"
        )
    ]
    if not defaults:
        preferred_methods = {"POST", "PATCH", "PUT"} if is_form else {"GET"}
        defaults = [
            endpoint
            for endpoint in api_endpoints
            if str(endpoint.get("method") or "GET").upper() == "GET"
        ]
    if not defaults and api_endpoints:
        defaults = api_endpoints[:1]
    return [
        {
            **endpoint,
            "usage": "load_detail"
            if str(endpoint.get("endpoint_id") or "").endswith(".detail")
            else _endpoint_usage(endpoint),
            "trigger": _default_endpoint_trigger(endpoint, _endpoint_usage(endpoint)),
            "required_for_initial_load": str(endpoint.get("method") or "GET").upper()
            == "GET",
            "binds_to": _default_endpoint_bindings(endpoint),
        }
        for endpoint in defaults
    ]


def _endpoint_usage(endpoint: dict[str, Any]) -> str:
    method = str(endpoint.get("method") or "GET").upper()
    endpoint_id = str(endpoint.get("endpoint_id") or endpoint.get("id") or "")
    if method == "POST":
        return "create"
    if method in {"PUT", "PATCH"}:
        return "update"
    if method == "DELETE":
        return "delete"
    if endpoint_id.endswith(".detail"):
        return "load_detail"
    if endpoint_id.endswith(".list"):
        return "page_load"
    return "read"


def _initial_load_required(
    dependency: dict[str, Any],
    endpoint: dict[str, Any],
    usage: str,
) -> bool:
    if "required_for_initial_load" in dependency:
        return bool(dependency.get("required_for_initial_load"))
    method = str(endpoint.get("method") or "GET").upper()
    return method == "GET" and usage in {"page_load", "load_detail", "read"}


def _default_endpoint_trigger(endpoint: dict[str, Any], usage: str) -> str:
    method = str(endpoint.get("method") or "GET").upper()
    if usage == "page_load":
        return "进入页面或提交筛选条件"
    if usage == "load_detail":
        return "点击查看详情或进入详情页"
    if method == "POST":
        return "提交新增表单"
    if method in {"PUT", "PATCH"}:
        return "提交编辑表单"
    if method == "DELETE":
        return "确认删除操作"
    return "页面交互触发"


def _default_endpoint_bindings(endpoint: dict[str, Any]) -> list[str]:
    method = str(endpoint.get("method") or "GET").upper()
    endpoint_id = str(endpoint.get("endpoint_id") or endpoint.get("id") or "")
    if method == "GET" and endpoint_id.endswith(".list"):
        return ["主内容列表", "分页信息"]
    if method == "GET":
        return ["详情内容", "表单初值"]
    if method == "POST":
        return ["新增交互"]
    if method in {"PUT", "PATCH"}:
        return ["编辑交互"]
    if method == "DELETE":
        return ["删除交互"]
    return ["页面交互"]


def _component_structure(
    page_name: str,
    page_path: str,
    layout: dict[str, Any],
    agent_value: Any,
) -> list[dict[str, Any]]:
    agent_items = _dict_items(agent_value)
    if agent_items:
        return [
            {
                **item,
                "area": str(item.get("area") or item.get("name") or "页面区域"),
                "purpose": str(item.get("purpose") or item.get("description") or ""),
                "components": _text_items(item.get("components")),
            }
            for item in agent_items
        ]
    page_text = f"{page_name} {page_path}".lower()
    is_list = "列表" in page_name or "list" in page_text
    is_detail = "详情" in page_name or "detail" in page_text or "{id}" in page_path
    is_form = any(marker in page_text for marker in ("create", "edit", "new")) or any(
        marker in page_name for marker in ("新增", "编辑", "表单")
    )
    main_components = ["Table", "Pagination"] if is_list else ["Descriptions"]
    if is_form:
        main_components = ["Form"]
    elif is_detail:
        main_components = ["Descriptions", "Tabs"]
    return [
        {
            "area": "页面标题区",
            "purpose": "展示页面标题、上下文入口和主要操作。",
            "components": ["PageHeader", "Breadcrumb", "Button"],
        },
        {
            "area": "筛选/操作区",
            "purpose": "承载查询条件、刷新、新增和批量操作。",
            "components": ["Form", "Input", "Select", "Button", "Space"],
        },
        {
            "area": "主要内容区",
            "purpose": "承载核心业务数据展示或编辑。",
            "components": main_components,
        },
    ]


def _layout_design(
    page_name: str,
    page_path: str,
    layout: dict[str, Any],
    agent_value: Any,
) -> dict[str, Any]:
    if isinstance(agent_value, dict):
        return {
            **agent_value,
            "regions": _dict_items(agent_value.get("regions")),
        }
    regions = _text_items(layout.get("structure"))
    page_text = f"{page_name} {page_path}".lower()
    if "列表" in page_name or "list" in page_text:
        presentation = "表格型列表呈现，适合批量浏览、筛选和行级操作。"
        main_region = "以列表/表格形态承载核心数据浏览。"
    elif "详情" in page_name or "detail" in page_text or "{id}" in page_path:
        presentation = "详情型信息呈现，适合查看单个业务对象的完整信息。"
        main_region = "以详情信息分组承载核心业务字段。"
    elif any(marker in page_text for marker in ("create", "edit", "new")) or any(
        marker in page_name for marker in ("新增", "编辑", "表单")
    ):
        presentation = "表单型录入呈现，适合创建或修改业务对象。"
        main_region = "以表单形态承载字段录入和校验。"
    else:
        presentation = "混合型内容呈现，根据页面目标组织主要信息和辅助信息。"
        main_region = "承载页面核心业务内容。"

    def region_responsibility(region: str) -> str:
        if "标题" in region or "上下文" in region:
            return "说明当前位置、页面目标和必要的上下文入口。"
        if "筛选" in region or "查询" in region:
            return "承载查询条件、筛选条件和重置等数据定位操作。"
        if "操作" in region and "筛选" not in region:
            return "承载页面级新增、刷新、批量处理等主要动作入口。"
        if "主要" in region or "内容" in region:
            return main_region
        if "辅助" in region:
            return "承载辅助信息、说明、统计或上下文补充。"
        return "承载与页面目标相关的业务内容。"

    return {
        "overall_layout": "页面按业务职责组织信息层级，突出主要内容与关键操作。",
        "regions": [
            {
                "name": region,
                "responsibility": region_responsibility(region),
            }
            for region in regions
        ],
        "primary_content_presentation": presentation,
        "operation_entry_position": "页面级操作靠近相关业务区域；行级或项级操作贴近对应数据项。",
        "responsive_strategy": layout.get(
            "responsive",
            "优先桌面端工作台布局，窄屏时保持核心内容优先，辅助信息可折叠或下移。",
        ),
    }


def _operation_interactions(
    api_dependencies: list[dict[str, Any]],
    agent_value: Any,
) -> list[dict[str, Any]]:
    allowed_endpoint_ids = {
        str(api.get("endpoint_id"))
        for api in api_dependencies
        if api.get("endpoint_id")
    }
    agent_items = _dict_items(agent_value)
    if agent_items:
        return [
            {
                **item,
                "action": str(item.get("action") or item.get("name") or "页面操作"),
                "trigger": str(item.get("trigger") or "用户操作"),
                "behavior": str(item.get("behavior") or item.get("description") or ""),
                "endpoint_id": (
                    str(item.get("endpoint_id"))
                    if item.get("endpoint_id")
                    and str(item.get("endpoint_id")) in allowed_endpoint_ids
                    else ""
                ),
            }
            for item in agent_items
        ]
    interactions: list[dict[str, Any]] = []
    for api in api_dependencies:
        method = api.get("method")
        usage = api.get("usage")
        endpoint_id = api.get("endpoint_id")
        if method == "GET" and (
            usage == "load_detail" or str(endpoint_id or "").endswith(".detail")
        ):
            action = "查看详情"
            behavior = "点击行、详情按钮或详情入口后加载单条记录信息。"
        elif method == "GET" and usage in {"page_load", "read"}:
            action = "页面加载/查询"
            behavior = "进入页面或调整筛选条件后调用接口刷新主要内容。"
        elif method == "POST":
            action = "新增"
            behavior = "点击新增或提交表单后调用创建接口，成功后刷新列表或返回详情。"
        elif method in {"PUT", "PATCH"}:
            action = "编辑"
            behavior = "点击编辑并提交修改后调用更新接口，成功后刷新当前数据。"
        elif method == "DELETE":
            action = "删除"
            behavior = "点击删除并确认后调用删除接口，成功后移除对应记录。"
        else:
            action = str(usage or method or "操作")
            behavior = str(api.get("summary") or "调用页面相关 API 完成业务操作。")
        interactions.append(
            {
                "action": action,
                "trigger": str(api.get("trigger") or "用户操作或页面生命周期"),
                "behavior": behavior,
                "endpoint_id": endpoint_id,
            }
        )
    return interactions


def _state_feedback(layout: dict[str, Any], agent_value: Any) -> list[dict[str, Any]]:
    agent_items = _dict_items(agent_value)
    if agent_items:
        return [
            {
                **item,
                "state": str(item.get("state") or item.get("name") or "反馈状态"),
                "trigger": str(item.get("trigger") or "页面交互"),
                "behavior": str(item.get("behavior") or item.get("description") or ""),
                "scope": str(item.get("scope") or "相关业务区域"),
            }
            for item in agent_items
        ]
    states = _text_items(layout.get("states")) or ["loading", "empty", "error", "ready"]
    default_behavior = {
        "loading": "数据加载中时在相关内容区域展示加载反馈，并避免重复提交。",
        "empty": "查询无结果时在相关内容区域展示空状态和可恢复操作。",
        "error": "接口或操作失败时展示错误原因，并保留用户上下文。",
        "ready": "数据加载完成后展示可交互内容。",
        "success": "新增、编辑、删除等操作成功后提示结果并刷新相关内容。",
        "confirm": "删除、批量操作等高风险动作执行前要求用户确认。",
        "validation": "表单提交前在对应字段附近展示校验反馈。",
    }
    return [
        {
            "state": state,
            "trigger": "页面加载或用户操作",
            "behavior": default_behavior.get(state, "根据交互结果展示对应反馈。"),
            "scope": "相关业务区域",
        }
        for state in states
    ]


def _operation_visibility(
    operation_interactions: list[dict[str, Any]],
    permissions: list[str],
) -> list[dict[str, Any]]:
    visible_to_all = _text_items(permissions)
    visibility = []
    for interaction in operation_interactions:
        action = str(interaction.get("action") or "页面操作")
        dangerous = any(marker in action for marker in ("删除", "批量", "编辑", "新增"))
        visible_to = ["admin"] if dangerous and "admin" in visible_to_all else visible_to_all
        visibility.append(
            {
                "action": action,
                "visible_to": visible_to,
                "unauthorized_behavior": "隐藏操作入口或展示无权限提示。",
            }
        )
    return visibility


def _page_navigation(page: dict[str, Any], agent_value: Any) -> list[dict[str, Any]]:
    agent_items = _dict_items(agent_value)
    if agent_items:
        return [
            {
                **item,
                "trigger": str(item.get("trigger") or item.get("action") or "页面跳转"),
                "target_page_id": str(item.get("target_page_id") or ""),
                "target_path": str(item.get("target_path") or item.get("path") or ""),
                "behavior": str(item.get("behavior") or item.get("description") or ""),
            }
            for item in agent_items
        ]
    navigation = page.get("page_navigation")
    if _dict_items(navigation):
        return _dict_items(navigation)
    return []


def extract_page_detail_context(
    project_plan: dict[str, Any],
    page_id: str,
) -> dict[str, Any]:
    page = _find_by_id(project_plan["frontend_pages"], page_id)
    page_name = str(page.get("name") or page_id)
    page_path = str(page.get("path") or "/")
    references = page_design_references(project_plan, page_id)
    endpoint_ids = {
        str(item.get("endpoint_id"))
        for item in references["endpoint_dependencies"]
    }
    relevant_contracts = [
        {
            **contract,
            "endpoints": [
                endpoint
                for endpoint in _dict_items(contract.get("endpoints"))
                if str(endpoint.get("id")) in endpoint_ids
            ],
        }
        for contract in _dict_items(project_plan.get("api_contracts"))
        if any(
            str(endpoint.get("id")) in endpoint_ids
            for endpoint in _dict_items(contract.get("endpoints"))
        )
    ]
    navigation_pages = [
        {
            "id": candidate.get("id"),
            "name": candidate.get("name"),
            "path": candidate.get("path"),
        }
        for candidate in _dict_items(project_plan.get("frontend_pages"))
        if str(candidate.get("id"))
        in {str(item.get("target_page_id")) for item in references["navigation_targets"]}
    ]
    return {
        "type": "page",
        "page_id": page_id,
        "page_name": page_name,
        "path": page_path,
        "module_id": str(page.get("module_id") or "core"),
        "page_goal": page.get("description") or f"完成 {page_name} 的核心业务展示与操作。",
        "layout": {
            "structure": page.get(
                "layout",
                ["页面标题区", "筛选/操作区", "主要内容区"],
            ),
            "responsive": "优先桌面端工作台布局，保持内容可扫描、操作可达。",
        },
        "interactions": page.get(
            "interactions",
            ["进入页面后加载数据", "支持查看主要业务内容", "展示 loading、empty、error、ready 状态"],
        ),
        "references": references,
        "endpoint_contracts": relevant_contracts,
        "navigation_pages": navigation_pages,
    }


def create_data_source_detail_plan(
    project_plan: dict[str, Any],
    data_source_id: str,
    user_request: str = "",
    agent_detail_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = _find_by_id(project_plan["data_sources"], data_source_id)
    api_contracts = [
        contract
        for contract in project_plan.get("api_contracts", [])
        if contract.get("data_source_id") == data_source_id
    ]
    dependent_pages = [
        {
            "page_id": page.get("id"),
            "page_name": page.get("name"),
            "path": page.get("path"),
        }
        for page in project_plan.get("frontend_pages", [])
        if isinstance(page, dict)
    ]
    detail_plan = {
        "id": f"data_source_detail:{source['id']}",
        "type": "data_source",
        "data_source_id": source["id"],
        "data_source_name": source["name"],
        "status": "confirmed",
        "confirmed_at": datetime.now(UTC).isoformat(),
        "source_data_source": source,
        "schema_refs": source.get("schema_refs", []),
        "entities": source.get("entities", []),
        "api_contracts": api_contracts,
        "dependent_pages": dependent_pages,
        "seed_strategy": source.get("seed_strategy"),
        "user_confirmation_note": user_request.strip(),
        "acceptance_criteria": [
            f"数据源 {source['name']} 可以提供已约定实体和字段。",
            "相关 API 契约与 ProjectPlan.api_contracts 保持一致。",
            "依赖该数据源的页面只能通过已声明 API 访问数据。",
        ],
        "approved": True,
    }
    if isinstance(agent_detail_plan, dict):
        detail_plan.update(agent_detail_plan)
    for key in ("entities", "api_contracts", "dependent_pages", "acceptance_criteria"):
        if not isinstance(detail_plan.get(key), list):
            detail_plan[key] = []
    detail_plan.pop("schema", None)
    detail_plan.update(
        {
            "id": f"data_source_detail:{source['id']}",
            "type": "data_source",
            "data_source_id": source["id"],
            "data_source_name": source["name"],
            "status": "confirmed",
            "confirmed_at": datetime.now(UTC).isoformat(),
            "source_data_source": source,
            "schema_refs": source.get("schema_refs", []),
            "api_contracts": api_contracts,
            "user_confirmation_note": user_request.strip(),
            "approved": True,
        }
    )
    return detail_plan


def attach_data_source_detail_plan(
    project_plan: dict[str, Any],
    detail_plan: dict[str, Any],
) -> dict[str, Any]:
    updated_plan = deepcopy(project_plan)
    existing_details = {
        item["data_source_id"]: item
        for item in updated_plan.get("data_source_detail_plans", [])
        if isinstance(item, dict) and item.get("data_source_id")
    }
    existing_details[detail_plan["data_source_id"]] = detail_plan
    updated_plan["data_source_detail_plans"] = list(existing_details.values())

    for source in updated_plan["data_sources"]:
        if source.get("id") == detail_plan["data_source_id"]:
            source["detail_status"] = "confirmed"
            source["detail_plan_id"] = detail_plan["id"]

    updated_plan["data_source_detail_confirmation_summary"] = {
        "confirmed_data_sources": len(updated_plan["data_source_detail_plans"]),
        "total_data_sources": len(updated_plan["data_sources"]),
        "latest_data_source_id": detail_plan["data_source_id"],
    }
    return updated_plan


def create_page_detail_plan(
    project_plan: dict[str, Any],
    page_context: dict[str, Any],
    agent_note: str = "live main-agent page detail design",
    agent_detail_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    page_id = page_context["page_id"]
    page = _find_by_id(project_plan["frontend_pages"], page_id)
    page_name = str(page.get("name") or page_id)
    page_path = str(page.get("path") or "/")
    api_contracts = _dict_items(project_plan.get("api_contracts"))
    api_dependencies = normalize_page_api_dependencies(
        api_contracts,
        [],
        page_context.get("references", {}).get("endpoint_dependencies", []),
        page_path=page_path,
        page_name=page_name,
    )
    endpoint_dependencies = [
        {
            "api_contract_id": item.get("api_contract_id"),
            "endpoint_id": item.get("endpoint_id"),
            "usage": item.get("usage") or "read",
            "required": bool(item.get("required_for_initial_load")),
        }
        for item in api_dependencies
        if item.get("api_contract_id") and item.get("endpoint_id")
    ]
    endpoint_ids_by_contract: dict[str, set[str]] = {}
    for dependency in endpoint_dependencies:
        if not isinstance(dependency, dict):
            continue
        contract_id = dependency.get("api_contract_id")
        endpoint_id = dependency.get("endpoint_id")
        if contract_id and endpoint_id:
            endpoint_ids_by_contract.setdefault(str(contract_id), set()).add(
                str(endpoint_id)
            )
    layout = page_context.get("layout", {})
    contract_data_sources = [
        {
            "id": contract.get("data_source_id", ""),
            "api_contract_id": contract.get("id", ""),
            "endpoints": contract_endpoints_for_dependencies(
                contract,
                endpoint_ids_by_contract.get(str(contract.get("id")), set()),
            ),
        }
        for contract in api_contracts
        if contract.get("data_source_id")
        and str(contract.get("id")) in endpoint_ids_by_contract
    ]
    component_structure = _component_structure(
        page_name,
        page_path,
        layout,
        (agent_detail_plan or {}).get("component_structure"),
    )
    layout_design = _layout_design(
        page_name,
        page_path,
        layout,
        (agent_detail_plan or {}).get("layout_design"),
    )
    page_navigation = [
        {
            **item,
            "target_path": str(
                _find_by_id(project_plan["frontend_pages"], item["target_page_id"]).get("path")
                or ""
            ),
        }
        for item in page_context.get("references", {}).get("navigation_targets", [])
        if item.get("target_page_id")
    ]
    operation_interactions = _operation_interactions(
        api_dependencies,
        (agent_detail_plan or {}).get("operation_interactions"),
    )
    operation_visibility = _operation_visibility(
        operation_interactions,
        page_context.get("references", {}).get("permissions", []),
    )
    state_feedback = _state_feedback(
        {
            **layout,
            "states": page_context.get("states", page.get("states", [])),
        },
        (agent_detail_plan or {}).get("state_feedback"),
    )
    response_bindings = normalize_response_bindings(
        project_plan.get("api_contracts", []),
        endpoint_dependencies if isinstance(endpoint_dependencies, list) else [],
        (agent_detail_plan or {}).get("response_bindings"),
    )
    detail_plan = {
        "id": f"page_detail:{page_id}",
        "type": "page",
        "page_id": page_id,
        "page_name": page_name,
        "path": page_path,
        "status": "confirmed",
        "confirmed_at": datetime.now(UTC).isoformat(),
        "source_page_context": page_context,
        "page_goal": page_context["page_goal"],
        "basic_layout": {
            "structure": layout.get(
                "structure",
                ["页面标题区", "主要内容区", "操作区"],
            ),
            "states": page_context.get("states", page.get("states", [])),
            "responsive": layout.get(
                "responsive",
                "默认支持桌面端布局，后续可扩展移动端适配。",
            ),
        },
        "interactions": page_context["interactions"],
        "layout_design": layout_design,
        "component_structure": component_structure,
        "state_feedback": state_feedback,
        "operation_interactions": operation_interactions,
        "operation_visibility": operation_visibility,
        "page_navigation": page_navigation,
        "api_dependencies": api_dependencies,
        "data_sources": contract_data_sources,
        "permissions": page_context.get("references", {}).get("permissions", []),
        "endpoint_dependencies": page_context.get("references", {}).get("endpoint_dependencies", []),
        "navigation_targets": page_context.get("references", {}).get("navigation_targets", []),
        "response_bindings": response_bindings,
        "acceptance_criteria": [
            f"用户可以访问 {page_path} 并看到 {page_name} 的主要内容。",
            "页面具备 loading、empty、error、ready 四类基础状态。",
            "页面只访问 ProjectPlan.api_contracts 中已声明的 API。",
            "页面权限与 ProjectPlan 页面清单保持一致。",
        ],
        "agent_note": agent_note,
        "approved": True,
    }
    if not isinstance(detail_plan.get("basic_layout"), dict):
        detail_plan["basic_layout"] = {}
    detail_plan["basic_layout"] = _normalize_basic_layout(detail_plan["basic_layout"])
    for key in ("interactions", "data_sources", "permissions", "acceptance_criteria"):
        if not isinstance(detail_plan.get(key), list):
            detail_plan[key] = []
    for key in (
        "component_structure",
        "layout_design",
        "state_feedback",
        "operation_interactions",
        "page_navigation",
        "api_dependencies",
        "operation_visibility",
    ):
        if not isinstance(detail_plan.get(key), list):
            detail_plan[key] = []
    for key in ("interactions", "permissions", "acceptance_criteria"):
        detail_plan[key] = _text_items(detail_plan.get(key))
    detail_plan.update(
        {
            "id": f"page_detail:{page_id}",
            "type": "page",
            "page_id": page_id,
            "page_name": page_name,
            "path": page_path,
            "status": "confirmed",
            "confirmed_at": datetime.now(UTC).isoformat(),
            "source_page_context": page_context,
            "data_sources": contract_data_sources,
            "api_dependencies": api_dependencies,
            "layout_design": layout_design,
            "component_structure": component_structure,
            "state_feedback": state_feedback,
            "operation_interactions": operation_interactions,
            "operation_visibility": operation_visibility,
            "page_navigation": page_navigation,
            "response_bindings": response_bindings,
            "agent_note": agent_note,
            "approved": True,
        }
    )
    return detail_plan


def attach_page_detail_plan(
    project_plan: dict[str, Any],
    detail_plan: dict[str, Any],
) -> dict[str, Any]:
    updated_plan = deepcopy(project_plan)
    existing_details = {
        item["page_id"]: item
        for item in updated_plan.get("page_detail_plans", [])
        if isinstance(item, dict) and item.get("page_id")
    }
    existing_details[detail_plan["page_id"]] = detail_plan
    updated_plan["page_detail_plans"] = list(existing_details.values())

    for page in updated_plan["frontend_pages"]:
        if page.get("id") == detail_plan["page_id"]:
            page["detail_status"] = "confirmed"
            page["detail_plan_id"] = detail_plan["id"]

    updated_plan["detail_confirmation_summary"] = {
        "confirmed_pages": len(updated_plan["page_detail_plans"]),
        "total_pages": len(updated_plan["frontend_pages"]),
        "latest_page_id": detail_plan["page_id"],
    }
    return updated_plan
