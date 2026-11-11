"""任务规划仅复用配置检查，业务任务每轮按当前上下文重新生成。"""

from collections.abc import Mapping

from app.services.build_task_reuse_contracts import ReuseFacts


def generation_reuse_facts(facts: ReuseFacts) -> ReuseFacts:
    """保留完整基线身份校验，仅让 bootstrap 能力抵扣本轮生成职责。"""
    return ReuseFacts.model_validate({**facts.model_dump(mode="python"),
        "reusable_capabilities_by_unit": {
            unit: capabilities for unit, capabilities in facts.reusable_capabilities_by_unit.items()
            if unit == "backend:bootstrap"
        },
        "retained_endpoint_owners": (),
        # 模板外部能力仍用于验证 shell 等前置条件；权限资源本轮重新物化。
        "external_capabilities": tuple(item for item in facts.external_capabilities
                                       if item.unit_id != "frontend:auth-guard"),
    })


def completed_page_backend_endpoints(workspace, endpoint_keys) -> list[dict[str, str]]:
    """只跳过生命周期中已完成且当前无变更的页面依赖接口，不按整个模块跳过。"""
    from app.services.application_lifecycle import load_application_lifecycle

    lifecycle = load_application_lifecycle(workspace)
    if lifecycle is None:
        return []
    completed = []
    for contract, endpoint in sorted(set(endpoint_keys)):
        progress = lifecycle.development_artifacts.endpoints.get(contract, {}).get(endpoint)
        if (progress is not None and progress.initial_development_status == "completed"
                and progress.change_status == "unchanged"):
            completed.append({"api_contract_id": contract, "endpoint_id": endpoint})
    return completed


def completed_backend_keys(context: Mapping) -> set[tuple[str, str]]:
    """从冻结上下文读取精确的页面后端完成集合，非页面目标不跳过生成。"""
    if context.get("target", {}).get("type") != "page":
        return set()
    return {(item["api_contract_id"], item["endpoint_id"])
            for item in context.get("completed_backend_endpoints", ())}


def completed_backend_units(context: Mapping) -> set[str]:
    """仅当模块在本页引用的接口全部完成时，允许该模块没有本轮任务。"""
    from app.services.unit_task_manifest import backend_module_unit_id

    designs = {(item["apiContractId"], item["endpointId"])
               for item in context.get("endpoint_designs", ())}
    completed = completed_backend_keys(context)
    return {backend_module_unit_id(contract) for contract, _ in completed
            if {key for key in designs if key[0] == contract} <= completed}
