"""Async Workflow Planning adapter 的正式合同输入投影。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.frozen_contract_store import PlanningFormalInputs


def mainline_formal_contract_inputs(
    formal_artifacts: Mapping[str, Mapping[str, Any]],
    project_plan: Mapping[str, Any],
    build_context: Mapping[str, Any],
) -> PlanningFormalInputs:
    """把已过门禁的正式产物和已解析 API 设计转换为 Frozen Store 输入。"""

    product_plan = formal_artifacts["product_plan"]
    page_contracts = _mapping_items(project_plan.get("page_implementation_contracts"))
    api_contracts = _mapping_items(project_plan.get("api_contracts"))
    endpoint_api_designs = _mapping_items(build_context.get("endpoint_designs"))
    return PlanningFormalInputs(
        product_plan={
            "content": product_plan,
            "source": {
                "artifact": ".xcodeagent/plans/product-plan.json",
                "revision": product_plan.get("version") or "confirmed",
            },
        },
        technical_plan={
            "content": project_plan,
            "source": {
                "artifact": ".xcodeagent/plans/technical-plan.json",
                "revision": project_plan.get("version") or "confirmed",
            },
        },
        page_contracts=[
            {
                "content": contract,
                "source": {
                    "artifact": "runtime-page-contracts",
                    "page_id": contract.get("pageId"),
                },
            }
            for contract in page_contracts
        ],
        api_contracts=[
            {
                "content": contract,
                "source": {
                    "artifact": ".xcodeagent/plans/technical-plan.json",
                    "api_contract_id": contract.get("id"),
                },
            }
            for contract in api_contracts
        ],
        endpoint_api_designs=[
            {
                "content": design,
                "source": {
                    **_endpoint_api_design_source(design),
                },
            }
            for design in endpoint_api_designs
        ],
        authorization_slices=_authorization_slices(project_plan, page_contracts),
    )


def _mapping_items(value: Any) -> list[Mapping[str, Any]]:
    """从未知序列中只保留显式映射，不修复或猜测正式身份。"""

    return [item for item in value or [] if isinstance(item, Mapping)]


def _endpoint_api_design_source(design: Mapping[str, Any]) -> dict[str, str]:
    """为已解析的 Endpoint API Design 构造不依赖磁盘读取的稳定来源身份。"""

    identity = {
        "api_contract_id": design.get("apiContractId"),
        "endpoint_id": design.get("endpointId"),
        "artifact_revision": design.get("artifactRevision"),
    }
    if any(
        not isinstance(value, str) or not value.strip()
        for value in identity.values()
    ):
        raise ValueError(
            "Endpoint API Design 必须包含 apiContractId、endpointId 和 artifactRevision。"
        )
    return {
        "artifact": "confirmed-endpoint-api-design",
        **{key: str(value) for key, value in identity.items()},
    }


def _authorization_slices(
    project_plan: Mapping[str, Any],
    page_contracts: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """按 Page 的正式权限目标构造最小切片，不携带凭据或运行时状态。"""

    manifest = project_plan.get("authorization_manifest")
    if not isinstance(manifest, Mapping) or manifest.get("enabled") is not True:
        return []
    bindings = manifest.get("bindings")
    if not isinstance(bindings, Mapping):
        return []
    pages = _mapping_items(bindings.get("pages"))
    actions = _mapping_items(bindings.get("actions"))
    endpoints = _mapping_items(bindings.get("endpoints"))
    contracts_by_page = {
        str(contract.get("pageId") or ""): contract
        for contract in page_contracts
        if str(contract.get("pageId") or "").strip()
    }
    slices: list[dict[str, Any]] = []
    covered_endpoint_ids: set[str] = set()
    page_ids = sorted(
        {
            str(item.get("pageId"))
            for item in (*pages, *actions)
            if str(item.get("pageId") or "").strip()
        }
    )
    for page_id in page_ids:
        endpoint_ids = {
            str(endpoint_id)
            for endpoint_id in contracts_by_page.get(page_id, {}).get(
                "requiredEndpointIds", []
            )
            if str(endpoint_id).strip()
        }
        covered_endpoint_ids.update(endpoint_ids)
        slices.append(
            {
                "content": {
                    "pageId": page_id,
                    "pages": [item for item in pages if item.get("pageId") == page_id],
                    "actions": [
                        item for item in actions if item.get("pageId") == page_id
                    ],
                    "endpoints": [
                        item
                        for item in endpoints
                        if item.get("endpointId") in endpoint_ids
                    ],
                },
                "source": {
                    "artifact": "authorization-manifest",
                    "page_id": page_id,
                },
            }
        )
    remaining_endpoints = [
        item
        for item in endpoints
        if item.get("endpointId") not in covered_endpoint_ids
    ]
    if remaining_endpoints:
        slices.append(
            {
                "content": {
                    "pageId": "application",
                    "pages": [],
                    "actions": [],
                    "endpoints": remaining_endpoints,
                },
                "source": {
                    "artifact": "authorization-manifest",
                    "scope": "application",
                },
            }
        )
    return slices
