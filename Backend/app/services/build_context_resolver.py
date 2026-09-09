"""按页面或后端数据单元定向加载 Build DAG 编译上下文。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.services.api_design import (
    api_design_business_descriptions,
    api_design_mapping_flows,
    api_design_source_types,
    load_confirmed_endpoint_designs,
)
from app.services.frontend_page_tree import find_frontend_page, project_plan_page_records
from app.services.template_scaffold_injection import prebuilt_files_for_plan


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


def _workspace_root(project_plan_path: str | Path | None) -> Path:
    """从 TechnicalPlan JSON 路径解析工作区根目录。"""

    if project_plan_path is None:
        raise ValueError("Build 上下文缺少 technical-plan.json 路径。")
    path = Path(project_plan_path).expanduser().resolve()
    if path.name != "technical-plan.json" or path.parent.name != "plans":
        raise ValueError("Build 上下文必须使用规范的 technical-plan.json 路径。")
    return path.parent.parent.parent


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
        context = _page_context(project_plan, target_id, project_plan_path)
    elif target_type == "endpoint":
        context = _endpoint_context(project_plan, target_id, api_contract_id, project_plan_path)
    else:
        raise ValueError(f"Unsupported build target type: {target_type}.")
    # 把平台预置的后端骨架文件清单传给 Agent，让它知道哪些文件已存在、只需补业务逻辑。
    context["prebuilt_files"] = prebuilt_files_for_plan(project_plan)
    return context


def _page_context(
    project_plan: dict[str, Any],
    page_id: str,
    project_plan_path: str | Path | None,
) -> dict[str, Any]:
    """解析页面实现契约、TechnicalPlan Endpoint 与已确认 API 设计。"""

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
    workspace_root = _workspace_root(project_plan_path)
    endpoint_unit_ids: list[str] = []
    for endpoint_id in endpoint_ids:
        endpoint = endpoint_index.get(endpoint_id)
        if endpoint is None:
            raise ValueError(f"Page {page_id} references unknown endpoint {endpoint_id}.")
        contract_id = str(endpoint.get("api_contract_id") or "")
        if contract_id:
            endpoint_unit_ids.append(_endpoint_unit_id(contract_id, endpoint_id))

    endpoint_contracts = [endpoint_index[endpoint_id] for endpoint_id in endpoint_ids]
    endpoint_designs = load_confirmed_endpoint_designs(
        workspace_root,
        project_plan,
        endpoint_ids,
    )
    entity_ids = _technical_plan_entity_ids(project_plan, endpoint_ids)
    source_types = api_design_source_types(endpoint_designs)
    mapping_flows = api_design_mapping_flows(endpoint_designs)
    business_descriptions = api_design_business_descriptions(endpoint_designs)
    required_unit_ids = ["frontend:shell"]
    if endpoint_ids:
        required_unit_ids.append("frontend:api-client")
    if _page_requires_auth(page):
        required_unit_ids.append("frontend:auth-guard")
    if endpoint_ids:
        required_unit_ids.extend(
            ["backend:bootstrap", *list(dict.fromkeys(endpoint_unit_ids))]
        )
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
        "endpoint_designs": endpoint_designs,
        "mapping_flows": mapping_flows,
        "business_descriptions": business_descriptions,
        "source_types": source_types,
        "required_unit_ids": [*required_unit_ids, f"page:{page_id}"],
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
            "endpoint_designs": endpoint_designs,
            "mapping_flows": mapping_flows,
            "business_descriptions": business_descriptions,
        },
    }


def _endpoint_context(
    project_plan: dict[str, Any],
    endpoint_id: str,
    api_contract_id: str | None,
    project_plan_path: str | Path | None,
) -> dict[str, Any]:
    """解析单个 TechnicalPlan Endpoint，只暴露其局部 API 设计与必要 Unit。"""

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
    endpoint_designs = load_confirmed_endpoint_designs(
        _workspace_root(project_plan_path),
        project_plan,
        [endpoint_id],
        api_contract_id=contract_id,
    )
    source_types = api_design_source_types(endpoint_designs)
    entity_ids = _technical_plan_entity_ids(
        project_plan,
        [endpoint_id],
        api_contract_ids=[contract_id],
    )
    mapping_flows = api_design_mapping_flows(endpoint_designs)
    business_descriptions = api_design_business_descriptions(endpoint_designs)
    required_unit_ids = ["backend:bootstrap", _endpoint_unit_id(contract_id, endpoint_id)]
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
        "endpoint_designs": endpoint_designs,
        "mapping_flows": mapping_flows,
        "business_descriptions": business_descriptions,
        "source_types": source_types,
        "required_unit_ids": required_unit_ids,
        "source_refs": {
            "technical_plan_endpoint": {
                "id": endpoint_id,
                "api_contract_id": contract_id,
            },
            "technical_plan_endpoints": [
                {"id": endpoint_id, "api_contract_id": contract_id}
            ],
            "endpoint_designs": endpoint_designs,
            "mapping_flows": mapping_flows,
            "business_descriptions": business_descriptions,
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


def _technical_plan_entity_ids(
    project_plan: dict[str, Any],
    endpoint_ids: list[str],
    *,
    api_contract_ids: list[str] | None = None,
) -> list[str]:
    """从 TechnicalPlan Contract 独立读取业务 Entity ID，不依赖物理来源映射。"""

    target_endpoint_ids = {str(item).strip() for item in endpoint_ids if str(item).strip()}
    target_contract_ids = {
        str(item).strip() for item in (api_contract_ids or []) if str(item).strip()
    }
    if not target_endpoint_ids:
        return []
    result: list[str] = []
    for contract in _dict_items(project_plan.get("api_contracts")):
        contract_id = str(contract.get("id") or "")
        contract_endpoints = {
            str(endpoint.get("id") or "")
            for endpoint in _dict_items(contract.get("endpoints"))
        }
        if target_contract_ids and contract_id not in target_contract_ids:
            continue
        if not (contract_endpoints & target_endpoint_ids):
            continue
        for entity_id in contract.get("entity_ids") or []:
            normalized = str(entity_id).strip()
            if normalized and normalized not in result:
                result.append(normalized)
    return result




def _page_requires_auth(page: dict[str, Any]) -> bool:
    """根据页面权限引用判断当前页面构建是否需要鉴权公共能力。"""

    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    permissions = references.get("permissions") or page.get("permissions") or []
    return bool(permissions) and list(permissions) != ["anonymous"]


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """只保留列表中的字典项，统一处理不可信外部结构。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
