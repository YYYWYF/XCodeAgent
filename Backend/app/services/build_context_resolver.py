"""按页面或后端数据单元定向加载 Build DAG 编译上下文。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.services.entity_definitions import (
    confirmed_entity_designs,
    entity_design_source_type,
    entity_design_summaries,
    missing_entity_design_ids,
    plan_data_sources,
)
from app.services.entity_design import (
    entity_design_endpoint_binding_errors,
    entity_design_validation_errors,
)
from app.services.frontend_page_tree import find_frontend_page, project_plan_page_records


def _endpoint_contract(
    project_plan: dict[str, Any],
    endpoint: dict[str, Any],
) -> dict[str, Any]:
    """读取 endpoint 所属的 API 契约，缺失时返回空对象。"""

    contract_id = str(endpoint.get("api_contract_id") or "")
    return next(
        (
            item
            for item in _dict_items(project_plan.get("api_contracts"))
            if str(item.get("id") or "") == contract_id
        ),
        {},
    )


def _endpoint_entity_designs(
    project_plan: dict[str, Any],
    endpoint: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """读取 endpoint 所属契约的已确认实体设计，并返回缺失设计清单。"""

    contract = _endpoint_contract(project_plan, endpoint)
    confirmed = confirmed_entity_designs(project_plan, contract)
    invalid_ids = [
        str(detail.get("entity_id") or "")
        for detail in confirmed
        if entity_design_validation_errors(project_plan, detail)
        or entity_design_endpoint_binding_errors(
            project_plan,
            detail,
            api_contract_id=str(endpoint.get("api_contract_id") or ""),
            endpoint_id=str(endpoint.get("id") or ""),
        )
    ]
    missing_ids = missing_entity_design_ids(project_plan, contract)
    return (
        [
            detail
            for detail in confirmed
            if str(detail.get("entity_id") or "") not in invalid_ids
        ],
        list(dict.fromkeys([*missing_ids, *invalid_ids])),
    )


def _entity_design_source_types(entity_designs: list[dict[str, Any]]) -> list[str]:
    """按已确认实体设计提取有序去重的数据源类型集合。"""

    result: list[str] = []
    for detail in entity_designs:
        source_type = entity_design_source_type(detail)
        if source_type and source_type not in result:
            result.append(source_type)
    return result


def _assert_endpoint_entities_designed(
    endpoint_id: str,
    entity_designs: list[dict[str, Any]],
    missing_entity_ids: list[str],
) -> None:
    """接口绑定实体为空或存在未确认实体设计时，给出可定位的构建前置错误。"""

    if missing_entity_ids:
        raise ValueError(
            f"Endpoint {endpoint_id} 绑定实体 "
            f"{', '.join(missing_entity_ids)} 缺少已确认实体设计。"
        )
    if not entity_designs:
        raise ValueError(f"Endpoint {endpoint_id} 未绑定任何实体。")


def _page_key_from_page_id(page_id: str) -> str:
    """将 snake_case 的 pageId 转换为 PascalCase 的 PageKey。

    与前端 templateApi.ts 的 pageKeyFromPageId 保持一致：
    按 _ / - / 空格分段，每段首字母大写后拼接，保留所有段（含 "page" 后缀）。
    例：dashboard_page → DashboardPage，order_list_page → OrderListPage。
    """
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(page_id or "page")).strip("-")
    segments = [s for s in re.split(r"[-_\s]+", cleaned) if s]
    if not segments:
        return "Page"
    pascal = "".join(seg[:1].upper() + seg[1:].lower() for seg in segments)
    # 确保以字母开头
    if not pascal[:1].isalpha():
        pascal = "Page" + pascal
    return pascal


def resolve_target_build_context(
    project_plan: dict[str, Any],
    *,
    target_type: str,
    target_id: str,
    api_contract_id: str | None = None,
    project_plan_path: str | Path | None = None,
) -> dict[str, Any]:
    """解析目标详情、直接 endpoint/API 依赖与编译所需的 Unit 标识。"""

    if target_type == "page":
        return _page_context(project_plan, target_id, project_plan_path)
    if target_type == "endpoint":
        return _endpoint_context(project_plan, target_id, api_contract_id, project_plan_path)
    raise ValueError(f"Unsupported build target type: {target_type}.")


def _page_context(
    project_plan: dict[str, Any],
    page_id: str,
    project_plan_path: str | Path | None,
) -> dict[str, Any]:
    """解析页面实现契约及其 TechnicalPlan Endpoint，并按实体绑定限定 Unit 范围。"""

    page = find_frontend_page(project_plan_page_records(project_plan), page_id)
    if page is None:
        raise ValueError(f"ProjectPlan does not contain page {page_id}.")
    page_contract = _page_implementation_contract(
        project_plan,
        page_id,
        project_plan_path,
    )
    endpoint_index = _endpoint_index(project_plan.get("api_contracts"))
    endpoint_ids = _contract_endpoint_ids(page_contract)
    entity_ids: list[str] = []
    source_types: list[str] = []
    endpoint_unit_ids: list[str] = []
    for endpoint_id in endpoint_ids:
        endpoint = endpoint_index.get(endpoint_id)
        if endpoint is None:
            raise ValueError(f"Page {page_id} references unknown endpoint {endpoint_id}.")
        entity_designs, missing_entity_ids = _endpoint_entity_designs(project_plan, endpoint)
        _assert_endpoint_entities_designed(endpoint_id, entity_designs, missing_entity_ids)
        for entity_design in entity_designs:
            entity_id = str(entity_design.get("entity_id") or "")
            if entity_id and entity_id not in entity_ids:
                entity_ids.append(entity_id)
        endpoint_source_types = _entity_design_source_types(entity_designs)
        for source_type in endpoint_source_types:
            if source_type not in source_types:
                source_types.append(source_type)
        contract_id = str(endpoint.get("api_contract_id") or "")
        # 仅非纯 static 的 endpoint 挂后端 Unit；static 由 frontend:data:static 承载。
        if contract_id and not (
            endpoint_source_types and set(endpoint_source_types) <= {"static"}
        ):
            endpoint_unit_ids.append(_endpoint_unit_id(contract_id, endpoint_id))

    endpoint_contracts = [endpoint_index[endpoint_id] for endpoint_id in endpoint_ids]

    all_static = bool(source_types) and set(source_types) <= {"static"}
    return {
        "target": {
            "type": "page",
            "id": page_id,
            "page_key": _page_key_from_page_id(page_id),
        },
        "page_implementation_contract": page_contract,
        "endpoint_contract": None,
        "direct_endpoint_contracts": endpoint_contracts,
        "endpoint_ids": endpoint_ids,
        "required_endpoint_ids": endpoint_ids,
        "entity_ids": entity_ids,
        "entity_designs": entity_design_summaries(
            project_plan,
            entity_ids,
            {
                (str(item.get("api_contract_id") or ""), str(item.get("id") or ""))
                for item in endpoint_contracts
            },
        ),
        "required_unit_ids": [
            "frontend:shell",
            *(["frontend:api-client"] if not all_static else []),
            *(["frontend:auth-guard"] if _page_requires_auth(page) else []),
            *(
                ["backend:bootstrap"]
                if set(source_types) & {"database", "external_api"}
                else []
            ),
            *(["frontend:data:static"] if "static" in source_types else []),
            *(list(dict.fromkeys(endpoint_unit_ids)) if not all_static else []),
            f"page:{page_id}",
        ],
        "source_refs": {
            "page_implementation_contract": {
                "id": page_id,
                "ui_design_path": (
                    page_contract.get("uiDesignRef", {}).get("path")
                    if isinstance(page_contract.get("uiDesignRef"), dict)
                    else None
                ),
                "ui_design_sha256": (
                    page_contract.get("uiDesignRef", {}).get("sha256")
                    if isinstance(page_contract.get("uiDesignRef"), dict)
                    else None
                ),
            },
            "technical_plan_endpoints": [
                {
                    "id": endpoint_id,
                    "api_contract_id": endpoint_index[endpoint_id].get("api_contract_id"),
                }
                for endpoint_id in endpoint_ids
            ],
        },
    }


def _endpoint_context(
    project_plan: dict[str, Any],
    endpoint_id: str,
    api_contract_id: str | None,
    project_plan_path: str | Path | None,
) -> dict[str, Any]:
    """解析单个 TechnicalPlan Endpoint，只暴露绑定实体与必要 Unit。"""

    endpoint_index = _endpoint_index(project_plan.get("api_contracts"))
    contract_id = str(api_contract_id or "").strip()
    endpoint = (
        endpoint_index.get(f"{contract_id}\0{endpoint_id}")
        if contract_id
        else endpoint_index.get(endpoint_id)
    )
    if endpoint is None:
        target_label = f"{contract_id}/{endpoint_id}" if contract_id else endpoint_id
        raise ValueError(f"ProjectPlan does not contain endpoint {target_label}.")
    contract_id = str(endpoint.get("api_contract_id") or "")
    if not contract_id:
        raise ValueError(f"Endpoint {endpoint_id} does not declare an API contract.")
    entity_designs, missing_entity_ids = _endpoint_entity_designs(project_plan, endpoint)
    _assert_endpoint_entities_designed(endpoint_id, entity_designs, missing_entity_ids)
    source_types = _entity_design_source_types(entity_designs)
    if not source_types:
        raise ValueError(f"Endpoint {endpoint_id} 绑定实体未声明数据源类型。")
    entity_ids = [str(item.get("entity_id") or "") for item in entity_designs]
    uses_static = "static" in source_types
    uses_backend = bool(set(source_types) & {"database", "external_api"})
    required_unit_ids = []
    if uses_backend:
        required_unit_ids.append("backend:bootstrap")
    if uses_backend:
        required_unit_ids.append(_endpoint_unit_id(contract_id, endpoint_id))
    if uses_static:
        required_unit_ids.append("frontend:data:static")
    return {
        "target": {
            "type": "endpoint",
            "id": endpoint_id,
            "api_contract_id": contract_id,
        },
        "endpoint_contract": endpoint,
        "direct_endpoint_contracts": [endpoint],
        "endpoint_ids": [endpoint_id],
        "required_endpoint_ids": [endpoint_id],
        "entity_ids": entity_ids,
        "entity_designs": entity_design_summaries(
            project_plan,
            entity_ids,
            {(contract_id, endpoint_id)},
        ),
        "required_unit_ids": required_unit_ids,
        "source_refs": {
            "technical_plan_endpoint": {
                "id": endpoint_id,
                "api_contract_id": contract_id,
            },
            "technical_plan_endpoints": [
                {"id": endpoint_id, "api_contract_id": contract_id}
            ],
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


def _source_type(source: dict[str, Any]) -> str:
    """读取正式数据源类型，并拒绝旧 mock 或缺失类型进入构建链路。"""

    source_type = str(source.get("type") or "")
    if source_type not in {"database", "static", "external_api"}:
        raise ValueError("ProjectPlan data source type must be database, static, or external_api.")
    return source_type












def _page_implementation_contract(
    project_plan: dict[str, Any],
    page_id: str,
    project_plan_path: str | Path | None,
) -> dict[str, Any]:
    """读取当前页面的正式 PageImplementationContract。"""

    for contract in _dict_items(project_plan.get("page_implementation_contracts")):
        if str(contract.get("pageId") or "") == page_id:
            return contract
    raise ValueError(f"TechnicalPlan does not contain PageImplementationContract {page_id}.")


def _contract_endpoint_ids(contract: dict[str, Any]) -> list[str]:
    """从 PageImplementationContract 提取去重 endpoint 标识。"""

    result = []
    for endpoint_id in contract.get("requiredEndpointIds") or []:
        normalized = str(endpoint_id or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _endpoint_index(value: Any) -> dict[str, dict[str, Any]]:
    """建立 endpoint 到 TechnicalPlan 完整接口契约的只读反向索引。"""

    index: dict[str, dict[str, Any]] = {}
    for contract in _dict_items(value):
        contract_id = str(contract.get("id") or "")
        for endpoint_index, endpoint in enumerate(_dict_items(contract.get("endpoints"))):
            endpoint_id = str(endpoint.get("id") or endpoint_index + 1)
            indexed_endpoint = {**endpoint, "api_contract_id": contract_id}
            index.setdefault(endpoint_id, indexed_endpoint)
            if contract_id:
                index[f"{contract_id}\0{endpoint_id}"] = indexed_endpoint
    return index


def _endpoint_unit_id(api_contract_id: str, endpoint_id: str) -> str:
    """生成 endpoint Unit 的稳定复合标识，避免不同契约下接口 ID 冲突。"""

    return f"backend:endpoint:{api_contract_id}:{endpoint_id}"




def _page_requires_auth(page: dict[str, Any]) -> bool:
    """根据页面权限引用判断当前页面构建是否需要鉴权公共能力。"""

    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    permissions = references.get("permissions") or page.get("permissions") or []
    return bool(permissions) and list(permissions) != ["anonymous"]


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """只保留列表中的字典项，统一处理不可信外部结构。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
