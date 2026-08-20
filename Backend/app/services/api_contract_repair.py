from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.api_schema_refs import normalize_local_schema_ref


def repair_cross_contract_schema_refs(project_plan: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """修复 API 契约中跨契约引用的 Schema。

    大模型生成 ProjectPlan 时，可能把语义上属于契约 A 的 Schema 错放到契约 B，
    导致契约 A 的 Endpoint 引用了未在自身 schemas 中定义的 Schema。

    本函数检测所有「Endpoint 引用了未知 Schema」的错误，若该 Schema 实际定义在
    另一个契约中，则把它移动到引用它的契约内；数据源归属由实体设计独立维护。

    Returns:
        (修复后的 project_plan, 修复动作描述列表)
    """

    plan = deepcopy(project_plan)
    repairs: list[str] = []
    contracts = plan.get("api_contracts")
    if not isinstance(contracts, list):
        return plan, repairs

    # 建立 schema 名 -> 所属 contract 的全局索引
    schema_owner: dict[str, dict[str, Any]] = {}
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        for schema_id in (contract.get("schemas") or {}):
            schema_owner.setdefault(schema_id, contract)

    # 第一轮：把 Endpoint 引用缺失、但存在于其他契约的 Schema 移动过来
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        contract_id = str(contract.get("id") or "")
        schemas = contract.setdefault("schemas", {})
        for endpoint in contract.get("endpoints") or []:
            if not isinstance(endpoint, dict):
                continue
            endpoint_id = str(endpoint.get("id") or "")
            for key in ("request_schema_ref", "response_schema_ref"):
                ref = endpoint.get(key)
                if not ref:
                    continue
                resolved = normalize_local_schema_ref(ref, contract_id=contract_id)
                if resolved in schemas:
                    continue
                owner = schema_owner.get(resolved)
                if owner is None or owner is contract:
                    continue
                owner_id = str(owner.get("id") or "")
                # 把 schema 从拥有者契约移动到当前契约
                schema_def = (owner.get("schemas") or {}).pop(resolved, None)
                if schema_def is None:
                    continue
                schemas[resolved] = schema_def
                repairs.append(
                    f"Moved schema {resolved} from contract {owner_id} to {contract_id} "
                    f"(referenced by endpoint {endpoint_id})."
                )
                # 同步更新全局索引，避免后续重复移动
                schema_owner[resolved] = contract

    return plan, repairs


def validate_and_repair_project_plan(project_plan: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    """先校验，若存在跨契约 Schema 引用问题则自动修复，返回(计划, 修复动作, 剩余错误)。"""

    errors = validate_api_contract_consistency(project_plan)
    if not errors:
        return project_plan, [], []
    repaired, repairs = repair_cross_contract_schema_refs(project_plan)
    if not repairs:
        return project_plan, [], errors
    remaining = validate_api_contract_consistency(repaired)
    return repaired, repairs, remaining
