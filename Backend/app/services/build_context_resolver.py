"""按页面或数据源定向加载 Build DAG 编译上下文。"""

from __future__ import annotations

from typing import Any


def resolve_target_build_context(
    project_plan: dict[str, Any],
    *,
    target_type: str,
    target_id: str,
) -> dict[str, Any]:
    """解析目标详情、直接 API/数据源依赖与编译所需的 Unit 标识。"""

    if target_type == "page":
        return _page_context(project_plan, target_id)
    if target_type == "data_source":
        return _data_source_context(project_plan, target_id)
    raise ValueError(f"Unsupported build target type: {target_type}.")


def _page_context(project_plan: dict[str, Any], page_id: str) -> dict[str, Any]:
    """只解析指定页面及其 endpoint 直接关联的数据源详情。"""

    page = _required_item(project_plan.get("frontend_pages"), "pageId", page_id, "page")
    page_detail = _required_detail(
        project_plan.get("page_detail_plans"),
        "pageId",
        page_id,
        "PageDetail",
    )
    _require_confirmed_detail(page.get("detail_design"), "PageDetail", page_id)
    endpoint_index = _endpoint_index(project_plan.get("api_contracts"))
    endpoint_ids = _endpoint_ids(page_detail)
    source_ids: list[str] = []
    for endpoint_id in endpoint_ids:
        endpoint = endpoint_index.get(endpoint_id)
        if endpoint is None:
            raise ValueError(f"Page {page_id} references unknown endpoint {endpoint_id}.")
        source_id = str(endpoint.get("data_source_id") or "")
        if not source_id:
            raise ValueError(f"Endpoint {endpoint_id} does not declare a data source.")
        if source_id not in source_ids:
            source_ids.append(source_id)

    data_source_details = []
    data_source_refs = []
    for source_id in source_ids:
        source = _required_item(project_plan.get("data_sources"), "id", source_id, "data source")
        detail = _required_detail(
            project_plan.get("data_source_detail_plans"),
            "data_source_id",
            source_id,
            "DataSourceDetail",
        )
        _require_confirmed_detail(source.get("detail_design"), "DataSourceDetail", source_id)
        data_source_details.append(detail)
        data_source_refs.append(_artifact_ref(source.get("detail_design"), source_id))

    return {
        "target": {"type": "page", "id": page_id},
        "page_detail": page_detail,
        "data_source_detail": None,
        "direct_data_source_details": data_source_details,
        "endpoint_ids": endpoint_ids,
        "data_source_ids": source_ids,
        "required_unit_ids": [
            "app:frontend-shell",
            "app:route-registry",
            "app:api-client",
            *( ["app:auth-guard"] if _page_requires_auth(page) else [] ),
            *( ["app:backend-bootstrap"] if source_ids else [] ),
            *(f"data-source:{source_id}" for source_id in source_ids),
            f"page:{page_id}",
        ],
        "source_refs": {
            "page_detail": _artifact_ref(page.get("detail_design"), page_id),
            "data_source_details": data_source_refs,
        },
    }


def _data_source_context(project_plan: dict[str, Any], source_id: str) -> dict[str, Any]:
    """只解析指定数据源及其确认详情，不反向加载页面详情。"""

    source = _required_item(project_plan.get("data_sources"), "id", source_id, "data source")
    detail = _required_detail(
        project_plan.get("data_source_detail_plans"),
        "data_source_id",
        source_id,
        "DataSourceDetail",
    )
    _require_confirmed_detail(source.get("detail_design"), "DataSourceDetail", source_id)
    endpoint_ids = [
        endpoint_id
        for endpoint_id, endpoint in _endpoint_index(project_plan.get("api_contracts")).items()
        if endpoint.get("data_source_id") == source_id
    ]
    return {
        "target": {"type": "data_source", "id": source_id},
        "page_detail": None,
        "data_source_detail": detail,
        "direct_data_source_details": [detail],
        "endpoint_ids": endpoint_ids,
        "data_source_ids": [source_id],
        "required_unit_ids": ["app:backend-bootstrap", f"data-source:{source_id}"],
        "source_refs": {
            "data_source_details": [_artifact_ref(source.get("detail_design"), source_id)],
        },
    }


def _required_item(value: Any, key: str, target_id: str, label: str) -> dict[str, Any]:
    """读取目标业务对象，缺失时返回可定位的构建前置错误。"""

    item = next(
        (
            candidate
            for candidate in _dict_items(value)
            if str(candidate.get(key) or "") == target_id
        ),
        None,
    )
    if item is None:
        raise ValueError(f"ProjectPlan does not contain {label} {target_id}.")
    return item


def _required_detail(value: Any, key: str, target_id: str, label: str) -> dict[str, Any]:
    """读取已水合的详情正文，避免为当前目标加载无关详情。"""

    detail = next(
        (
            candidate
            for candidate in _dict_items(value)
            if str(candidate.get(key) or "") == target_id
        ),
        None,
    )
    if detail is None:
        raise ValueError(f"Confirmed {label} is required for {target_id}.")
    return detail


def _require_confirmed_detail(reference: Any, label: str, target_id: str) -> None:
    """校验 ProjectPlan 详情引用已确认且可供代码生成使用。"""

    detail_ref = reference if isinstance(reference, dict) else {}
    if detail_ref.get("status") != "confirmed":
        raise ValueError(f"{label} {target_id} is not confirmed.")


def _endpoint_ids(page_detail: dict[str, Any]) -> list[str]:
    """从页面详情持久化引用中提取稳定且去重的 endpoint 标识。"""

    references = page_detail.get("references") if isinstance(page_detail.get("references"), dict) else {}
    dependencies = page_detail.get("endpoint_dependencies") or references.get("endpoint_dependencies") or []
    result: list[str] = []
    for dependency in _dict_items(dependencies):
        endpoint_id = str(dependency.get("endpoint_id") or "")
        if endpoint_id and endpoint_id not in result:
            result.append(endpoint_id)
    return result


def _endpoint_index(value: Any) -> dict[str, dict[str, Any]]:
    """建立 endpoint 到数据源和契约的只读反向索引。"""

    return {
        str(endpoint.get("id")): {
            "data_source_id": str(contract.get("data_source_id") or ""),
            "api_contract_id": str(contract.get("id") or ""),
        }
        for contract in _dict_items(value)
        for endpoint in _dict_items(contract.get("endpoints"))
        if endpoint.get("id")
    }


def _artifact_ref(reference: Any, target_id: str) -> dict[str, Any]:
    """投射详情 artifact 的稳定路径、哈希和业务标识。"""

    detail_ref = reference if isinstance(reference, dict) else {}
    return {
        "id": target_id,
        "json_path": detail_ref.get("json_path"),
        "sha256": detail_ref.get("sha256"),
    }


def _page_requires_auth(page: dict[str, Any]) -> bool:
    """根据页面权限引用判断当前页面构建是否需要鉴权公共能力。"""

    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    permissions = references.get("permissions") or page.get("permissions") or []
    return bool(permissions) and list(permissions) != ["anonymous"]


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """只保留列表中的字典项，统一处理不可信外部结构。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
