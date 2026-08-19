"""ProjectPlan 页面依赖的归一化、校验与页面设计投射。"""

from __future__ import annotations

from typing import Any

from app.services.api_contracts import dict_items, endpoint_dependencies_for_contracts
from app.services.frontend_page_tree import (
    find_frontend_page,
    frontend_page_menu_paths,
    is_menu_node,
    is_page_leaf,
    project_plan_page_records,
)


def normalize_page_dependencies(
    pages: list[dict[str, Any]],
    api_contracts: list[dict[str, Any]],
    *,
    include_action_implementations: bool = False,
) -> list[dict[str, Any]]:
    """为每个页面生成唯一的 references 依赖容器。"""

    endpoint_index = _endpoint_index(api_contracts)
    pageIds = {str(page.get("pageId") or "") for page in pages}
    normalized: list[dict[str, Any]] = []
    for page in pages:
        endpoint_dependencies = _normalize_endpoint_dependencies(
            page,
            endpoint_index=endpoint_index,
            api_contracts=api_contracts,
        )
        navigation_targets = _normalize_navigation_targets(
            _references_value(page, "navigation_targets"), pageIds=pageIds
        )
        references = {
            "permissions": _string_items(_references_value(page, "permissions")),
            "endpoint_dependencies": [dict(item) for item in endpoint_dependencies],
            "navigation_targets": [dict(item) for item in navigation_targets],
        }
        if include_action_implementations:
            references["action_implementations"] = [
                dict(item)
                for item in dict_items(_references_value(page, "action_implementations"))
            ]
        normalized.append(
            {
                "pageId": str(page.get("pageId") or ""),
                "name": str(page.get("name") or page.get("pageId") or "页面"),
                "path": str(page.get("path") or "/"),
                "module_id": str(page.get("module_id") or "core"),
                "description": str(page.get("description") or page.get("name") or "业务页面"),
                "references": references,
            }
        )
    return normalized


def validate_project_plan_dependencies(project_plan: dict[str, Any]) -> list[str]:
    """校验页面 id、路由、endpoint 与跳转均可由 ProjectPlan 独立解析。"""

    pages = project_plan_page_records(project_plan)
    contracts = dict_items(project_plan.get("api_contracts"))
    endpoint_index = _endpoint_index(contracts)
    errors: list[str] = []
    _validate_unique_values(pages, "pageId", "pageId", errors)
    _validate_unique_values(pages, "path", "page path", errors)
    page_source = (
        project_plan.get("pages", [])
        if project_plan.get("artifact_type") == "technical-plan"
        else project_plan.get("frontend_pages")
    )
    menu_paths = frontend_page_menu_paths(page_source)
    duplicates = sorted(
        {value for value in menu_paths if value and menu_paths.count(value) > 1}
    )
    for value in duplicates:
        errors.append(f"Duplicate menu unique_path: {value}.")
    _validate_menu_page_path_conflicts(page_source, errors)
    pageIds = {str(page.get("pageId") or "") for page in pages}
    for page in pages:
        pageId = str(page.get("pageId") or "")
        references = page.get("references") if isinstance(page.get("references"), dict) else {}
        for dependency in dict_items(references.get("endpoint_dependencies") or page.get("endpoint_dependencies")):
            endpoint_id = str(dependency.get("endpoint_id") or "")
            if not endpoint_id:
                errors.append(
                    f"Page {pageId} contains an endpoint dependency without endpoint_id."
                )
            elif endpoint_id not in endpoint_index:
                errors.append(
                    f"Page {pageId} references unknown endpoint {endpoint_id}."
                )
        for target in dict_items(references.get("navigation_targets") or page.get("navigation_targets")):
            targetPageId = str(target.get("targetPageId") or "")
            if not targetPageId or targetPageId not in pageIds:
                errors.append(
                    f"Page {pageId} references unknown navigation target {targetPageId}."
                )
    return errors


def _validate_menu_page_path_conflicts(value: Any, errors: list[str]) -> None:
    """校验菜单路由没有与直接页面叶子共用同一个最终路由。"""

    for node in dict_items(value):
        if not is_menu_node(node):
            continue
        unique_path = str(node.get("unique_path") or "").strip()
        if unique_path:
            for child in dict_items(node.get("children")):
                if not is_page_leaf(child):
                    continue
                page_path = str(child.get("path") or "").strip()
                if page_path == unique_path:
                    errors.append(
                        "Menu unique_path conflicts with direct page path: "
                        f"{unique_path}."
                    )
        _validate_menu_page_path_conflicts(node.get("children"), errors)


def page_design_references(
    project_plan: dict[str, Any], pageId: str
) -> dict[str, Any]:
    """从 ProjectPlan 原样复制页面设计允许使用的全部引用型依赖。"""

    page = find_frontend_page(project_plan_page_records(project_plan), pageId)
    if page is None:
        raise ValueError(f"项目计划中不存在页面：{pageId}")
    references = (
        page.get("references") if isinstance(page.get("references"), dict) else {}
    )
    return {
        "permissions": list(
            references.get("permissions") or page.get("permissions") or []
        ),
        "endpoint_dependencies": [
            dict(item)
            for item in dict_items(
                references.get("endpoint_dependencies")
                or page.get("endpoint_dependencies")
            )
        ],
        "navigation_targets": [
            dict(item)
            for item in dict_items(
                references.get("navigation_targets") or page.get("navigation_targets")
            )
        ],
    }


def _references_value(page: dict[str, Any], key: str) -> Any:
    """优先读取 references；规划生成阶段尚未归一化时读取同源根字段。"""

    references = page.get("references")
    return references.get(key) if isinstance(references, dict) and key in references else page.get(key)


def _string_items(value: Any) -> list[str]:
    """标准化字符串数组并移除空值。"""

    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _endpoint_index(api_contracts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """建立 endpoint 到所属契约和数据源的确定性反向索引。"""

    return {
        str(endpoint.get("id")): {
            "api_contract_id": str(contract.get("id") or ""),
            "endpoint": endpoint,
        }
        for contract in api_contracts
        for endpoint in dict_items(contract.get("endpoints"))
        if endpoint.get("id")
    }


def _normalize_endpoint_dependencies(
    page: dict[str, Any],
    *,
    endpoint_index: dict[str, dict[str, Any]],
    api_contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """接受规划模型的 endpoint 引用；缺失时仅按页面数据源生成保守读取默认值。"""

    supplied = dict_items(_references_value(page, "endpoint_dependencies"))
    if not supplied:
        supplied = endpoint_dependencies_for_contracts(
            api_contracts,
            [str(item) for item in page.get("data_dependencies", [])],
            page_path=str(page.get("path") or ""),
            page_name=str(page.get("name") or ""),
        )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in supplied:
        endpoint_id = str(item.get("endpoint_id") or "")
        if not endpoint_id or endpoint_id in seen:
            continue
        seen.add(endpoint_id)
        result.append(
            {
                "endpoint_id": endpoint_id,
                "usage": str(item.get("usage") or "read"),
                "trigger": str(item.get("trigger") or "页面交互触发"),
                "required_for_initial_load": bool(
                    item.get("required_for_initial_load", item.get("required", False))
                ),
            }
        )
    return result


def _normalize_navigation_targets(
    value: Any, *, pageIds: set[str]
) -> list[dict[str, Any]]:
    """规范页面跳转引用，忽略模型未声明 targetPageId 的自由文本。"""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in dict_items(value):
        targetPageId = str(item.get("targetPageId") or "")
        if not targetPageId or targetPageId in seen:
            continue
        seen.add(targetPageId)
        result.append(
            {
                "targetPageId": targetPageId,
                "trigger": str(item.get("trigger") or "页面跳转"),
            }
        )
    return result


def _validate_unique_values(
    pages: list[dict[str, Any]],
    field: str,
    label: str,
    errors: list[str],
) -> None:
    """校验页面标识或路由非空且全局唯一。"""

    values = [str(page.get(field) or "") for page in pages]
    if any(not value for value in values):
        errors.append(f"Every {label} must be non-empty.")
    duplicates = sorted(
        {value for value in values if value and values.count(value) > 1}
    )
    for value in duplicates:
        errors.append(f"Duplicate {label}: {value}.")
