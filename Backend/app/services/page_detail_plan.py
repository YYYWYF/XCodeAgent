from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from typing import Any

from app.services.api_contracts import (
    normalize_page_api_dependencies,
    normalize_response_bindings,
)
from app.services.entity_definitions import (
    plan_data_sources,
)
from app.services.frontend_page_tree import (
    find_frontend_page,
    project_plan_page_records,
    update_frontend_page_leaves,
)
from app.services.page_dependencies import page_design_references


def detail_design_targets(project_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """返回详细设计入口可选择的页面目标；接口目标由 api_contracts[].endpoints 单独展开。"""

    page_targets = [
        {
            "id": page.get("pageId"),
            "type": "page",
            "label": f"页面：{page.get('name') or page.get('pageId') or '未命名页面'}",
            "name": page.get("name") or page.get("pageId") or "未命名页面",
            "description": (
                f"{page.get('path') or '/'}，"
                f"{page.get('description') or page.get('name') or '待补充页面目标'}"
            ),
        }
        for page in project_plan_page_records(project_plan)
        if isinstance(page, dict) and page.get("pageId")
    ]
    return page_targets


def resolve_detail_design_target(
    project_plan: dict[str, Any],
    request: str,
    selectedPageId: str | None = None,
) -> dict[str, Any] | None:
    """按用户选择或文本请求解析页面详细设计目标。"""

    targets = detail_design_targets(project_plan)
    for target in targets:
        if target["type"] == "page" and target["id"] == selectedPageId:
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


def _find_page_by_pageId(items: list[dict[str, Any]], pageId: str) -> dict[str, Any]:
    """按页面唯一标识 pageId 查找页面对象。"""

    page = find_frontend_page(items, pageId)
    if page is not None:
        return page
    raise ValueError(f"项目计划中不存在页面：{pageId}")


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


def _endpoint_identity(endpoint: dict[str, Any], index: int) -> str:
    """返回 endpoint 的稳定选择标识；没有显式 id 时与前端一致使用 1-based 序号。"""

    return str(endpoint.get("id") or index + 1)


def _api_endpoints_for_contracts(
    api_contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    for contract in api_contracts:
        contract_id = str(contract.get("id") or "")
        for endpoint_index, endpoint in enumerate(_dict_items(contract.get("endpoints"))):
            endpoint_id = _endpoint_identity(endpoint, endpoint_index)
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
                "targetPageId": str(item.get("targetPageId") or ""),
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
    pageId: str,
) -> dict[str, Any]:
    """提取单个叶子页面详细设计所需的最小上下文。"""

    page = _find_page_by_pageId(project_plan_page_records(project_plan), pageId)
    page_name = str(page.get("name") or pageId)
    page_path = str(page.get("path") or "/")
    references = page_design_references(project_plan, pageId)
    endpoint_ids = {
        str(item.get("endpoint_id"))
        for item in references["endpoint_dependencies"]
    }
    endpoint_detail_summaries = [
        _endpoint_detail_summary(detail)
        for detail in _dict_items(project_plan.get("endpoint_detail_plans"))
        if str(detail.get("endpoint_id") or "") in endpoint_ids
    ]
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
            "pageId": candidate.get("pageId"),
            "name": candidate.get("name"),
            "path": candidate.get("path"),
        }
        for candidate in project_plan_page_records(project_plan)
        if str(candidate.get("pageId"))
        in {str(item.get("targetPageId")) for item in references["navigation_targets"]}
    ]
    return {
        "type": "page",
        "pageId": pageId,
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
        "endpoint_detail_summaries": endpoint_detail_summaries,
        "navigation_pages": navigation_pages,
    }


def _endpoint_detail_summary(detail: dict[str, Any]) -> dict[str, Any]:
    """把 EndpointDetail 压缩为页面设计可读取的摘要，避免复制完整接口设计。"""

    interface_design = (
        detail.get("interface_design")
        if isinstance(detail.get("interface_design"), dict)
        else {}
    )
    request = (
        interface_design.get("request")
        if isinstance(interface_design.get("request"), dict)
        else {}
    )
    response = (
        interface_design.get("response_format")
        if isinstance(interface_design.get("response_format"), dict)
        else {}
    )
    processing_logic = _text_items(detail.get("processing_logic"))
    source_endpoint = (
        detail.get("source_endpoint")
        if isinstance(detail.get("source_endpoint"), dict)
        else {}
    )
    request_body = (
        request.get("request_body")
        if isinstance(request.get("request_body"), dict)
        else {}
    )
    return {
        "api_contract_id": str(detail.get("api_contract_id") or ""),
        "endpoint_id": str(detail.get("endpoint_id") or ""),
        "method": str(detail.get("method") or source_endpoint.get("method") or "GET").upper(),
        "path": str(detail.get("path") or source_endpoint.get("path") or ""),
        "summary": str(detail.get("summary") or source_endpoint.get("summary") or ""),
        "status": str(detail.get("status") or ""),
        "request_schema_ref": request_body.get("schema_ref")
        or source_endpoint.get("request_schema_ref")
        or "",
        "response_schema_ref": response.get("schema_ref")
        or source_endpoint.get("response_schema_ref")
        or "",
        "processing_summary": processing_logic[:3],
    }


def endpoint_bound_entity_summaries(
    project_plan: dict[str, Any],
    api_contract_id: str,
) -> list[dict[str, Any]]:
    """从已确认实体设计构造接口绑定实体的只读摘要，接口不重新设计数据来源。"""

    api_contracts = _dict_items(project_plan.get("api_contracts"))
    contract = next(
        (
            item
            for item in api_contracts
            if str(item.get("id") or "") == api_contract_id
        ),
        None,
    )
    if contract is None:
        raise ValueError(f"项目计划中不存在 API 契约：{api_contract_id}")
    entity_ids = _text_items(contract.get("entity_ids"))
    confirmed_details = {
        str(detail.get("entity_id") or ""): detail
        for detail in _dict_items(project_plan.get("entity_detail_plans"))
        if str(detail.get("status") or "") == "confirmed"
        and str(detail.get("entity_id") or "")
    }
    entity_to_source_type: dict[str, str] = {}
    for source in plan_data_sources(project_plan):
        source_type = str(source.get("type") or "")
        for entity in _dict_items(source.get("entities")):
            entity_id = str(entity.get("id") or "")
            if entity_id:
                entity_to_source_type.setdefault(entity_id, source_type)
    summaries: list[dict[str, Any]] = []
    for entity_id in entity_ids:
        detail = confirmed_details.get(entity_id)
        if detail is None:
            raise ValueError(
                f"接口关联的实体 {entity_id} 尚未完成实体设计并确认，"
                "请先在实体设计阶段选择并确认数据源，再设计接口。"
            )
        database_design = (
            detail.get("database_design")
            if isinstance(detail.get("database_design"), dict)
            else {}
        )
        external_api_design = (
            detail.get("external_api_design")
            if isinstance(detail.get("external_api_design"), dict)
            else {}
        )
        api_info = (
            external_api_design.get("api_info")
            if isinstance(external_api_design.get("api_info"), dict)
            else {}
        )
        summaries.append(
            {
                "entity_id": entity_id,
                "entity_name": str(detail.get("entity_name") or entity_id),
                "data_source_type": str(
                    detail.get("data_source_type")
                    or entity_to_source_type.get(entity_id)
                    or ""
                ),
                "fields": [
                    {
                        "name": str(field.get("name") or ""),
                        "label": str(field.get("label") or field.get("name") or ""),
                        "type": str(field.get("type") or "text"),
                        "required": bool(field.get("required")),
                    }
                    for field in _dict_items(detail.get("fields"))[:100]
                    if field.get("name")
                ],
                "database_reference": {
                    "matched_table": database_design.get("matched_table"),
                    "binding_count": len(_dict_items(database_design.get("bindings"))),
                    "operation_count": len(
                        _dict_items(database_design.get("database_operations"))
                    ),
                    "table_generation_required": bool(
                        (
                            database_design.get("table_generation")
                            if isinstance(
                                database_design.get("table_generation"), dict
                            )
                            else {}
                        ).get("required")
                    ),
                }
                if str(detail.get("data_source_type") or "") == "database"
                else None,
                "external_api_reference": {
                    "path": str(api_info.get("path") or ""),
                    "method": str(api_info.get("method") or ""),
                    "mapping_count": len(
                        _dict_items(external_api_design.get("field_mappings"))
                    ),
                }
                if str(detail.get("data_source_type") or "") == "external_api"
                else None,
            }
        )
    return summaries


def extract_endpoint_detail_context(
    project_plan: dict[str, Any],
    api_contract_id: str,
    endpoint_id: str,
) -> dict[str, Any]:
    """从 ProjectPlan 中提取单个 endpoint 详细设计所需的最小上下文。"""

    api_contracts = _dict_items(project_plan.get("api_contracts"))
    contract = next(
        (
            item
            for item in api_contracts
            if str(item.get("id") or "") == api_contract_id
        ),
        None,
    )
    if not contract:
        raise ValueError(f"项目计划中不存在 API 契约：{api_contract_id}")
    endpoints = _dict_items(contract.get("endpoints"))
    endpoint = next(
        (
            item
            for endpoint_index, item in enumerate(endpoints)
            if _endpoint_identity(item, endpoint_index) == endpoint_id
        ),
        None,
    )
    if not endpoint:
        raise ValueError(f"API 契约 {api_contract_id} 中不存在接口：{endpoint_id}")
    bound_entities = endpoint_bound_entity_summaries(project_plan, api_contract_id)
    dependent_pages = [
        {
            "pageId": page.get("pageId") or page.get("id"),
            "page_name": page.get("name"),
            "path": page.get("path"),
            "usage": str(dependency.get("usage") or ""),
            "trigger": str(dependency.get("trigger") or ""),
        }
        for page in project_plan_page_records(project_plan)
        for dependency in _dict_items(
            (page.get("references") if isinstance(page.get("references"), dict) else {}).get(
                "endpoint_dependencies"
            )
        )
        if str(dependency.get("endpoint_id") or "") == endpoint_id
    ]
    schemas = contract.get("schemas") if isinstance(contract.get("schemas"), dict) else {}
    return {
        "type": "endpoint",
        "api_contract": contract,
        "api_contract_id": api_contract_id,
        "endpoint": endpoint,
        "endpoint_id": endpoint_id,
        "method": str(endpoint.get("method") or "GET").upper(),
        "path": str(endpoint.get("path") or ""),
        "summary": str(endpoint.get("summary") or endpoint_id),
        "request_schema": schemas.get(str(endpoint.get("request_schema_ref") or "")),
        "response_schema": schemas.get(str(endpoint.get("response_schema_ref") or "")),
        "dependent_pages": dependent_pages,
        "bound_entities": bound_entities,
    }


def create_endpoint_detail_plan(
    project_plan: dict[str, Any],
    endpoint_context: dict[str, Any],
    user_request: str = "",
    agent_detail_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """创建单个 endpoint 粒度的接口详细设计。"""

    endpoint = endpoint_context["endpoint"]
    contract = endpoint_context["api_contract"]
    method = endpoint_context["method"]
    path = endpoint_context["path"]
    endpoint_id = endpoint_context["endpoint_id"]
    request_parameters = _dict_items(endpoint.get("parameters"))
    path_parameters = [
        item for item in request_parameters if str(item.get("in") or "") == "path"
    ]
    query_parameters = [
        item for item in request_parameters if str(item.get("in") or "query") == "query"
    ]
    header_parameters = [
        item for item in request_parameters if str(item.get("in") or "") == "header"
    ]
    request_schema_ref = endpoint.get("request_schema_ref") or ""
    response_schema_ref = endpoint.get("response_schema_ref") or ""
    detail_plan = {
        "id": f"endpoint_detail:{endpoint_context['api_contract_id']}:{endpoint_id}",
        "type": "endpoint",
        "api_contract_id": endpoint_context["api_contract_id"],
        "endpoint_id": endpoint_id,
        "name": f"{method} {path}".strip(),
        "method": method,
        "path": path,
        "summary": endpoint_context["summary"],
        "status": "confirmed",
        "confirmed_at": datetime.now(UTC).isoformat(),
        "source_endpoint": endpoint,
        "source_api_contract": {
            "id": contract.get("id"),
            "label": contract.get("label") or contract.get("name") or contract.get("id"),
            "base_path": contract.get("base_path"),
        },
        "data_usage": {
            "served_pages": endpoint_context.get("dependent_pages", []),
            "purpose": endpoint_context["summary"],
            "served_business": endpoint_context["summary"],
            "consumer": "依赖该接口的前端页面或后续服务",
        },
        "interface_design": {
            "restful_style": {
                "compliant": True,
                "method": method,
                "path": path,
                "resource": _rest_resource_name(path),
                "description": f"使用 {method} {path} 表达接口资源操作。",
            },
            "request": {
                "path_parameters": path_parameters,
                "query_parameters": query_parameters,
                "header_parameters": header_parameters,
                "request_body": {
                    "required": bool(request_schema_ref),
                    "schema_ref": request_schema_ref or None,
                    "schema": endpoint_context.get("request_schema") or {},
                    "fields": [],
                    "note": (
                        "该接口需要请求体。"
                        if request_schema_ref
                        else f"{method} 接口不需要请求体。"
                    ),
                },
                "file_upload": {
                    "required": False,
                    "format": None,
                    "note": "当前接口不涉及文件上传。",
                },
            },
            "response_format": {
                "status_code": 200,
                "schema_ref": response_schema_ref or None,
                "content_type": "application/json",
                "schema": endpoint_context.get("response_schema") or {},
                "structure": endpoint_context.get("response_schema") or {},
                "errors": endpoint.get("error_responses") or endpoint.get("error_codes") or [],
            },
        },
        "processing_logic": [
            "校验路径、查询、请求头和请求体参数。",
            "按权限、租户或业务范围过滤数据。",
            "基于接口绑定实体的已确认数据源读写数据并转换为 API 契约约定的响应结构。",
            "处理空数据、参数错误、权限不足和数据读写异常。",
        ],
        "acceptance_criteria": [
            f"`{method} {path}` 按 API 契约接收请求并返回约定响应。",
            "参数错误、权限不足、空数据和数据来源异常均有明确响应。",
            "依赖页面可按 endpoint 返回字段完成展示或交互。",
        ],
        "user_confirmation_note": user_request.strip(),
        "approved": True,
    }
    if isinstance(agent_detail_plan, dict):
        detail_plan.update(_formal_endpoint_detail_fields(agent_detail_plan))
    detail_plan.update(
        {
            "id": f"endpoint_detail:{endpoint_context['api_contract_id']}:{endpoint_id}",
            "type": "endpoint",
            "api_contract_id": endpoint_context["api_contract_id"],
            "endpoint_id": endpoint_id,
            "method": method,
            "path": path,
            "status": "confirmed",
            "confirmed_at": datetime.now(UTC).isoformat(),
            "source_endpoint": endpoint,
            "source_api_contract": detail_plan["source_api_contract"],
            "approved": True,
        }
    )
    return detail_plan


def compose_endpoint_detail_from_decision(
    project_plan: dict[str, Any],
    endpoint_context: dict[str, Any],
    endpoint_decision: dict[str, Any],
    *,
    user_request: str = "",
) -> dict[str, Any]:
    """以 EndpointDecision 为唯一语义来源，确定性组装正式 EndpointDetail。"""

    endpoint_decision = deepcopy(endpoint_decision)
    validate_endpoint_decision(endpoint_decision)
    detail_plan = create_endpoint_detail_plan(
        project_plan,
        endpoint_context,
        user_request=user_request,
    )
    detail_plan["endpoint_decision"] = _normalize_endpoint_decision(endpoint_decision)
    refresh_endpoint_detail_from_decision(detail_plan)
    return detail_plan


def refresh_endpoint_detail_from_decision(detail_plan: dict[str, Any]) -> None:
    """在决策或用户选择变化后重建投影字段，避免详情内部出现第二套语义。"""

    raw_decision = detail_plan.get("endpoint_decision")
    validate_endpoint_decision(raw_decision)
    decision = _normalize_endpoint_decision(raw_decision)
    detail_plan["endpoint_decision"] = decision
    detail_plan["risks"] = deepcopy(decision["risks"])
    detail_plan["design_stage"] = "complete"
    detail_plan["processing_logic"] = _processing_logic_from_endpoint_decision(
        detail_plan,
        decision,
    )
    detail_plan["acceptance_criteria"] = _acceptance_criteria_from_endpoint_decision(
        detail_plan,
        decision,
    )


def validate_endpoint_decision(value: Any) -> None:
    """统一校验 EndpointDecision 契约，生成与审核链路不得各自定义结构。"""

    decision = value if isinstance(value, dict) else {}
    semantics = decision.get("operation_semantics")
    if not isinstance(semantics, dict):
        raise ValueError("接口决策缺少有效字段：operation_semantics")
    if semantics.get("operation_kind") not in {"read", "create", "update", "delete", "action"}:
        raise ValueError("接口决策字段非法：operation_semantics.operation_kind")
    if semantics.get("target_cardinality") not in {
        "exactly_one",
        "zero_or_one",
        "many",
        "not_applicable",
    }:
        raise ValueError("接口决策字段非法：operation_semantics.target_cardinality")
    selector = semantics.get("selector")
    if (
        not isinstance(selector, dict)
        or selector.get("source") not in {"path", "query", "request_body", "contract", "none"}
        or not isinstance(selector.get("fields"), list)
    ):
        raise ValueError("接口决策缺少有效选择器：operation_semantics.selector")
    if not isinstance(semantics.get("transaction_required"), bool):
        raise ValueError("接口决策字段类型错误：operation_semantics.transaction_required")
    if not isinstance(semantics.get("success_status_code"), int):
        raise ValueError("接口决策字段类型错误：operation_semantics.success_status_code")
    if semantics.get("side_effect") not in {"none", "insert", "update", "delete", "custom"}:
        raise ValueError("接口决策字段非法：operation_semantics.side_effect")
    if not isinstance(decision.get("risks"), list):
        raise ValueError("接口决策字段类型错误：risks 必须是数组")


def _normalize_endpoint_decision(value: Any) -> dict[str, Any]:
    """规范化 EndpointDecision，使后续组装只读取固定结构。"""

    decision = value if isinstance(value, dict) else {}
    semantics = (
        decision.get("operation_semantics")
        if isinstance(decision.get("operation_semantics"), dict)
        else {}
    )
    selector = semantics.get("selector") if isinstance(semantics.get("selector"), dict) else {}
    return {
        "operation_semantics": {
            "operation_kind": str(semantics.get("operation_kind") or "action"),
            "target_cardinality": str(
                semantics.get("target_cardinality") or "not_applicable"
            ),
            "selector": {
                "source": str(selector.get("source") or "none"),
                "fields": _text_items(selector.get("fields")),
            },
            "transaction_required": bool(semantics.get("transaction_required")),
            "zero_match_behavior": str(semantics.get("zero_match_behavior") or ""),
            "multiple_match_behavior": str(
                semantics.get("multiple_match_behavior") or ""
            ),
            "success_status_code": semantics.get("success_status_code"),
            "side_effect": str(semantics.get("side_effect") or "none"),
        },
        "risks": _text_items(decision.get("risks")),
    }


def _processing_logic_from_endpoint_decision(
    detail_plan: dict[str, Any],
    decision: dict[str, Any],
) -> list[str]:
    """把闭合决策映射为处理逻辑，不再次推断字段或数据操作。"""

    semantics = decision["operation_semantics"]
    selector = semantics["selector"]
    selector_fields = "、".join(selector["fields"]) or "无额外选择字段"
    cardinality_labels = {
        "exactly_one": "恰好一个目标",
        "zero_or_one": "零个或一个目标",
        "many": "多个目标",
        "not_applicable": "不限定目标数量",
    }
    logic = [
        f"按 API 契约校验 {detail_plan.get('method', 'GET')} {detail_plan.get('path', '')} 的请求。",
        (
            f"通过 {selector['source']} 中的 {selector_fields} 定位"
            f"{cardinality_labels.get(semantics['target_cardinality'], semantics['target_cardinality'])}。"
        ),
        (
            "基于接口绑定实体的已确认数据源执行"
            f" {semantics['operation_kind']}，副作用为 {semantics['side_effect']}。"
        ),
    ]
    if semantics["zero_match_behavior"]:
        logic.append(f"未匹配目标时：{semantics['zero_match_behavior']}")
    if semantics["multiple_match_behavior"]:
        logic.append(f"匹配多个目标时：{semantics['multiple_match_behavior']}")
    if semantics["transaction_required"]:
        logic.append("数据变更必须在事务中完成，失败时整体回滚。")
    return logic


def _acceptance_criteria_from_endpoint_decision(
    detail_plan: dict[str, Any],
    decision: dict[str, Any],
) -> list[str]:
    """把同一闭合决策映射为验收标准，避免模型独立生成另一套结果语义。"""

    semantics = decision["operation_semantics"]
    criteria = [
        (
            f"`{detail_plan.get('method', 'GET')} {detail_plan.get('path', '')}` "
            f"仅按契约执行 {semantics['operation_kind']}，目标数量规则为 "
            f"{semantics['target_cardinality']}。"
        ),
        (
            f"成功响应状态码为 {semantics['success_status_code']}，"
            f"数据副作用为 {semantics['side_effect']}。"
        ),
    ]
    if semantics["zero_match_behavior"]:
        criteria.append(f"零匹配结果符合决策：{semantics['zero_match_behavior']}")
    if semantics["multiple_match_behavior"]:
        criteria.append(f"多匹配结果符合决策：{semantics['multiple_match_behavior']}")
    return criteria


def _formal_endpoint_detail_fields(agent_detail_plan: dict[str, Any]) -> dict[str, Any]:
    """只接收 endpoint 详细设计正式模板字段，避免模型旧字段混入产物。"""

    allowed_fields = {
        "data_usage",
        "interface_design",
        "processing_logic",
        "dependent_pages",
        "acceptance_criteria",
        "risks",
        "endpoint_decision",
    }
    result = {
        key: deepcopy(value)
        for key, value in agent_detail_plan.items()
        if key in allowed_fields
    }
    return result


def _rest_resource_name(path: str) -> str:
    """从接口路径中提取 RESTful 资源名。"""

    parts = [part for part in path.strip("/").split("/") if part and not part.startswith("{")]
    return parts[-1] if parts else path or "/"


def attach_endpoint_detail_plan(
    project_plan: dict[str, Any],
    detail_plan: dict[str, Any],
) -> dict[str, Any]:
    """把 endpoint 详细设计挂回 ProjectPlan 内存态。"""

    updated_plan = deepcopy(project_plan)
    detail_key = (
        str(detail_plan.get("api_contract_id") or ""),
        str(detail_plan.get("endpoint_id") or ""),
    )
    existing_details = {
        (
            str(item.get("api_contract_id") or ""),
            str(item.get("endpoint_id") or ""),
        ): item
        for item in updated_plan.get("endpoint_detail_plans", [])
        if isinstance(item, dict) and item.get("api_contract_id") and item.get("endpoint_id")
    }
    existing_details[detail_key] = detail_plan
    updated_plan["endpoint_detail_plans"] = list(existing_details.values())
    for contract in _dict_items(updated_plan.get("api_contracts")):
        if str(contract.get("id") or "") != detail_plan.get("api_contract_id"):
            continue
        for endpoint_index, endpoint in enumerate(_dict_items(contract.get("endpoints"))):
            if _endpoint_identity(endpoint, endpoint_index) == detail_plan.get("endpoint_id"):
                endpoint["detail_status"] = detail_plan.get("status") or "confirmed"
                endpoint["detail_plan_id"] = detail_plan["id"]
                break
    updated_plan["endpoint_detail_confirmation_summary"] = {
        "confirmed_endpoints": len(updated_plan["endpoint_detail_plans"]),
        "latest_api_contract_id": detail_plan.get("api_contract_id"),
        "latest_endpoint_id": detail_plan.get("endpoint_id"),
    }
    return updated_plan


def create_page_detail_plan(
    project_plan: dict[str, Any],
    page_context: dict[str, Any],
    agent_note: str = "live main-agent page detail design",
    agent_detail_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pageId = page_context["pageId"]
    page = _find_page_by_pageId(project_plan_page_records(project_plan), pageId)
    page_name = str(page.get("name") or pageId)
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
    layout = page_context.get("layout", {})
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
                _find_page_by_pageId(
                    project_plan_page_records(project_plan), item["targetPageId"]
                ).get("path")
                or ""
            ),
        }
        for item in page_context.get("references", {}).get("navigation_targets", [])
        if item.get("targetPageId")
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
        "id": f"page_detail:{pageId}",
        "type": "page",
        "pageId": pageId,
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
        "permissions": page_context.get("references", {}).get("permissions", []),
        "endpoint_dependencies": page_context.get("references", {}).get("endpoint_dependencies", []),
        "navigation_targets": page_context.get("references", {}).get("navigation_targets", []),
        "response_bindings": response_bindings,
        "acceptance_criteria": [
            f"用户可以访问 {page_path} 并看到 {page_name} 的主要内容。",
            "页面具备 loading、empty、error、ready 四类基础状态。",
            "页面只访问项目计划中已声明的 API。",
            "页面权限与项目计划中的页面清单保持一致。",
        ],
        "agent_note": agent_note,
        "approved": True,
    }
    if not isinstance(detail_plan.get("basic_layout"), dict):
        detail_plan["basic_layout"] = {}
    detail_plan["basic_layout"] = _normalize_basic_layout(detail_plan["basic_layout"])
    for key in ("interactions", "permissions", "acceptance_criteria"):
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
            "id": f"page_detail:{pageId}",
            "type": "page",
            "pageId": pageId,
            "page_name": page_name,
            "path": page_path,
            "status": "confirmed",
            "confirmed_at": datetime.now(UTC).isoformat(),
            "source_page_context": page_context,
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
    """把页面详细设计回挂到菜单树中的目标页面叶子。"""

    updated_plan = deepcopy(project_plan)
    existing_details = {
        item["pageId"]: item
        for item in updated_plan.get("page_detail_plans", [])
        if isinstance(item, dict) and item.get("pageId")
    }
    existing_details[detail_plan["pageId"]] = detail_plan
    updated_plan["page_detail_plans"] = list(existing_details.values())
    page_field = "pages" if updated_plan.get("artifact_type") == "technical-plan" else "frontend_pages"
    updated_plan[page_field] = update_frontend_page_leaves(
        updated_plan.get(page_field),
        {
            detail_plan["pageId"]: {
                "detail_status": "confirmed",
                "detail_plan_id": detail_plan["id"],
            }
        },
    )

    updated_plan["detail_confirmation_summary"] = {
        "confirmed_pages": len(updated_plan["page_detail_plans"]),
        "total_pages": len(project_plan_page_records(updated_plan)),
        "latestPageId": detail_plan["pageId"],
    }
    return updated_plan
