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
    另一个契约中，则把它移动到引用它的契约内，并同步修正数据源 schema_refs。

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

    if not repairs:
        return plan, repairs

    # 第二轮：修正数据源 schema_refs，使其指向移动后的契约
    _repair_data_source_schema_refs(plan, contracts, repairs)

    return plan, repairs


def _repair_data_source_schema_refs(
    plan: dict[str, Any],
    contracts: list[dict[str, Any]],
    repairs: list[str],
) -> None:
    """重建每个数据源的 schema_refs，只保留指向本契约内真实存在的 Schema 的引用。"""

    # contract_id -> set(schema_id)
    contract_schemas: dict[str, set[str]] = {}
    # data_source_id -> contract
    source_contract: dict[str, dict[str, Any]] = {}
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        cid = str(contract.get("id") or "")
        contract_schemas[cid] = set((contract.get("schemas") or {}).keys())
        dsid = str(contract.get("data_source_id") or "")
        if dsid:
            source_contract[dsid] = contract

    repaired_source_ids: set[str] = set()
    for entity in plan.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        data_source = entity.get("data_source")
        if not isinstance(data_source, dict):
            # 类型化数据源下实体 data_source 只是类型字符串，无源级 schema_refs 可修复。
            continue
        dsid = str(data_source.get("id") or "")
        if not dsid or dsid in repaired_source_ids:
            continue
        contract = source_contract.get(dsid)
        if contract is None:
            continue
        cid = str(contract.get("id") or "")
        valid = contract_schemas.get(cid, set())
        refs = data_source.get("schema_refs")
        if not isinstance(refs, list):
            continue
        new_refs: list[str] = []
        changed = False
        for ref in refs:
            ref_str = str(ref)
            # 解析裸引用（#/schemas/X）与带契约前缀的引用（cid#/schemas/X）
            if "#/schemas/" in ref_str:
                ref_contract, _, schema_id = ref_str.partition("#/schemas/")
            else:
                ref_contract, schema_id = "", ref_str
            # 归属本契约且真实存在的 schema，统一写成完整引用
            if schema_id in valid and (not ref_contract or ref_contract == cid):
                full = f"{cid}#/schemas/{schema_id}"
                if full not in new_refs:
                    new_refs.append(full)
                if ref_str != full:
                    changed = True
            else:
                changed = True  # 丢弃指向其他契约或不存在的引用
        if changed:
            data_source["schema_refs"] = new_refs
            repaired_source_ids.add(dsid)
            repairs.append(f"Rebuilt schema_refs for data source {dsid}.")


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
