"""页面或接口进入任务拆解前的确定性开发就绪检查。"""

from __future__ import annotations

from typing import Any

from app.services.entity_definitions import missing_entity_design_ids
from app.services.entity_design import (
    entity_design_endpoint_binding_errors,
    entity_design_validation_errors,
)
from app.services.frontend_page_tree import project_plan_page_records


def development_readiness(
    project_plan: dict[str, Any],
    *,
    target_type: str,
    target_id: str,
    api_contract_id: str | None = None,
) -> dict[str, Any]:
    """检查目标引用的实体是否都已完成独立 EntitySourceBinding。"""

    contracts = _target_contracts(
        project_plan,
        target_type=target_type,
        target_id=target_id,
        api_contract_id=api_contract_id,
    )
    missing_ids: list[str] = []
    for contract in contracts:
        for entity_id in missing_entity_design_ids(project_plan, contract):
            if entity_id not in missing_ids:
                missing_ids.append(entity_id)
        for entity_id in contract.get("entity_ids") or []:
            normalized_id = str(entity_id).strip()
            detail = next(
                (
                    item
                    for item in _dict_items(project_plan.get("entity_detail_plans"))
                    if str(item.get("entity_id") or "") == normalized_id
                    and str(item.get("status") or "") == "confirmed"
                ),
                None,
            )
            if (
                normalized_id
                and isinstance(detail, dict)
                and entity_design_validation_errors(project_plan, detail)
                and normalized_id not in missing_ids
            ):
                missing_ids.append(normalized_id)
                continue
            if normalized_id and isinstance(detail, dict):
                endpoint_binding_errors = [
                    error
                    for endpoint in _dict_items(contract.get("endpoints"))
                    for error in entity_design_endpoint_binding_errors(
                        project_plan,
                        detail,
                        api_contract_id=str(contract.get("id") or ""),
                        endpoint_id=str(endpoint.get("id") or ""),
                    )
                ]
                if endpoint_binding_errors and normalized_id not in missing_ids:
                    missing_ids.append(normalized_id)
    names = {
        str(entity.get("id") or ""): str(entity.get("name") or entity.get("id") or "")
        for entity in _dict_items(project_plan.get("entities"))
    }
    return {
        "ready": not missing_ids,
        "target_type": target_type,
        "target_id": target_id,
        "api_contract_id": api_contract_id,
        "endpoint_ids": [
            str(endpoint.get("id") or "")
            for contract in contracts
            for endpoint in _dict_items(contract.get("endpoints"))
            if str(endpoint.get("id") or "").strip()
        ],
        "missing_entities": [
            {"entity_id": entity_id, "entity_name": names.get(entity_id) or entity_id}
            for entity_id in missing_ids
        ],
    }


def _target_contracts(
    project_plan: dict[str, Any],
    *,
    target_type: str,
    target_id: str,
    api_contract_id: str | None,
) -> list[dict[str, Any]]:
    """解析页面/API 目标对应的 TechnicalPlan API Contract 切片。"""

    contracts = _dict_items(project_plan.get("api_contracts"))
    if target_type == "endpoint":
        matches = [
            contract
            for contract in contracts
            if (not api_contract_id or str(contract.get("id") or "") == api_contract_id)
            and any(
                str(endpoint.get("id") or "") == target_id
                for endpoint in _dict_items(contract.get("endpoints"))
            )
        ]
        if len(matches) != 1:
            raise ValueError(f"TechnicalPlan 无法唯一定位 Endpoint：{api_contract_id or '*'}:{target_id}。")
        return [_scoped_contract(matches[0], {target_id})]
    if target_type != "page":
        raise ValueError(f"不支持的开发目标类型：{target_type}。")
    page = next(
        (
            item
            for item in project_plan_page_records(project_plan)
            if str(item.get("pageId") or item.get("id") or "") == target_id
        ),
        None,
    )
    if page is None:
        raise ValueError(f"TechnicalPlan 不包含页面：{target_id}。")
    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    endpoint_ids = {
        str(item.get("endpoint_id") or "")
        for item in _dict_items(references.get("endpoint_dependencies"))
        if str(item.get("endpoint_id") or "").strip()
    }
    return [
        _scoped_contract(contract, endpoint_ids)
        for contract in contracts
        if any(
            str(endpoint.get("id") or "") in endpoint_ids
            for endpoint in _dict_items(contract.get("endpoints"))
        )
    ]


def _scoped_contract(contract: dict[str, Any], endpoint_ids: set[str]) -> dict[str, Any]:
    """保留目标接口并维持 Contract 级实体绑定。"""

    return {
        **contract,
        "endpoints": [
            endpoint
            for endpoint in _dict_items(contract.get("endpoints"))
            if str(endpoint.get("id") or "") in endpoint_ids
        ],
    }


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """过滤列表中的非字典输入。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
