"""从正式 ProjectPlan 解析计划执行需要独占的业务资源。"""

from __future__ import annotations

from typing import Any

from app.domain.application_lifecycle import (
    ExecutionResourceClaim,
    ExecutionResourceReason,
    ExecutionResourceRole,
    ExecutionResourceType,
)
from app.services.entity_definitions import contract_data_source_id
from app.services.frontend_page_tree import find_frontend_page, project_plan_page_records


def resolve_execution_resource_claims(
    project_plan: dict[str, Any] | None,
    execution_scope: dict[str, str] | None,
) -> list[ExecutionResourceClaim]:
    """由服务端正式计划计算主目标及直接依赖，拒绝依赖客户端自报锁集合。"""

    scope = execution_scope if isinstance(execution_scope, dict) else {}
    target_type = str(scope.get("type") or "application")
    target_id = str(scope.get("targetId") or "application")
    if target_type == "application":
        return [_claim(ExecutionResourceType.APPLICATION, "application", primary=True)]

    plan = project_plan if isinstance(project_plan, dict) else {}
    if target_type == "page":
        return _page_claims(plan, target_id)
    if target_type == "data_source":
        return _data_source_claims(plan, target_id)
    if target_type == "endpoint":
        return _endpoint_claims(
            plan,
            target_id,
            str(scope.get("apiContractId") or scope.get("api_contract_id") or "").strip(),
        )
    raise ValueError(f"不支持的计划执行资源范围：{target_type}。")


def resource_claim_keys(claims: list[ExecutionResourceClaim]) -> tuple[str, ...]:
    """把资源声明转换为可用于租约求交集的稳定键集合。"""

    return tuple(dict.fromkeys(f"{claim.type.value}:{claim.target_id}" for claim in claims))


def _page_claims(
    project_plan: dict[str, Any],
    page_id: str,
) -> list[ExecutionResourceClaim]:
    """解析页面本身、跳转关联页、API 契约及其数据源。"""

    page = find_frontend_page(project_plan_page_records(project_plan), page_id)
    if page is None:
        return [_claim(ExecutionResourceType.PAGE, page_id, primary=True)]

    claims = [_claim(ExecutionResourceType.PAGE, page_id, primary=True)]
    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    for target in _dict_items(
        references.get("navigation_targets") or page.get("navigation_targets")
    ):
        related_page_id = str(target.get("targetPageId") or "").strip()
        if related_page_id and related_page_id != page_id:
            claims.append(_claim(ExecutionResourceType.PAGE, related_page_id))

    endpoint_ids = {
        str(item.get("endpoint_id") or "").strip()
        for item in _dict_items(
            references.get("endpoint_dependencies") or page.get("endpoint_dependencies")
        )
    }
    endpoint_ids.discard("")
    contracts = _dict_items(project_plan.get("api_contracts"))
    source_ids = {
        contract_data_source_id(project_plan, contract).strip()
        for contract in contracts
        if any(
            str(endpoint.get("id") or "") in endpoint_ids
            for endpoint in _dict_items(contract.get("endpoints"))
        )
    }
    source_ids.discard("")
    related_endpoint_ids: set[str] = set()
    for contract in contracts:
        contract_endpoint_ids = {
            str(endpoint.get("id") or "").strip()
            for endpoint in _dict_items(contract.get("endpoints"))
        }
        direct_contract = bool(contract_endpoint_ids.intersection(endpoint_ids))
        shared_source_contract = (
            contract_data_source_id(project_plan, contract) in source_ids
        )
        if not direct_contract and not shared_source_contract:
            continue
        contract_id = str(contract.get("id") or "").strip()
        source_id = contract_data_source_id(project_plan, contract).strip()
        if contract_id:
            claims.append(_claim(ExecutionResourceType.API_CONTRACT, contract_id))
        if source_id:
            claims.append(_claim(ExecutionResourceType.DATA_SOURCE, source_id))
        related_endpoint_ids.update(contract_endpoint_ids)

    # 共享 API 或数据源的页面同样会被后端拒绝启动，提前写入页面锁可让切页后立即显示只读控制栏。
    for related_page in project_plan_page_records(project_plan):
        related_page_id = str(
            related_page.get("pageId") or related_page.get("id") or ""
        ).strip()
        if not related_page_id or related_page_id == page_id:
            continue
        related_references = (
            related_page.get("references")
            if isinstance(related_page.get("references"), dict)
            else {}
        )
        related_dependencies = _dict_items(
            related_references.get("endpoint_dependencies")
            or related_page.get("endpoint_dependencies")
        )
        if any(
            str(item.get("endpoint_id") or "") in related_endpoint_ids
            for item in related_dependencies
        ):
            claims.append(_claim(ExecutionResourceType.PAGE, related_page_id))
    return _deduplicated_claims(claims)


def _data_source_claims(
    project_plan: dict[str, Any],
    source_id: str,
) -> list[ExecutionResourceClaim]:
    """解析数据源本身、所属 API 契约及直接引用这些契约的页面。"""

    claims = [_claim(ExecutionResourceType.DATA_SOURCE, source_id, primary=True)]
    endpoint_ids: set[str] = set()
    for contract in _dict_items(project_plan.get("api_contracts")):
        if contract_data_source_id(project_plan, contract) != source_id:
            continue
        contract_id = str(contract.get("id") or "").strip()
        if contract_id:
            claims.append(_claim(ExecutionResourceType.API_CONTRACT, contract_id))
        endpoint_ids.update(
            str(endpoint.get("id") or "").strip()
            for endpoint in _dict_items(contract.get("endpoints"))
        )
    endpoint_ids.discard("")
    for page in project_plan_page_records(project_plan):
        references = page.get("references") if isinstance(page.get("references"), dict) else {}
        dependencies = _dict_items(
            references.get("endpoint_dependencies") or page.get("endpoint_dependencies")
        )
        if any(str(item.get("endpoint_id") or "") in endpoint_ids for item in dependencies):
            page_id = str(page.get("pageId") or page.get("id") or "").strip()
            if page_id:
                claims.append(_claim(ExecutionResourceType.PAGE, page_id))
    return _deduplicated_claims(claims)


def _endpoint_claims(
    project_plan: dict[str, Any],
    endpoint_id: str,
    api_contract_id: str,
) -> list[ExecutionResourceClaim]:
    """解析 endpoint 本身、所属 API 契约、数据源和直接引用页面。"""

    endpoint_key = (
        f"{api_contract_id}:{endpoint_id}" if api_contract_id else endpoint_id
    )
    claims = [_claim(ExecutionResourceType.ENDPOINT, endpoint_key, primary=True)]
    source_id = ""
    contract_id = api_contract_id
    for contract in _dict_items(project_plan.get("api_contracts")):
        current_contract_id = str(contract.get("id") or "").strip()
        if api_contract_id and current_contract_id != api_contract_id:
            continue
        if not any(
            str(endpoint.get("id") or "").strip() == endpoint_id
            for endpoint in _dict_items(contract.get("endpoints"))
        ):
            continue
        contract_id = current_contract_id
        source_id = contract_data_source_id(project_plan, contract).strip()
        break
    if contract_id:
        claims.append(_claim(ExecutionResourceType.API_CONTRACT, contract_id))
    if source_id:
        claims.append(_claim(ExecutionResourceType.DATA_SOURCE, source_id))
    for page in project_plan_page_records(project_plan):
        references = page.get("references") if isinstance(page.get("references"), dict) else {}
        dependencies = _dict_items(
            references.get("endpoint_dependencies") or page.get("endpoint_dependencies")
        )
        if any(str(item.get("endpoint_id") or "").strip() == endpoint_id for item in dependencies):
            page_id = str(page.get("pageId") or page.get("id") or "").strip()
            if page_id:
                claims.append(_claim(ExecutionResourceType.PAGE, page_id))
    return _deduplicated_claims(claims)


def _claim(
    resource_type: ExecutionResourceType,
    target_id: str,
    *,
    primary: bool = False,
) -> ExecutionResourceClaim:
    """创建具有统一角色和原因的资源声明。"""

    return ExecutionResourceClaim(
        type=resource_type,
        targetId=target_id,
        role=(ExecutionResourceRole.PRIMARY if primary else ExecutionResourceRole.DEPENDENCY),
        reason=(
            ExecutionResourceReason.PRIMARY_TARGET
            if primary
            else ExecutionResourceReason.PLAN_DEPENDENCY
        ),
    )


def _deduplicated_claims(
    claims: list[ExecutionResourceClaim],
) -> list[ExecutionResourceClaim]:
    """按稳定资源键去重，并优先保留主目标声明。"""

    result: dict[str, ExecutionResourceClaim] = {}
    for claim in claims:
        key = f"{claim.type.value}:{claim.target_id}"
        if key not in result or claim.role == ExecutionResourceRole.PRIMARY:
            result[key] = claim
    return list(result.values())


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """过滤外部计划数组中的非对象值。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
